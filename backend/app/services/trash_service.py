"""回收站服务：列表 / 还原 / 彻底删除（引用释放）/ 清空。

口径（PLAN §2.2/§2.4）：
- 回收站占配额；30 天保留以 deleted_at 计（清理任务 M7 接管，此处仅展示剩余天数）；
- 还原：目录连同整棵子树一起还原；原父目录已不在（被删/仍在回收站）→ 还原到空间根；
  目标位置重名 → 409 并给建议名，绝不静默覆盖；
- 彻底删除（引用释放的事实来源是 file_version 行）：删版本行 → ref_count-- →
  归零的对象从 MinIO 物理删除并删除对象行；user_file 行保留（status=DELETED）作 lineage；
- 清空 = 逐个彻底删除"顶层"回收项（父链上无其他 TRASH 项），目录子树由目录项负责，
  避免双重释放。
"""
import logging

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.errors import BizError, ErrorCode
from app.core.logging import space_id_var
from app.core.time import utcnow
from app.models.file_version import FileVersion
from app.models.operation_log import ACTION_DELETE, OperationLog
from app.models.space import ROLE_EDITOR, ROLE_VIEWER, Space
from app.models.storage_object import StorageObject
from app.models.user import User
from app.models.user_file import FILE_STATUS_DELETED, FILE_STATUS_NORMAL, FILE_STATUS_TRASH, UserFile
from app.services.permission import ensure_role, get_personal_space
from app.services.quota_service import add_usage

logger = logging.getLogger(__name__)

_MAX_TREE_DEPTH = 256


# ---------- 列表 ----------
async def list_trash(
    db: AsyncSession, user: User, page: int = 1, page_size: int = 50
) -> tuple[list[dict], int]:
    space = await get_personal_space(db, user.id)
    await ensure_role(db, user.id, space.id, ROLE_VIEWER)
    space_id_var.set(space.id)

    base = select(UserFile).where(
        UserFile.space_id == space.id, UserFile.status == FILE_STATUS_TRASH
    )
    total = await db.scalar(
        select(func.count()).select_from(UserFile).where(
            UserFile.space_id == space.id, UserFile.status == FILE_STATUS_TRASH
        )
    )
    rows = (
        await db.execute(base.order_by(UserFile.deleted_at.desc(), UserFile.id.desc())
                         .offset((page - 1) * page_size).limit(page_size))
    ).scalars().all()

    retention = get_settings().trash_retention_days
    now = utcnow()
    items = []
    for r in rows:
        days_left = max(0, retention - (now - r.deleted_at).days) if r.deleted_at else 0
        items.append({
            "id": r.id, "name": r.name, "is_folder": r.is_folder,
            "size": 0 if r.is_folder else r.size,
            "deleted_at": r.deleted_at, "remaining_days": days_left,
            "parent_id": r.parent_id, "original_path": await _original_path(db, r),
        })
    return items, int(total or 0)


async def _original_path(db: AsyncSession, row: UserFile) -> str:
    """原位置展示串（父链可能同样在回收站——按链路取名，不看 status）。"""
    names: list[str] = []
    cur = row.parent_id
    for _ in range(_MAX_TREE_DEPTH):
        if cur == 0:
            break
        parent = await db.get(UserFile, cur)
        if parent is None:
            break
        names.insert(0, parent.name)
        cur = parent.parent_id
    return "/" + "/".join(names) if names else "/（根目录）"


# ---------- 还原 ----------
def _suggest_name(base_name: str) -> str:
    stem, dot, ext = base_name.rpartition(".")
    if not dot:
        return f"{base_name}（1）"
    return f"{stem}（1）.{ext}"


async def _first_free_name(db: AsyncSession, space_id: int, parent_id: int, name: str) -> str | None:
    """目标目录里第一个可用名（复用 folder_service 的共享实现）。"""
    from app.services.folder_service import first_free_name

    return await first_free_name(db, space_id, parent_id, name)


