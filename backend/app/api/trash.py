"""回收站接口（M4）：列表 / 还原 / 彻底删除 / 清空。"""
from fastapi import APIRouter, Query

from app.core.response import ApiRoute
from app.deps import CurrentUser, DB, StorageDep
from app.schemas.file import TrashPageOut
from app.services import trash_service

router = APIRouter(route_class=ApiRoute, prefix="/trash", tags=["trash"])


@router.get("")
async def list_trash(
    user: CurrentUser, db: DB,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=100),
) -> TrashPageOut:
    items, total = await trash_service.list_trash(db, user, page, page_size)
    return TrashPageOut(items=items, total=total, page=page, page_size=page_size)


@router.post("/{file_id}/restore")
async def restore(file_id: int, user: CurrentUser, db: DB) -> dict:
    return await trash_service.restore(db, user, file_id)


@router.delete("/{file_id}")
async def purge(file_id: int, user: CurrentUser, db: DB, storage: StorageDep) -> dict:
    return await trash_service.purge(db, storage, user, file_id)


@router.delete("")
async def clear(user: CurrentUser, db: DB, storage: StorageDep) -> dict:
    return await trash_service.clear(db, storage, user)
