"""用户接口（M1：/me；M3 起补充配额用量）。"""
from fastapi import APIRouter

from app.core.response import ApiRoute
from app.deps import CurrentUser, DB
from app.schemas.auth import UserOut
from app.schemas.file import QuotaOut
from app.services.permission import get_personal_space
from app.services.quota_service import get_space_quota_limit, get_usage_summary

router = APIRouter(route_class=ApiRoute, prefix="/users", tags=["users"])


@router.get("/me")
async def me(user: CurrentUser) -> UserOut:
    return UserOut.model_validate(user)


@router.get("/me/quota")
async def my_quota(user: CurrentUser, db: DB) -> QuotaOut:
    """当前用户个人空间的配额与用量（used 含回收站与历史版本）。"""
    space = await get_personal_space(db, user.id)
    limit = await get_space_quota_limit(db, space)
    return QuotaOut(**await get_usage_summary(db, space.id, limit))
