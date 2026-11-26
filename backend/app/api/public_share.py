"""公开分享接口（M5）：免登录，独立于空间成员体系。

- POST /{token}/auth        提取码换会话（防爆破计数、幂等键、原子扣名额）
- GET  /{token}/download    绑定版本流式下载（Range 复用 M3，传输中周期复核）
- GET  /{token}/preview     内联预览（图片/PDF/文本；其余 attachment）
会话凭据经查询参数 session= 传递（<img>/<a> 无法带自定义头；已记入已知限制）。
"""
from fastapi import APIRouter, Query, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from app.core.config import get_settings
from app.core.errors import BizError, ErrorCode
from app.core.response import ApiRoute
from app.deps import DB, RedisDep, StorageDep
from app.models.file_version import FileVersion
from app.models.storage_object import StorageObject
from app.services import file_service, preview_service, share_service

router = APIRouter(route_class=ApiRoute, prefix="/s", tags=["public-share"])

_CSP = "default-src 'none'; img-src 'self'; style-src 'unsafe-inline'; sandbox"


class ShareAuthRequest(BaseModel):
    extract_code: str = Field(min_length=1, max_length=32)
    idempotency_key: str | None = Field(default=None, max_length=64)


@router.post("/{token}/auth")
async def auth(token: str, body: ShareAuthRequest, request: Request, db: DB, redis: RedisDep) -> dict:
    from app.api.auth import client_ip

    return await share_service.auth_share(
        db, redis, token, body.extract_code, client_ip(request),
        (body.idempotency_key or "").strip() or None,
    )


async def _load_object(db: DB, version_id: int) -> StorageObject:
    version = await db.get(FileVersion, version_id)
    if version is None:
        raise BizError(ErrorCode.INTERNAL, message="分享版本缺失", http_status=500)
    obj = await db.get(StorageObject, version.storage_object_id)
    if obj is None or obj.status != "ACTIVE":
        raise BizError(ErrorCode.INTERNAL, message="分享内容缺失", http_status=500)
    return obj


@router.get("/{token}/download")
async def download(
    token: str, request: Request, db: DB, storage: StorageDep,
    session: str = Query(min_length=8, max_length=64),
):
    share, sess, version, file = await share_service.validate_session(db, token, session)
    obj = await _load_object(db, version.id)
    size = int(obj.size)
    etag = f'"{obj.sha256}"'

    base_headers = {
        "Accept-Ranges": "bytes",
        "ETag": etag,
        "Cache-Control": "private, no-store",
        "X-Content-Type-Options": "nosniff",
        "Content-Disposition": file_service.content_disposition(file.name),
    }
    guarded = share_service.transfer_guard(storage, share, sess)

    range_header = request.headers.get("Range")
    if range_header and (not request.headers.get("If-Range") or request.headers.get("If-Range") == etag):
        parsed = file_service.parse_range_header(range_header, size)
        if parsed == "invalid":
            raise BizError(ErrorCode.RANGE_NOT_SATISFIABLE, data={"range": range_header, "size": size})
        if parsed:
            start, length = parsed
            return StreamingResponse(
                guarded(file_service.iter_object(storage, obj, start, length)),
                status_code=206, media_type="application/octet-stream",
                headers={**base_headers, "Content-Range": f"bytes {start}-{start + length - 1}/{size}",
                         "Content-Length": str(length)},
            )

    return StreamingResponse(
        guarded(file_service.iter_object(storage, obj, 0, size)),
        status_code=200, media_type="application/octet-stream",
        headers={**base_headers, "Content-Length": str(size)},
    )


@router.get("/{token}/preview")
async def preview(
    token: str, db: DB, storage: StorageDep,
    session: str = Query(min_length=8, max_length=64),
):
    """公开预览：图片/PDF inline（CSP+nosniff）；文本 ≤2MiB inline；其余 attachment 下载。"""
    share, sess, version, file = await share_service.validate_session(db, token, session)
    obj = await _load_object(db, version.id)
    size = int(obj.size)

    head = b""
    async for chunk in file_service.iter_object(storage, obj, 0, min(64, size)):
        head += chunk

    text_ok = False
    if file.name.lower().endswith(tuple(preview_service._TEXT_EXTS)):
        limit = get_settings().share_preview_text_max_bytes
        if size <= limit:
            data = b""
            async for chunk in file_service.iter_object(storage, obj, 0, size):
                data += chunk
            text_ok = preview_service.try_decode_text(data) is not None

    decision = preview_service.decide(file.name, head, size, text_ok)
    if decision.disposition == "attachment":
        return StreamingResponse(
            file_service.iter_object(storage, obj, 0, size),
            status_code=200, media_type=decision.content_type,
            headers={
                "Content-Disposition": file_service.content_disposition(file.name),
                "Cache-Control": "private, no-store",
                "X-Content-Type-Options": "nosniff",
                "X-Preview-Reason": decision.reason,
                "Content-Length": str(size),
            },
        )

    guarded = share_service.transfer_guard(storage, share, sess)
    return StreamingResponse(
        guarded(file_service.iter_object(storage, obj, 0, size)),
        status_code=200,
        headers={
            "Content-Type": decision.content_type,
            "Cache-Control": "private, no-store",
            "X-Content-Type-Options": "nosniff",
            "Content-Security-Policy": _CSP,
            "X-Preview-Reason": decision.reason,
            "Content-Length": str(size),
        },
    )
