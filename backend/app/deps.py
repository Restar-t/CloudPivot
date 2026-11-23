"""FastAPI 依赖汇总：路由只从这里拿依赖，保持薄路由纪律。

M0：get_db / get_redis / get_storage
M1：get_current_user（JWT + Redis 会话屏障）
M2 起追加：require_space_role
"""
from typing import Annotated

from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import BizError, ErrorCode
from app.core.logging import user_id_var
from app.core.security import decode_access_token
from app.db.session import get_db
from app.models.user import User
from app.services.auth_service import access_denied
from app.storage.minio_client import MinioStorage

DB = Annotated[AsyncSession, Depends(get_db)]

_bearer = HTTPBearer(auto_error=False, description="Bearer access token")


def get_redis(request: Request) -> Redis:
    """全局 Redis 客户端（lifespan 中创建，app.state 挂载）。"""
    return request.app.state.redis


def get_storage(request: Request) -> MinioStorage:
    """全局 MinIO 封装实例（lifespan 中创建）。"""
    return request.app.state.storage


RedisDep = Annotated[Redis, Depends(get_redis)]
StorageDep = Annotated[MinioStorage, Depends(get_storage)]


async def get_current_user(
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db)],
    redis: Annotated[Redis, Depends(get_redis)],
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)] = None,
) -> User:
    """解析 Bearer JWT → Redis 会话屏障（退出黑名单/改密时间线）→ 装载用户。

    401 语义：缺令牌/令牌无效 → 1001；过期 → 1002；退出或改密后 → 1003。
    """
    if credentials is None or not credentials.credentials:
        raise BizError(ErrorCode.UNAUTHENTICATED)
    claims = decode_access_token(credentials.credentials)

    try:
        denied = await access_denied(redis, int(claims["sub"]), claims["jti"], claims["iat"])
    except BizError:
        raise
    except Exception as exc:  # Redis 故障：fail-closed，避免撤销语义被绕过
        raise BizError(ErrorCode.DEPENDENCY_UNAVAILABLE) from exc
    if denied:
        raise BizError(ErrorCode.SESSION_REVOKED)

    user = await db.get(User, int(claims["sub"]))
    if user is None or not user.is_active:
        raise BizError(ErrorCode.UNAUTHENTICATED)

    request.state.access_claims = claims
    user_id_var.set(user.id)
    return user


CurrentUser = Annotated[User, Depends(get_current_user)]
