"""分享成员接口（M5）：创建 / 我的分享列表 / 取消。"""
from fastapi import APIRouter
from pydantic import BaseModel, Field

from app.core.response import ApiRoute
from app.deps import CurrentUser, DB
from app.services import share_service

router = APIRouter(route_class=ApiRoute, prefix="/shares", tags=["shares"])


class ShareCreateRequest(BaseModel):
    file_id: int
    extract_code: str | None = Field(default=None, max_length=16)
    expire_days: int | None = Field(default=None, ge=1, le=365)
    max_visits: int | None = Field(default=None, ge=1, le=100000)


class ShareCancelRequest(BaseModel):
    pass  # 占位：取消无请求体


@router.post("")
async def create(body: ShareCreateRequest, user: CurrentUser, db: DB) -> dict:
    return await share_service.create_share(
        db, user, body.file_id, body.extract_code, body.expire_days, body.max_visits
    )


@router.get("")
async def my_shares(user: CurrentUser, db: DB) -> dict:
    return {"items": await share_service.list_shares(db, user)}


@router.post("/{share_id}/cancel")
async def cancel(share_id: int, user: CurrentUser, db: DB) -> dict:
    return await share_service.cancel_share(db, user, share_id)
