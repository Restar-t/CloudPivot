"""认证接口：薄路由，业务全部在 auth_service。

- register/login/refresh 无需认证
- logout/password 需 access 令牌（logout 同时撤销 refresh）
"""
from fastapi import APIRouter, Request

from app.core.response import ApiRoute, ok
from app.deps import CurrentUser, DB, RedisDep
from app.schemas.auth import (
    LoginRequest,
    LoginResult,
    LogoutRequest,
    PasswordChangeRequest,
    RefreshRequest,
    RegisterRequest,
    TokenPair,
    UserOut,
)
from app.services import auth_service

router = APIRouter(route_class=ApiRoute, prefix="/auth", tags=["auth"])


def client_ip(request: Request) -> str:
    """nginx 反代场景取 X-Forwarded-For 首段。"""
    forwarded = request.headers.get("X-Forwarded-For")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else ""


@router.post("/register")
async def register(body: RegisterRequest, db: DB) -> UserOut:
    return await auth_service.register(db, body.username, body.password)


@router.post("/login")
async def login(body: LoginRequest, request: Request, db: DB, redis: RedisDep) -> LoginResult:
    return await auth_service.login(db, redis, body.username, body.password, client_ip(request))


@router.post("/refresh")
async def refresh(body: RefreshRequest, db: DB, redis: RedisDep) -> TokenPair:
    return await auth_service.refresh(db, redis, body.refresh_token)


@router.post("/logout")
async def logout(
    body: LogoutRequest, request: Request, user: CurrentUser, db: DB, redis: RedisDep
):
    claims = request.state.access_claims
    await auth_service.logout(
        db, redis, user, body.refresh_token, claims["jti"], claims["exp"]
    )
    return ok(True)


@router.put("/password")
async def change_password(
    body: PasswordChangeRequest, request: Request, user: CurrentUser, db: DB, redis: RedisDep
) -> TokenPair:
    current_refresh = request.headers.get("X-Refresh-Token")
    return await auth_service.change_password(
        db, redis, user, body.old_password, body.new_password, current_refresh
    )
