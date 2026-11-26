"""版本管理服务（M7.1，T05）。

- 列表：全部保留版本（version_no/大小/时间/当前标记）+ 总占用；
- 恢复：指针切换 + generation+1，不新增版本、不增用量（PLAN §2.2）；
- 删指定版本：当前版本禁删（409）、最后一个保留版本禁删（409）；
  释放 = 删版本行 + ref-- + 配额减 + 零引用对象走两阶段删除（M7.4）。
"""
import logging

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import BizError, ErrorCode
from app.core.logging import space_id_var
from app.core.time import utcnow
from app.models.file_version import FileVersion
from app.models.operation_log import ACTION_DELETE, OperationLog
from app.models.quota_usage import QuotaUsage
from app.models.space import ROLE_EDITOR, ROLE_VIEWER
from app.models.storage_object import StorageObject
from app.models.user import User
from app.models.user_file import FILE_STATUS_NORMAL, UserFile
from app.services.object_service import mark_deleted, release_object
from app.services.permission import ensure_role

logger = logging.getLogger(__name__)


async def _load_file(db: AsyncSession, user: User, file_id: int, min_role: str) -> UserFile:
    file = await db.get(UserFile, file_id)
    if file is None or file.is_folder or file.status != FILE_STATUS_NORMAL:
        raise BizError(ErrorCode.NOT_FOUND)
    await ensure_role(db, user.id, file.space_id, min_role)
    space_id_var.set(file.space_id)
    return file


async def list_versions(db: AsyncSession, user: User, file_id: int) -> dict:
    file = await _load_file(db, user, file_id, ROLE_VIEWER)
    rows = (await db.execute(
        select(FileVersion).where(FileVersion.file_id == file_id).order_by(FileVersion.version_no)
    )).scalars().all()
    total_size = sum(r.size for r in rows)
    return {
        "file_id": file_id, "file_name": file.name,
        "current_version_id": file.current_version_id,
        "total_size": total_size,
        "items": [
            {"version_no": r.version_no, "version_id": r.id, "size": r.size,
             "created_at": r.created_at, "created_by": r.created_by,
             "current": r.id == file.current_version_id}
            for r in rows
        ],
    }


async def restore_version(db: AsyncSession, user: User, file_id: int, version_no: int) -> dict:
    file = await _load_file(db, user, file_id, ROLE_EDITOR)
    version = (
        await db.execute(
            select(FileVersion).where(FileVersion.file_id == file_id, FileVersion.version_no == version_no)
        )
    ).scalar_one_or_none()
    if version is None:
        raise BizError(ErrorCode.NOT_FOUND)
    if version.id == file.current_version_id:
        return {"restored": True, "already_current": True, "version_no": version_no}  # 幂等

    file.current_version_id = version.id
    file.size = version.size
    file.generation += 1  # 切指针也 bump：进行中的同名上传任务完成校验失败（M6 依赖）
    db.add(OperationLog(
        user_id=user.id, action="restore_version", target_type="file", target_id=file_id,
        detail=f"to_version_no={version_no}",
    ))
    await db.commit()
    logger.info(
        "version restored",
        extra={"file_id": file_id, "version_no": version_no, "user_id": user.id},
    )
    # 恢复不增用量（指针切换，版本集合不变）
    return {"restored": True, "already_current": False, "version_no": version_no,
            "generation": file.generation}


async def delete_version(db: AsyncSession, storage, user: User, file_id: int, version_no: int) -> dict:
    file = await _load_file(db, user, file_id, ROLE_EDITOR)
    version = (
        await db.execute(
            select(FileVersion).where(FileVersion.file_id == file_id, FileVersion.version_no == version_no)
        )
    ).scalar_one_or_none()
    if version is None:
        raise BizError(ErrorCode.NOT_FOUND)
    if version.id == file.current_version_id:
        raise BizError(ErrorCode.STATE_CONFLICT, message="当前版本禁止直接删除（先恢复其他版本）")
    remaining = await db.scalar(
        select(func.count()).select_from(FileVersion).where(FileVersion.file_id == file_id)
    )
    if int(remaining or 0) <= 1:
        raise BizError(ErrorCode.STATE_CONFLICT, message="最后一个保留版本禁止删除")

    obj = await db.get(StorageObject, version.storage_object_id)
    await db.delete(version)
    pending: tuple[str, str] | None = None
    if obj is not None:
        pending = await release_object(db, obj)  # ref--；零引用 → DELETING
    await db.execute(
        QuotaUsage.__table__.update()
        .where(QuotaUsage.space_id == file.space_id)
        .values(used_bytes=QuotaUsage.used_bytes - version.size)
    )
    db.add(OperationLog(
        user_id=user.id, action=ACTION_DELETE, target_type="version",
        target_id=version.id, detail=f"file={file_id} version_no={version_no} size={version.size}",
    ))
    await db.commit()

    # 两阶段第二段：物理删 MinIO → 行转 DELETED（保留行；失败留 DELETING 由对账重试）
    if pending:
        try:
            await storage.delete(pending[0], pending[1])
        except Exception as exc:  # noqa: BLE001 — 对账循环重试
            logger.error("version object delete failed", extra={"key": pending[1], "error": str(exc)})
        else:
            obj = await db.get(StorageObject, version.storage_object_id)
            if obj is not None and obj.status == "DELETING":
                await mark_deleted(db, obj)

    logger.info(
        "version deleted",
        extra={"file_id": file_id, "version_no": version_no, "size": version.size, "user_id": user.id},
    )
    return {"deleted": True, "version_no": version_no, "released_bytes": version.size}
