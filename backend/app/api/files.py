"""文件接口（M2 起逐步扩充：rename → M3 upload/download/delete → M4 move/copy/zip...）。"""
from fastapi import APIRouter

from app.core.response import ApiRoute
from app.deps import CurrentUser, DB
from app.schemas.file import FileItemOut, RenameRequest
from app.services import folder_service

router = APIRouter(route_class=ApiRoute, prefix="/files", tags=["files"])


@router.patch("/{file_id}/rename")
async def rename(file_id: int, body: RenameRequest, user: CurrentUser, db: DB) -> FileItemOut:
    return await folder_service.rename_file(db, user, file_id, body.new_name)