async def restore(db: AsyncSession, user: User, file_id: int) -> dict:
    row = await db.get(UserFile, file_id)
    if row is None or row.status != FILE_STATUS_TRASH:
        raise BizError(ErrorCode.NOT_FOUND)
    await ensure_role(db, user.id, row.space_id, ROLE_EDITOR)

    # 目标父目录：原父目录仍是 NORMAL 才回原位，否则回根
    target_parent = row.parent_id
    if target_parent != 0:
        parent = await db.get(UserFile, target_parent)
        if parent is None or parent.status != FILE_STATUS_NORMAL or not parent.is_folder:
            target_parent = 0

    final_name = await _first_free_name(db, row.space_id, target_parent, row.name)
    if final_name is not None:
        # 原名被占 → 409 + 建议名（由用户决定，不静默覆盖）
        raise BizError(ErrorCode.NAME_CONFLICT, data={"suggested_name": final_name})

    now = utcnow()
    restored = await _restore_subtree(db, row, now)
    db.add(OperationLog(
        user_id=user.id, action="restore", target_type="folder" if row.is_folder else "file",
        target_id=row.id, detail=f"name={row.name} rows={restored} to_parent={target_parent}",
    ))
    await db.commit()
    logger.info("trash restored", extra={"action": "restore", "file_id": row.id, "rows": restored})
    return {"id": row.id, "name": row.name, "parent_id": target_parent, "restored_rows": restored}


async def _restore_subtree(db: AsyncSession, root: UserFile, now) -> int:
    """整棵子树还原：根恢复到指定父目录，子代一律原位复活。"""
    count = 0
    # 子女链：TRASH 且 parent 指向子树内已还原节点
    root.status = FILE_STATUS_NORMAL
    root.deleted_at = None
    root.alive = 1
    root.generation += 1
    count += 1

    frontier = [root.id]
    while frontier and count < 100000:
        children = (await db.execute(
            select(UserFile).where(
                UserFile.parent_id.in_(frontier), UserFile.status == FILE_STATUS_TRASH
            )
        )).scalars().all()
        frontier = []
        for child in children:
            child.status = FILE_STATUS_NORMAL
            child.deleted_at = None
            child.alive = 1
            child.generation += 1
            count += 1
            frontier.append(child.id)
    return count


# ---------- 彻底删除 ----------
async def purge(db: AsyncSession, storage, user: User, file_id: int) -> dict:
    """彻底删除单个回收项（目录含整棵子树）；幂等：非 TRASH → 404。"""
    row = await db.get(UserFile, file_id)
    if row is None or row.status != FILE_STATUS_TRASH:
        raise BizError(ErrorCode.NOT_FOUND)
    await ensure_role(db, user.id, row.space_id, ROLE_EDITOR)
    space_id_var.set(row.space_id)

    released = await _release_tree(db, storage, row)
    db.add(OperationLog(
        user_id=user.id, action=ACTION_DELETE, target_type="purge",
        target_id=row.id, detail=f"name={row.name} files={released['files']} bytes={released['bytes']} objects_purged={len(released['purged_objects'])}",
    ))
    await db.commit()

    # 事务提交后再物理删 MinIO；成功 → 行转 DELETED（保留）；失败 → 留 DELETING 由对账重试
    for bucket, key in released["purged_objects"]:
        try:
            await storage.delete(bucket, key)
        except Exception as exc:  # noqa: BLE001 — 对账循环重试
            logger.error("minio purge failed", extra={"bucket": bucket, "key": key, "error": str(exc)})
            continue
        from app.models.storage_object import OBJECT_STATUS_DELETED, OBJECT_STATUS_DELETING

        dead = (await db.execute(
            select(StorageObject).where(
                StorageObject.bucket == bucket, StorageObject.object_key == key,
                StorageObject.status == OBJECT_STATUS_DELETING,
            )
        )).scalars().all()
        for obj_row in dead:
            obj_row.status = OBJECT_STATUS_DELETED
    await db.commit()

    logger.info("trash purged", extra={"file_id": file_id, **{k: v for k, v in released.items() if k != 'purged_objects'}})
    return {"purged": True, "files": released["files"], "bytes_released": released["bytes"]}


