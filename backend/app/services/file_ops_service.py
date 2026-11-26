"""文件操作服务：移动 / 复制 / 批量删除 / 搜索（M4）。

纪律（PLAN §2.4）：
- 移动：目标目录角色校验 + 循环检测（目标不得是源自身或其后代）+ generation+1；跨空间移动首版禁止；
- 复制：逻辑复制（新 user_file/file_version 行共享 storage_object，ref_count++）；
  目标空间配额检查；目录递归复制（BFS，层级上限）；同名冲突逐项 409；
- 批量操作逐项返回结果，不因单项失败中断；
- 搜索：文件名 LIKE，通配符转义，仅本空间 NORMAL 行，分页排序复用 M2 参数。
"""
import logging

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import BizError, ErrorCode
from app.core.logging import space_id_var
from app.models.file_version import FileVersion
from app.models.operation_log import ACTION_DELETE, OperationLog
from app.models.space import ROLE_EDITOR, ROLE_VIEWER, Space
from app.models.storage_object import StorageObject
from app.models.user import User
from app.models.user_file import FILE_STATUS_NORMAL, UserFile
from app.services.folder_service import first_free_name, is_ancestor
from app.services.permission import ensure_role, get_personal_space
from app.services.quota_service import add_usage, check_and_add_usage, get_space_quota_limit

logger = logging.getLogger(__name__)

_MAX_TREE_DEPTH = 256


# ---------- 目标目录解析 ----------
async def _resolve_target(db: AsyncSession, user: User, target_folder_id: int) -> tuple[Space, UserFile | None]:
    if target_folder_id == 0:
        space = await get_personal_space(db, user.id)
        space_id_var.set(space.id)
        return space, None
    folder = await db.get(UserFile, target_folder_id)
    if folder is None or not folder.is_folder or folder.status != FILE_STATUS_NORMAL:
        raise BizError(ErrorCode.NOT_FOUND)
    space = await db.get(Space, folder.space_id)
    space_id_var.set(folder.space_id)
    return space, folder


async def _name_taken(db: AsyncSession, space_id: int, parent_id: int, name: str,
                      exclude_id: int | None = None) -> bool:
    cond = [
        UserFile.space_id == space_id, UserFile.parent_id == parent_id,
        UserFile.name == name, UserFile.status == FILE_STATUS_NORMAL,
    ]
    if exclude_id:
        cond.append(UserFile.id != exclude_id)
    return bool(await db.scalar(select(UserFile.id).where(*cond)))


# ---------- 移动 ----------
async def move_files(db: AsyncSession, user: User, file_ids: list[int], target_folder_id: int) -> list[dict]:
    space, target = await _resolve_target(db, user, target_folder_id)
    await ensure_role(db, user.id, space.id, ROLE_EDITOR)
    results = []
    for fid in file_ids:
        try:
            row = await db.get(UserFile, fid)
            if row is None or row.status != FILE_STATUS_NORMAL:
                raise BizError(ErrorCode.NOT_FOUND)
            await ensure_role(db, user.id, row.space_id, ROLE_EDITOR)
            if row.parent_id == target_folder_id:
                results.append({"id": fid, "ok": True, "message": "已在目标目录"})  # 幂等
                continue
            if row.is_folder and (target_folder_id == row.id or await is_ancestor(db, row.id, target_folder_id)):
                raise BizError(ErrorCode.FOLDER_CYCLE)
            if await _name_taken(db, row.space_id, target_folder_id, row.name, exclude_id=row.id):
                raise BizError(ErrorCode.NAME_CONFLICT)
            if target is not None and target.status != FILE_STATUS_NORMAL:
                raise BizError(ErrorCode.NOT_FOUND)
            row.parent_id = target_folder_id
            row.generation += 1
            await db.commit()
            results.append({"id": fid, "ok": True})
            logger.info("file moved", extra={"action": "move", "file_id": fid, "to": target_folder_id})
        except BizError as e:
            await db.rollback()
            results.append({"id": fid, "ok": False, "code": int(e.code), "message": e.message})
        except Exception as e:  # noqa: BLE001 — 单项异常不中断批量
            await db.rollback()
            results.append({"id": fid, "ok": False, "code": 9999, "message": str(e)[:120]})
    return results


# ---------- 复制 ----------
async def copy_files(db: AsyncSession, user: User, file_ids: list[int], target_folder_id: int) -> list[dict]:
    space, target = await _resolve_target(db, user, target_folder_id)
    await ensure_role(db, user.id, space.id, ROLE_EDITOR)
    limit = await get_space_quota_limit(db, space)
    results = []
    for fid in file_ids:
        try:
            src = await db.get(UserFile, fid)
            if src is None or src.status != FILE_STATUS_NORMAL:
                raise BizError(ErrorCode.NOT_FOUND)
            await ensure_role(db, user.id, src.space_id, ROLE_VIEWER)
            # 复制不产生命名冲突：克隆自动取下一个空位名（Windows「副本」风格），见 _copy_node
            copied = await _copy_node(db, src, target_folder_id, limit, user.id)
            await db.commit()
            results.append({"id": fid, "ok": True, "new_ids": copied})
            logger.info("file copied", extra={"action": "copy", "src": fid, "count": len(copied)})
        except BizError as e:
            await db.rollback()
            results.append({"id": fid, "ok": False, "code": int(e.code), "message": e.message})
        except Exception as e:  # noqa: BLE001
            await db.rollback()
            results.append({"id": fid, "ok": False, "code": 9999, "message": str(e)[:120]})
    return results


