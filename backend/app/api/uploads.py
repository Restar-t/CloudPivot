"""分片上传接口（M6）：init / parts / status / complete / cancel。

分片体走请求体原始字节（application/octet-stream），校验和经 X-Part-Sha256 头声明。
"""
from fastapi import APIRouter, Query, Request
from pydantic import BaseModel, Field

from app.core.response import ApiRoute
from app.deps import CurrentUser, DB, StorageDep
from app.services import upload_service

router = APIRouter(route_class=ApiRoute, prefix="/uploads", tags=["uploads"])


class UploadInitRequest(BaseModel):
    file_name: str = Field(min_length=1, max_length=255)
    size: int = Field(gt=0)
    parent_id: int = Field(default=0, ge=0)
    declared_sha256: str | None = Field(default=None, min_length=64, max_length=64)


@router.post("/init")
async def init(body: UploadInitRequest, user: CurrentUser, db: DB) -> dict:
    return await upload_service.init_upload(
        db, user, body.parent_id, body.file_name, body.size, body.declared_sha256
    )


@router.put("/{upload_id}/parts/{part_no}")
async def put_part(
    upload_id: str, part_no: int, request: Request,
    user: CurrentUser, db: DB, storage: StorageDep,
    part_sha256: str = Query(alias="sha256", min_length=64, max_length=64),
) -> dict:
    data = await request.body()  # 分片 8MiB：uvicorn 在内存可承受（STREAM 纪律针对整文件，单分片有界）
    return await upload_service.put_part(db, storage, user, upload_id, part_no, part_sha256, data)


@router.get("/{upload_id}")
async def status(upload_id: str, user: CurrentUser, db: DB, storage: StorageDep) -> dict:
    return await upload_service.task_status(db, storage, user, upload_id)


@router.post("/{upload_id}/complete")
async def complete(upload_id: str, user: CurrentUser, db: DB, storage: StorageDep) -> dict:
    return await upload_service.complete_upload(db, storage, user, upload_id)


@router.post("/{upload_id}/cancel")
async def cancel(upload_id: str, user: CurrentUser, db: DB, storage: StorageDep) -> dict:
    return await upload_service.cancel_upload(db, storage, user, upload_id)