async def clear(db: AsyncSession, storage, user: User) -> dict:
    """清空回收站：只清"顶层"回收项（父链无 TRASH），子树由其目录项释放。"""
    space = await get_personal_space(db, user.id)
    await ensure_role(db, user.id, space.id, ROLE_EDITOR)
    space_id_var.set(space.id)

    roots = (await db.execute(
        select(UserFile).where(UserFile.space_id == space.id, UserFile.status == FILE_STATUS_TRASH)
    )).scalars().all()
    top_ids = []
    for r in roots:
        if not await _has_trash_ancestor(db, r):
            top_ids.append(r.id)

    total_files, total_bytes = 0, 0
    purged_objects: list[tuple[str, str]] = []
    for fid in top_ids:
        released = await _release_tree(db, storage, await db.get(UserFile, fid))
        total_files += released["files"]
        total_bytes += released["bytes"]
        purged_objects.extend(released["purged_objects"])

    db.add(OperationLog(
        user_id=user.id, action=ACTION_DELETE, target_type="purge_all",
        target_id=None, detail=f"roots={len(top_ids)} files={total_files} bytes={total_bytes}",
    ))
    await db.commit()
    for bucket, key in purged_objects:
        try:
            await storage.delete(bucket, key)
        except Exception as exc:  # noqa: BLE001
            logger.error("minio purge failed", extra={"bucket": bucket, "key": key, "error": str(exc)})
            continue
        from sqlalchemy import select as _sel
        from app.models.storage_object import OBJECT_STATUS_DELETING as _DEL

        dead = (await db.execute(_sel(StorageObject).where(
            _sel(StorageObject.bucket) == bucket, _sel(StorageObject.object_key) == key,
            _sel(StorageObject.status) == _DEL,
        ))).scalars().all()
        for obj_row in dead:
            obj_row.status = "DELETED"
    await db.commit()
    return {"roots": len(top_ids), "files": total_files, "bytes_released": total_bytes}


async def _has_trash_ancestor(db: AsyncSession, row: UserFile) -> bool:
    cur = row.parent_id
    for _ in range(_MAX_TREE_DEPTH):
        if cur == 0:
            return False
        parent = await db.get(UserFile, cur)
        if parent is None:
            return False
        if parent.status == FILE_STATUS_TRASH:
            return True
        cur = parent.parent_id
    return False


async def _release_tree(db: AsyncSession, storage, root: UserFile) -> dict:
    """释放整棵子树的引用：删版本行、ref_count--、零引用对象行删除；user_file 行置 DELETED。"""
    files = bytes_released = 0
    purged_objects: list[tuple[str, str]] = []
    obj_cache: dict[int, StorageObject | None] = {}

    ids = await _subtree_ids(db, root)
    now = utcnow()
    for fid in ids:
        row = await db.get(UserFile, fid)
        if row is None:
            continue
        if not row.is_folder:
            versions = (await db.execute(
                select(FileVersion).where(FileVersion.file_id == fid)
            )).scalars().all()
            for v in versions:
                bytes_released += v.size
                if v.storage_object_id not in obj_cache:
                    obj_cache[v.storage_object_id] = await db.get(StorageObject, v.storage_object_id)
                obj = obj_cache[v.storage_object_id]
                if obj is None:
                    continue
                obj.ref_count -= 1
                if obj.ref_count <= 0:
                    # 两阶段：行置 DELETING（阻止新引用），物理删除在事务提交后执行，
                    # 成功后行转 DELETED 保留（审计/复活语义，M7.4）
                    from app.models.storage_object import OBJECT_STATUS_DELETING

                    obj.status = OBJECT_STATUS_DELETING
                    purged_objects.append((obj.bucket, obj.object_key))
            files += 1
        # 版本行随文件删除（硬删）；user_file 行保留为 DELETED lineage
        await db.execute(
            FileVersion.__table__.delete().where(FileVersion.file_id == fid)
        )
        row.status = FILE_STATUS_DELETED
        row.deleted_at = row.deleted_at or now
        row.alive = None

    # 配额释放（回收站占配额 → 彻底删除释放）
    if bytes_released:
        await add_usage(db, root.space_id, -bytes_released)
    return {"files": files, "bytes": bytes_released, "purged_objects": purged_objects}


async def _subtree_ids(db: AsyncSession, root: UserFile) -> list[int]:
    ids = [root.id]
    frontier = [root.id]
    while frontier and len(ids) < 100000:
        children = (await db.execute(
            select(UserFile.id).where(UserFile.parent_id.in_(frontier))
        )).scalars().all()
        ids.extend(children)
        frontier = children
    return ids