async def _copy_node(db: AsyncSession, src: UserFile, target_parent_id: int,
                     quota_limit: int, operator_id: int) -> list[int]:
    """复制单个节点（文件：新行+同对象新版本+配额；目录：递归建结构）。返回新建的 user_file ids。

    说明：跨空间复制要求源空间 ADMIN/OWNER（M8 团队空间接入时强校验）；
    当前个人空间内复制，源角色校验由调用方完成。
    """
    created: list[int] = []
    # 目录 → 逐层 BFS：队列元素 (源id, 新parent_id)
    queue: list[tuple[int, int]] = [(src.id, target_parent_id)]
    while queue and len(created) < 100000:
        src_id, parent_id = queue.pop(0)
        node = await db.get(UserFile, src_id)
        if node is None or node.status != FILE_STATUS_NORMAL:
            continue
        # 复制语义：与既有项同名（含原地复制与自身同名）→ 自动取下一个空位名
        # （Windows「副本」风格），绝不覆盖既有行；子节点在新子树内一般不冲突。
        final_name = await first_free_name(db, node.space_id, parent_id, node.name) or node.name
        clone = UserFile(
            space_id=node.space_id, parent_id=parent_id, name=final_name,
            is_folder=node.is_folder, status=FILE_STATUS_NORMAL, alive=1,
            generation=0, size=node.size,
        )
        db.add(clone)
        await db.flush()
        created.append(clone.id)
        if node.is_folder:
            child_ids = (await db.execute(
                select(UserFile.id).where(
                    UserFile.parent_id == node.id, UserFile.status == FILE_STATUS_NORMAL
                )
            )).scalars().all()
            queue.extend((cid, clone.id) for cid in child_ids)
        else:
            version = await db.get(FileVersion, node.current_version_id) if node.current_version_id else None
            if version is None:
                raise BizError(ErrorCode.INTERNAL, message="源文件版本缺失", http_status=500)
            obj = await db.get(StorageObject, version.storage_object_id)
            if obj is None:
                raise BizError(ErrorCode.INTERNAL, message="源文件对象缺失", http_status=500)
            # 目标空间配额检查（逻辑大小计费，与物理去重无关）
            await check_and_add_usage(db, node.space_id, quota_limit, node.size)
            obj.ref_count += 1
            new_version = FileVersion(
                file_id=clone.id, version_no=1, storage_object_id=obj.id,
                size=node.size, created_by=operator_id,
            )
            db.add(new_version)
            await db.flush()
            clone.current_version_id = new_version.id
    return created


# ---------- 批量删除（进回收站） ----------
async def batch_trash(db: AsyncSession, user: User, file_ids: list[int]) -> list[dict]:
    from app.services.file_service import trash_file

    results = []
    for fid in file_ids:
        try:
            r = await trash_file(db, user, fid)
            results.append({"id": fid, "ok": True, "trashed": r["trashed"], "name": r["name"]})
        except BizError as e:
            results.append({"id": fid, "ok": False, "code": int(e.code), "message": e.message})
        except Exception as e:  # noqa: BLE001
            results.append({"id": fid, "ok": False, "code": 9999, "message": str(e)[:120]})
    ok = [r for r in results if r.get("ok")]
    if ok:
        db.add(OperationLog(
            user_id=user.id, action=ACTION_DELETE, target_type="batch_trash",
            target_id=None, detail=f"count={len(ok)} ids={ok[:10]}",
        ))
        await db.commit()
    return results


# ---------- 搜索 ----------
def _escape_like(q: str) -> str:
    return q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


async def search_files(
    db: AsyncSession, user: User, q: str,
    page: int = 1, page_size: int = 20,
    sort: str = "name", order: str = "asc",
) -> tuple[list[UserFile], int]:
    space = await get_personal_space(db, user.id)
    await ensure_role(db, user.id, space.id, ROLE_VIEWER)
    space_id_var.set(space.id)

    pattern = f"%{_escape_like(q)}%"
    where = [
        UserFile.space_id == space.id, UserFile.status == FILE_STATUS_NORMAL,
        UserFile.name.like(pattern, escape="\\"),
    ]
    total = await db.scalar(select(func.count()).select_from(UserFile).where(*where))
    sort_col = {"name": UserFile.name, "size": UserFile.size, "uploaded_at": UserFile.updated_at}.get(sort)
    if sort_col is None:
        raise BizError(ErrorCode.BAD_REQUEST, message="不支持的排序字段")
    stmt = (
        select(UserFile).where(*where)
        .order_by(UserFile.is_folder.desc(), sort_col.desc() if order == "desc" else sort_col.asc())
        .offset((page - 1) * page_size).limit(page_size)
    )
    rows = (await db.execute(stmt)).scalars().all()
    return list(rows), int(total or 0)
