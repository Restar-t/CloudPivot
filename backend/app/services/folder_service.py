"""目录服务：新建文件夹 / 子列表（分页排序）/ 面包屑 / 重命名 / 循环祖先校验。

M2 约定：空间=当前用户个人空间（parent_id=0 即空间根）；
M8 团队空间接入时，接口将显式携带 space_id，此处服务层不变。
"""
import logging

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import BizError, ErrorCode
from app.core.filename import validate_filename
from app.core.logging import space_id_var
from app.models.space import ROLE_EDITOR, ROLE_VIEWER
from app.models.user import User
from app.models.user_file import (
    FILE_STATUS_NORMAL,
    ROOT_PARENT_ID,
    UserFile,
)
from app.schemas.file import FileItemOut
from app.services.permission import ensure_role, get_personal_space

logger = logging.getLogger(__name__)

_MAX_DEPTH = 256  # 防御损坏数据成环

_SORT_COLUMNS = {
    "name": UserFile.name,
    "size": UserFile.size,
    "uploaded_at": UserFile.updated_at,
}


async def _resolve_parent(
    db: AsyncSession, user: User, parent_id: int
) -> tuple[int, UserFile | None]:
    """parent_id → (space_id, 父目录行)。0=空间根（父行为 None）。非成员/不存在 → 404。"""
    if parent_id == ROOT_PARENT_ID:
        space = await get_personal_space(db, user.id)
        await ensure_role(db, user.id, space.id, ROLE_VIEWER)
        space_id_var.set(space.id)
        return space.id, None
    parent = await db.get(UserFile, parent_id)
    if parent is None or not parent.is_folder or parent.status != FILE_STATUS_NORMAL:
        raise BizError(ErrorCode.NOT_FOUND)
    await ensure_role(db, user.id, parent.space_id, ROLE_VIEWER)
    space_id_var.set(parent.space_id)
    return parent.space_id, parent


async def create_folder(db: AsyncSession, user: User, parent_id: int, name: str) -> FileItemOut:
    name = validate_filename(name)
    space_id, parent = await _resolve_parent(db, user, parent_id)
    # 目标父目录对写操作的角色要求：EDITOR+（VIEWER 只读）
    await ensure_role(db, user.id, space_id, ROLE_EDITOR)
    if parent is not None and parent.status != FILE_STATUS_NORMAL:  # 双检（并发下父目录可能刚进回收站）
        raise BizError(ErrorCode.NOT_FOUND)

    folder = UserFile(
        space_id=space_id,
        parent_id=parent_id,
        name=name,
        is_folder=True,
        status=FILE_STATUS_NORMAL,
        alive=1,
        generation=0,
    )
    db.add(folder)
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise BizError(ErrorCode.NAME_CONFLICT) from exc
    await db.refresh(folder)
    logger.info(
        "folder created",
        extra={"action": "folder_create", "target": name, "folder_id": folder.id,
               "user_id": user.id, "space_id": space_id},
    )
    return FileItemOut.model_validate(folder)


async def list_children(
    db: AsyncSession,
    user: User,
    folder_id: int,
    page: int = 1,
    page_size: int = 50,
    sort: str = "name",
    order: str = "asc",
) -> tuple[list[FileItemOut], int]:
    space_id, _parent = await _resolve_parent(db, user, folder_id)

    base = select(UserFile).where(
        UserFile.space_id == space_id,
        UserFile.parent_id == folder_id,
        UserFile.status == FILE_STATUS_NORMAL,
    )
    total = await db.scalar(
        select(func.count()).select_from(UserFile).where(
            UserFile.space_id == space_id,
            UserFile.parent_id == folder_id,
            UserFile.status == FILE_STATUS_NORMAL,
        )
    )

    sort_col = _SORT_COLUMNS.get(sort)
    if sort_col is None:
        raise BizError(ErrorCode.BAD_REQUEST, message="不支持的排序字段")
    direction = sort_col.desc() if order == "desc" else sort_col.asc()
    # 文件夹置顶后再按请求排序
    stmt = (
        base.order_by(UserFile.is_folder.desc(), direction)
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    rows = (await db.execute(stmt)).scalars().all()
    return [FileItemOut.model_validate(r) for r in rows], int(total or 0)


async def get_path(db: AsyncSession, user: User, folder_id: int) -> list[dict]:
    """面包屑：从根到当前目录的祖先链；folder_id=0 返回 [根]。"""
    chain: list[dict] = []
    cur_id = folder_id
    space_id: int | None = None
    for _ in range(_MAX_DEPTH):
        if cur_id == ROOT_PARENT_ID:
            break
        node = await db.get(UserFile, cur_id)
        if node is None or node.status != FILE_STATUS_NORMAL:
            raise BizError(ErrorCode.NOT_FOUND)
        space_id = node.space_id
        chain.insert(0, {"id": node.id, "name": node.name})
        cur_id = node.parent_id
    else:
        raise BizError(ErrorCode.INTERNAL, message="目录层级过深或数据成环", http_status=500)

    if space_id is not None:
        await ensure_role(db, user.id, space_id, ROLE_VIEWER)
    chain.insert(0, {"id": ROOT_PARENT_ID, "name": "根目录"})
    return chain


async def rename_file(db: AsyncSession, user: User, file_id: int, new_name: str) -> FileItemOut:
    new_name = validate_filename(new_name)
    row = await db.get(UserFile, file_id)
    if row is None or row.status != FILE_STATUS_NORMAL:
        raise BizError(ErrorCode.NOT_FOUND)
    await ensure_role(db, user.id, row.space_id, ROLE_EDITOR)

    if row.name == new_name:
        return FileItemOut.model_validate(row)  # 幂等：同名不改不 bump generation

    dup = await db.scalar(
        select(func.count()).select_from(UserFile).where(
            UserFile.space_id == row.space_id,
            UserFile.parent_id == row.parent_id,
            UserFile.name == new_name,
            UserFile.status == FILE_STATUS_NORMAL,
            UserFile.id != row.id,
        )
    )
    if dup:
        raise BizError(ErrorCode.NAME_CONFLICT)

    row.name = new_name
    row.generation += 1  # 使进行中的同名上传任务完成校验失败（M6 依赖）
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise BizError(ErrorCode.NAME_CONFLICT) from exc
    await db.refresh(row)
    logger.info(
        "file renamed",
        extra={"action": "rename", "target": new_name, "file_id": row.id, "user_id": user.id},
    )
    return FileItemOut.model_validate(row)


async def is_ancestor(db: AsyncSession, ancestor_id: int, node_id: int) -> bool:
    """node 的祖先链上是否包含 ancestor（M4 移动的循环检测；含 ancestor==node）。"""
    cur = node_id
    for _ in range(_MAX_DEPTH):
        if cur == ancestor_id:
            return True
        if cur == ROOT_PARENT_ID:
            return False
        node = await db.get(UserFile, cur)
        if node is None:
            return False
        cur = node.parent_id
    return False
