"""系统接口：/ping（存活）与 /health（三个依赖的真实状态）。"""
import asyncio
import logging

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from sqlalchemy import text

from app.core.config import get_settings
from app.core.response import ApiRoute, ok
from app.db.session import get_engine

logger = logging.getLogger(__name__)

router = APIRouter(route_class=ApiRoute, tags=["system"])

_CHECK_TIMEOUT_SECONDS = 3.0


async def _check_mysql() -> str:
    async with get_engine().connect() as conn:
        await conn.execute(text("SELECT 1"))
    return "up"


async def _check_redis(request: Request) -> str:
    await request.app.state.redis.ping()
    return "up"


async def _check_minio(request: Request) -> str:
    storage = request.app.state.storage
    if not await storage.bucket_exists(storage.bucket_formal):
        raise RuntimeError(f"bucket {storage.bucket_formal} missing")
    return "up"


@router.get("/ping")
async def ping():
    """存活探针（compose healthcheck 使用），不带依赖检查。"""
    return ok("pong")


@router.get("/health")
async def health(request: Request):
    """依赖就绪探针：MySQL / Redis / MinIO 逐项检查，任一失败整体 503。"""
    checks = {
        "mysql": _check_mysql,
        "redis": lambda: _check_redis(request),
        "minio": lambda: _check_minio(request),
    }
    components: dict[str, str] = {}
    for name, fn in checks.items():
        try:
            components[name] = await asyncio.wait_for(fn(), timeout=_CHECK_TIMEOUT_SECONDS)
        except Exception as exc:  # noqa: BLE001 - 单项失败不影响其余项的报告
            logger.warning("health check failed", extra={"component": name, "error": str(exc)})
            components[name] = f"down: {type(exc).__name__}"

    settings = get_settings()
    status = "ok" if all(v == "up" for v in components.values()) else "degraded"
    data = {
        "status": status,
        "components": components,
        "app": settings.app_name,
        "version": settings.app_version,
        "env": settings.env,
    }
    # code=0 表示"本次检查成功执行"；HTTP 状态表达依赖是否就绪（503 供探针/冒烟判断）
    http_status = 200 if status == "ok" else 503
    return JSONResponse(content=ok(data), status_code=http_status)
