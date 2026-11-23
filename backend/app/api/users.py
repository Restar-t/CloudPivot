"""用户接口（M1：/me；M3 起补充用量统计）。"""
from fastapi import APIRouter

from app.core.response import ApiRoute
from app.deps import CurrentUser
from app.schemas.auth import UserOut

router = APIRouter(route_class=ApiRoute, prefix="/users", tags=["users"])


@router.get("/me")
async def me(user: CurrentUser) -> UserOut:
    return UserOut.model_validate(user)
