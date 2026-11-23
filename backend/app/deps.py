"""FastAPI 依赖汇总：路由只从这里拿依赖，保持薄路由纪律。

M0：get_db / get_redis / get_storage
M1 起追加：get_current_user / require_space_role
"""
from typing import Annotated

from fastapi import Depends, Request
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.storage.minio_client import MinioStorage

DB = Annotated[AsyncSession, Depends(get_db)]


def get_redis(request: Request) -> Redis:
    """全局 Redis 客户端（lifespan 中创建，app.state 挂载）。"""
    return request.app.state.redis


def get_storage(request: Request) -> MinioStorage:
    """全局 MinIO 封装实例（lifespan 中创建）。"""
    return request.app.state.storage


RedisDep = Annotated[Redis, Depends(get_redis)]
StorageDep = Annotated[MinioStorage, Depends(get_storage)]
