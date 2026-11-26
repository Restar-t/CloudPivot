"""文件服务：单请求流式上传 / Range 下载 / 删除进回收站（M3 核心，三层数据模型首航）。

上传事务顺序（PLAN §2.3-4 提交语义的 M3 简化版）：
  流式收字节的临时文件 + 增量 SHA-256 → MinIO 写入（UUID key，字节数服务端强制）
  → 单事务：配额校验加锁累加 → 对象按 (sha256,size) 查找复用或登记 → 版本 v1
  → 逻辑文件 → operation_log。任一步失败：MinIO 对象成为孤儿（M7 GC 回收），DB 无变更。
"""
import hashlib
import logging
import tempfile
import uuid
from dataclasses import dataclass
from pathlib import Path

from fastapi import UploadFile
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.errors import BizError, ErrorCode
from app.core.filename import validate_filename
from app.core.logging import space_id_var
from app.core.time import utcnow
from app.models.operation_log import ACTION_DELETE, ACTION_UPLOAD, OperationLog
from app.models.space import ROLE_EDITOR, ROLE_VIEWER, Space
from app.models.storage_object import OBJECT_STATUS_ACTIVE, StorageObject
from app.models.user import User
from app.models.user_file import FILE_STATUS_NORMAL, FILE_STATUS_TRASH, ROOT_PARENT_ID, UserFile
from app.schemas.file import FileItemOut
from app.services.permission import ensure_role, get_personal_space
from app.services.quota_service import check_and_add_usage, get_space_quota_limit
from app.storage.minio_client import MinioStorage

logger = logging.getLogger(__name__)

_READ_CHUNK = 1024 * 1024  # 1MiB


@dataclass
class UploadResult:
    file: FileItemOut
    version_no: int
    storage_object_id: int
    sha256: str
    size: int


def _check_extension(filename: str) -> None:
    settings = get_settings()
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    if ext not in settings.upload_type_whitelist_set:
        raise BizError(ErrorCode.TYPE_NOT_ALLOWED, data={"extension": ext, "allowed": sorted(settings.upload_type_whitelist_set)})


async def _resolve_parent(db: AsyncSession, user: User, parent_id: int) -> tuple[Space, UserFile | None]:
    if parent_id == ROOT_PARENT_ID:
        space = await get_personal_space(db, user.id)
        space_id_var.set(space.id)
        return space, None
    parent = await db.get(UserFile, parent_id)
    if parent is None or not parent.is_folder or parent.status != FILE_STATUS_NORMAL:
        raise BizError(ErrorCode.NOT_FOUND)
    space = await db.get(Space, parent.space_id)
    space_id_var.set(parent.space_id)
    return space, parent


class _HashingReader:
    """流式读取 UploadFile → 临时文件；增量 SHA-256；字节数服务端强制上限。"""

    def __init__(self, upload: UploadFile, max_bytes: int):
        self.upload = upload
        self.max_bytes = max_bytes
        self.size = 0
        self.sha256 = hashlib.sha256()

    async def drain(self) -> Path:
        tmp_path = Path(tempfile.gettempdir()) / f"nd-upload-{uuid.uuid4().hex}.part"
        try:
            with open(tmp_path, "wb") as out:
                while chunk := await self.upload.read(_READ_CHUNK):
                    self.size += len(chunk)
                    if self.size > self.max_bytes:
                        raise BizError(ErrorCode.FILE_TOO_LARGE, data={"max_bytes": self.max_bytes})
                    self.sha256.update(chunk)
                    out.write(chunk)
            if self.size == 0:
                raise BizError(ErrorCode.BAD_REQUEST, message="不能上传空文件")
            return tmp_path
        except BaseException:
            tmp_path.unlink(missing_ok=True)
            raise


async def upload_file(
    db: AsyncSession,
    storage: MinioStorage,
    user: User,
    parent_id: int,
    upload: UploadFile,
) -> UploadResult:
    settings = get_settings()
    validate_filename(upload.filename or "")
    _check_extension(upload.filename)

    space, parent = await _resolve_parent(db, user, parent_id)
    await ensure_role(db, user.id, space.id, ROLE_EDITOR)
    if parent is not None and parent.status != FILE_STATUS_NORMAL:
        raise BizError(ErrorCode.NOT_FOUND)
    # 同目录活跃重名预检（唯一键兜底并发窗口）
    dup = await db.scalar(
        select(UserFile.id).where(
            UserFile.space_id == space.id,
            UserFile.parent_id == parent_id,
            UserFile.name == upload.filename,
            UserFile.status == FILE_STATUS_NORMAL,
        )
    )
    if dup:
        raise BizError(ErrorCode.NAME_CONFLICT)

    # ① 流式收字节（服务端强制大小上限 + 增量哈希）
    reader = _HashingReader(upload, settings.upload_max_file_size)
    tmp_path = await reader.drain()

    # ② MinIO 写入（formal 桶，UUID key）
    object_key = uuid.uuid4().hex
    sha256 = reader.sha256.hexdigest()
    size = reader.size
    try:
        with open(tmp_path, "rb") as fh:
            await storage.put_stream(storage.bucket_formal, object_key, fh, size)
    finally:
        tmp_path.unlink(missing_ok=True)

    # ③ 单事务提交元数据
    try:
        limit = await get_space_quota_limit(db, space)
        await check_and_add_usage(db, space.id, limit, size)

        from app.services.object_service import resolve_or_register

        # 物理去重统一入口：ACTIVE 复用（ref++）/ DELETED·DELETING 复活 / 新登记
        obj, reused = await resolve_or_register(db, sha256, size, storage.bucket_formal, object_key)
        new_object = not reused

        file = UserFile(
            space_id=space.id, parent_id=parent_id, name=upload.filename,
            is_folder=False, status=FILE_STATUS_NORMAL, alive=1,
            size=size, generation=0,
        )
        db.add(file)
        await db.flush()

        from app.models.file_version import FileVersion

        version = FileVersion(
            file_id=file.id, version_no=1, storage_object_id=obj.id,
            size=size, created_by=user.id,
        )
        db.add(version)
        await db.flush()
        file.current_version_id = version.id

        db.add(OperationLog(
            user_id=user.id, action=ACTION_UPLOAD, target_type="file", target_id=file.id,
            detail=f"name={upload.filename} size={size} version_no=1 object={obj.id} reused={not new_object}",
        ))
        await db.commit()
    except BizError:
        raise  # 配额 5xxx 等业务错误：DB 已回滚；MinIO 侧孤儿对象由 M7 GC 清理
    except Exception:
        await db.rollback()
        raise

    await db.refresh(file)
    logger.info(
        "file uploaded",
        extra={"action": ACTION_UPLOAD, "target": upload.filename, "file_id": file.id,
               "size": size, "object_reused": not new_object, "user_id": user.id},
    )
    return UploadResult(
        file=FileItemOut.model_validate(file), version_no=1,
        storage_object_id=obj.id, sha256=sha256, size=size,
    )


