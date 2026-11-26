"""ZIP 批量下载：流式生成（M4.4）。

内存纪律（PLAN §2.1.1-2 禁止整文件读入内存）：
- 生成器驱动——zipfile 在事件循环内逐块写入内部管道，每个 MinIO 块（1MiB）压缩后
  立即交给 StreamingResponse 发送；客户端慢 → 生成器被挂起 → 读取自动停止，
  内存驻留恒 ≤ 单块；无 Zip64 之外的额外磁盘/内存开销。
- 每个条目预校验（存在/NORMAL/成员可读），任一失败整个请求 404/403（不发半截包）；
- 上限配置化：条目数 zip_max_entries、总未压缩大小 zip_max_total_bytes。
"""
import io
import logging
import zipfile
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.errors import BizError, ErrorCode
from app.models.file_version import FileVersion
from app.models.space import ROLE_VIEWER
from app.models.storage_object import OBJECT_STATUS_ACTIVE, StorageObject
from app.models.user import User
from app.models.user_file import FILE_STATUS_NORMAL, UserFile
from app.services.permission import ensure_role

logger = logging.getLogger(__name__)


@dataclass
class ZipEntry:
    file_id: int
    arcname: str
    size: int
    bucket: str
    object_key: str
    sha256: str


@dataclass
class ZipPlan:
    entries: list[ZipEntry] = field(default_factory=list)
    total_bytes: int = 0


class _PipeWriter(io.RawIOBase):
    """zipfile 的内存汇聚管道：write 只进缓冲，drain 由生成器主动取走。"""

    def __init__(self) -> None:
        self.buf = bytearray()

    def writable(self) -> bool:  # noqa: D102
        return True

    def write(self, b) -> int:  # noqa: D102
        self.buf += b
        return len(b)

    def drain(self) -> bytes:
        data = bytes(self.buf)
        self.buf.clear()
        return data

    def close(self) -> None:  # zipfile 关闭时调用；不清理缓冲（外层还要 drain 中央目录）
        pass


async def build_plan(db: AsyncSession, user: User, file_ids: list[int]) -> ZipPlan:
    """预校验全部条目并生成打包计划；任一不可访问 → 整体拒绝（不产半截包）。"""
    settings = get_settings()
    if not file_ids:
        raise BizError(ErrorCode.BAD_REQUEST, message="未选择文件")
    if len(file_ids) > settings.zip_max_entries:
        raise BizError(ErrorCode.BAD_REQUEST,
                       message=f"一次最多打包 {settings.zip_max_entries} 个文件")
    plan = ZipPlan()
    seen_names: set[str] = set()
    for fid in file_ids:
        row = await db.get(UserFile, fid)
        if row is None or row.is_folder or row.status != FILE_STATUS_NORMAL:
            raise BizError(ErrorCode.NOT_FOUND, data={"id": fid})
        await ensure_role(db, user.id, row.space_id, ROLE_VIEWER)
        version = await db.get(FileVersion, row.current_version_id) if row.current_version_id else None
        if version is None:
            raise BizError(ErrorCode.INTERNAL, message=f"「{row.name}」版本缺失", http_status=500)
        obj = await db.get(StorageObject, version.storage_object_id)
        if obj is None or obj.status != OBJECT_STATUS_ACTIVE:
            raise BizError(ErrorCode.INTERNAL, message=f"「{row.name}」内容缺失", http_status=500)

        arcname = row.name
        n = 1
        while arcname in seen_names:  # 不同目录同名 → 加序号保证包内唯一
            arcname = f"{n}_{row.name}"
            n += 1
        seen_names.add(arcname)
        plan.entries.append(ZipEntry(
            file_id=row.id, arcname=arcname, size=int(obj.size),
            bucket=obj.bucket, object_key=obj.object_key, sha256=obj.sha256,
        ))
        plan.total_bytes += int(obj.size)
        if plan.total_bytes > settings.zip_max_total_bytes:
            raise BizError(ErrorCode.FILE_TOO_LARGE,
                           data={"total_bytes": plan.total_bytes, "limit": settings.zip_max_total_bytes})
    return plan


async def zip_stream(storage, plan: ZipPlan):
    """异步生成器：产出 zip 字节块（StreamingResponse 直接消费）。"""
    pipe = _PipeWriter()
    # 条目数不大（≤100）但单条可能大——逐块压缩逐块 yield
    with zipfile.ZipFile(pipe, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
        for entry in plan.entries:
            data = pipe.drain()
            if data:
                yield data
            with zf.open(entry.arcname, "w", force_zip64=True) as out:
                async for chunk in storage.get_range(entry.bucket, entry.object_key):
                    out.write(chunk)
                    data = pipe.drain()
                    if data:
                        yield data
            data = pipe.drain()
            if data:
                yield data
            # 注意：extra 不能用保留属性 name（LogRecord 冲突会让流中途崩溃）
            logger.debug("zip entry packed", extra={"entry_name": entry.arcname, "entry_size": entry.size})
    data = pipe.drain()  # 中央目录 + EOF
    if data:
        yield data
