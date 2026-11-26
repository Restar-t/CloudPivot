"""App 工厂：生命周期（Redis/MinIO/桶初始化）、中间件（trace/CORS）、异常处理器、路由注册。"""
import logging
import time
import uuid
from contextlib import asynccontextmanager

import redis.asyncio as aioredis
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.api import auth, files, folders, health, public_share, shares, trash, uploads, users
from app.core.config import get_settings
from app.core.errors import BizError, ErrorCode
from app.core.logging import (
    client_ip_var,
    setup_logging,
    trace_id_var,
)
from app.core.response import error_body
from app.db.session import dispose_engine
from app.storage.minio_client import MinioStorage

logger = logging.getLogger(__name__)

# HTTP 状态 -> 系统错误码（框架层异常兜底映射）
_STATUS_TO_CODE = {
    400: ErrorCode.BAD_REQUEST,
    401: ErrorCode.UNAUTHENTICATED,
    403: ErrorCode.FORBIDDEN,
    404: ErrorCode.ROUTE_NOT_FOUND,
    405: ErrorCode.METHOD_NOT_ALLOWED,
    413: ErrorCode.FILE_TOO_LARGE,
    422: ErrorCode.BAD_REQUEST,
    429: ErrorCode.LOGIN_LOCKED,
    503: ErrorCode.DEPENDENCY_UNAVAILABLE,
}


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    setup_logging(settings.debug)
    logger.info(
        "app starting",
        extra={"env": settings.env, "version": settings.app_version},
    )

    # MinIO：初始化两桶（失败告警不阻断，/health 会暴露 degraded）
    app.state.storage = MinioStorage.from_settings(settings)
    await app.state.storage.ensure_buckets(
        retries=settings.bucket_init_retries,
        delay=settings.bucket_init_delay_seconds,
    )

    # Redis：全局客户端（登录计数/缓存/限流共用）
    app.state.redis = aioredis.from_url(
        settings.redis_url, decode_responses=True, socket_connect_timeout=3
    )
    try:
        await app.state.redis.ping()
    except Exception as exc:  # noqa: BLE001
        logger.error("redis connect failed at startup", extra={"error": str(exc)})

    from app.services.background import start_background_loops, stop_background_loops

    await start_background_loops(app)
    yield
    await stop_background_loops()
    await app.state.redis.aclose()
    app.state.storage.close()
    await dispose_engine()
    logger.info("app stopped")


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title=settings.app_name,
        version=settings.app_version,
        lifespan=lifespan,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=["X-Trace-Id"],
    )

    @app.middleware("http")
    async def trace_middleware(request: Request, call_next):
        """分配 trace_id、记录访问日志；兜底把未处理异常转为 9999 响应包。"""
        trace_id = request.headers.get("X-Trace-Id") or uuid.uuid4().hex[:16]
        trace_id_var.set(trace_id)
        client_ip_var.set(request.client.host if request.client else None)
        start = time.perf_counter()
        try:
            response = await call_next(request)
        except Exception:
            logger.exception("unhandled exception")
            response = JSONResponse(
                content=error_body(int(ErrorCode.INTERNAL), ErrorCode.INTERNAL.name.lower()),
                status_code=500,
            )
        duration_ms = round((time.perf_counter() - start) * 1000, 2)
        logger.info(
            "request done",
            extra={
                "method": request.method,
                "path": request.url.path,
                "status": response.status_code,
                "duration_ms": duration_ms,
            },
        )
        response.headers["X-Trace-Id"] = trace_id
        return response

    @app.exception_handler(BizError)
    async def biz_error_handler(request: Request, exc: BizError):
        logger.warning(
            "biz error",
            extra={"biz_code": int(exc.code), "detail": exc.message},
        )
        return JSONResponse(
            content=error_body(int(exc.code), exc.message, exc.data),
            status_code=exc.http_status,
        )

    @app.exception_handler(RequestValidationError)
    async def validation_error_handler(request: Request, exc: RequestValidationError):
        errors = [
            {"loc": ".".join(str(x) for x in e.get("loc", [])), "msg": e.get("msg", "")}
            for e in exc.errors()
        ]
        return JSONResponse(
            content=error_body(int(ErrorCode.BAD_REQUEST), "请求参数错误", errors),
            status_code=422,
        )

    @app.exception_handler(StarletteHTTPException)
    async def http_exception_handler(request: Request, exc: StarletteHTTPException):
        code = _STATUS_TO_CODE.get(exc.status_code, ErrorCode.INTERNAL)
        return JSONResponse(
            content=error_body(int(code), str(exc.detail)),
            status_code=exc.status_code,
        )

    @app.exception_handler(Exception)
    async def fallback_error_handler(request: Request, exc: Exception):
        logger.exception("unhandled exception", exc_info=exc)
        return JSONResponse(
            content=error_body(int(ErrorCode.INTERNAL), "服务内部错误"),
            status_code=500,
        )

    app.include_router(health.router, prefix=settings.api_prefix)
    app.include_router(auth.router, prefix=settings.api_prefix)
    app.include_router(users.router, prefix=settings.api_prefix)
    app.include_router(folders.router, prefix=settings.api_prefix)
    app.include_router(files.router, prefix=settings.api_prefix)
    app.include_router(trash.router, prefix=settings.api_prefix)
    app.include_router(shares.router, prefix=settings.api_prefix)
    app.include_router(public_share.router, prefix=settings.api_prefix)
    app.include_router(uploads.router, prefix=settings.api_prefix)
    return app


app = create_app()