# ---------- 下载 ----------
def parse_range_header(range_header: str, size: int) -> tuple[int, int] | None | str:
    """解析单段 Range（RFC 7233）。

    返回 (start, length) / None（语法非法或多段 → 忽略，按 200 全量）/ "invalid"
    （语法合法但起点越界 → 416）。
    """
    import re

    m = re.fullmatch(r"bytes=(\d*)-(\d*)", range_header.strip())
    if not m:
        return None  # 语法非法（含多段、非 bytes 单位）→ 忽略
    start_s, end_s = m.group(1), m.group(2)
    if start_s == "" and end_s == "":
        return None
    if start_s == "":  # suffix: bytes=-N
        n = int(end_s)
        if n == 0 or size == 0:
            return None
        length = min(n, size)
        return (size - length, length)
    start = int(start_s)
    if start >= size:
        return "invalid"  # 语法合法但越界 → 416
    if end_s == "":
        return (start, size - start)
    end = int(end_s)
    if end < start:
        return None
    return (start, min(end, size - 1) - start + 1)


async def download_file(
    db: AsyncSession, storage: MinioStorage, user: User, file_id: int
):
    """鉴权并返回 (file, obj, version, etag)；字节流由路由层包装为 StreamingResponse。"""
    file = await db.get(UserFile, file_id)
    if file is None or file.is_folder or file.status != FILE_STATUS_NORMAL:
        raise BizError(ErrorCode.NOT_FOUND)
    await ensure_role(db, user.id, file.space_id, ROLE_VIEWER)

    if file.current_version_id is None:
        raise BizError(ErrorCode.NOT_FOUND)
    from app.models.file_version import FileVersion

    version = await db.get(FileVersion, file.current_version_id)
    if version is None:
        raise BizError(ErrorCode.INTERNAL, message="当前版本缺失", http_status=500)
    obj = await db.get(StorageObject, version.storage_object_id)
    if obj is None or obj.status != OBJECT_STATUS_ACTIVE:
        # 已引用对象缺失属严重不一致（M7 对账告警），此处不掩盖
        raise BizError(ErrorCode.INTERNAL, message="文件内容缺失，请联系管理员", http_status=500)

    etag = f'"{obj.sha256}"'
    return file, obj, version, etag


async def iter_object(storage: MinioStorage, obj: StorageObject, offset: int, length: int):
    async for chunk in storage.get_range(obj.bucket, obj.object_key, offset=offset, length=length):
        yield chunk


def content_disposition(filename: str) -> str:
    """RFC 5987：中文文件名兼容。一律 attachment（M5 预览才允许 inline 白名单）。"""
    from urllib.parse import quote

    ascii_name = filename.encode("ascii", "ignore").decode("ascii").replace('"', "") or "download"
    return f"attachment; filename=\"{ascii_name}\"; filename*=UTF-8''{quote(filename)}"


# ---------- 删除（进回收站） ----------
async def trash_file(db: AsyncSession, user: User, file_id: int) -> dict:
    file = await db.get(UserFile, file_id)
    if file is None or file.status != FILE_STATUS_NORMAL:
        raise BizError(ErrorCode.NOT_FOUND)
    await ensure_role(db, user.id, file.space_id, ROLE_EDITOR)

    now = utcnow()
    count = 0
    # 目录 → 迭代 BFS 标记整棵子树（可移植：sqlite/mysql 通用；深度防御 count 上限）
    queue = [file.id]
    while queue and count < 100000:
        fid = queue.pop()
        row = await db.get(UserFile, fid)
        if row is None or row.status != FILE_STATUS_NORMAL:
            continue
        row.status = FILE_STATUS_TRASH
        row.deleted_at = now
        row.generation += 1
        row.alive = None
        count += 1
        children = (
            await db.execute(
                select(UserFile.id).where(
                    UserFile.parent_id == fid, UserFile.status == FILE_STATUS_NORMAL
                )
            )
        ).scalars().all()
        queue.extend(children)

    db.add(OperationLog(
        user_id=user.id, action=ACTION_DELETE, target_type="file" if not file.is_folder else "folder",
        target_id=file.id, detail=f"name={file.name} trashed_rows={count}",
    ))
    await db.commit()
    logger.info(
        "file trashed",
        extra={"action": ACTION_DELETE, "target": file.name, "file_id": file.id,
               "rows": count, "user_id": user.id},
    )
    # 回收站占配额：用量不变
    return {"trashed": count, "file_id": file.id, "name": file.name}
