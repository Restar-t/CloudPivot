"""目录接口（M2）：创建 / 子列表 / 面包屑。鉴权全部经 folder_service → ensure_role。"""
from fastapi import APIRouter, Query

from app.core.response import ApiRoute
from app.deps import CurrentUser, DB
from app.schemas.file import (
    CreateFolderRequest,
    FileItemOut,
    PageOut,
    PathOut,
)
from app.services import folder_service

router = APIRouter(route_class=ApiRoute, prefix="/folders", tags=["folders"])


@router.post("")
async def create_folder(body: CreateFolderRequest, user: CurrentUser, db: DB) -> FileItemOut:
    return await folder_service.create_folder(db, user, body.parent_id, body.name)


@router.get("/{folder_id}/children")
async def list_children(
    folder_id: int,
    user: CurrentUser,
    db: DB,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=100),
    sort: str = Query(default="name", pattern="^(name|size|uploaded_at)$"),
    order: str = Query(default="asc", pattern="^(asc|desc)$"),
    folder_only: bool = Query(default=False, description="仅返回文件夹（目录树选择器用）"),
) -> PageOut:
    items, total = await folder_service.list_children(
        db, user, folder_id, page, page_size, sort, order
    )
    if folder_only:
        items = [i for i in items if i.is_folder]
        # 过滤后 total 不再准确——树选择器只关心子文件夹，直接给出过滤后数量
        total = len(items)
    return PageOut(items=items, total=total, page=page, page_size=page_size)


@router.get("/{folder_id}/path")
async def get_path(folder_id: int, user: CurrentUser, db: DB) -> PathOut:
    chain = await folder_service.get_path(db, user, folder_id)
    return PathOut(items=[{"id": c["id"], "name": c["name"]} for c in chain])
