"""文件接口：rename（M2）/ upload / download（Range）/ delete（M3）。

下载永远走应用网关（PLAN §2.5）：附件头 + 私有禁缓存 + nosniff；多段 Range 降级 200 全量（已知限制）。
"""
from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse

from app.core.errors import BizError, ErrorCode
from app.core.response import ApiRoute
from app.deps import CurrentUser, DB, StorageDep
from app.schemas.file import FileItemOut, RenameRequest, UploadOut
from app.services import file_service, folder_service

router = APIRouter(route_class=ApiRoute, prefix="/files", tags=["files"])


@router.patch("/{file_id}/rename")
async def rename(file_id: int, body: RenameRequest, user: CurrentUser, db: DB) -> FileItemOut:
    return await folder_service.rename_file(db, user, file_id, body.new_name)


@router.post("/upload")
async def upload(
    parent_id: int = 0, user: CurrentUser = None, db: DB = None,
    storage: StorageDep = None, request: Request = None,
) -> UploadOut:
    """multipart 单请求上传。parent_id 取表单字段或查询参数（默认 0=空间根）。"""
    form = await request.form()
    raw_parent = form.get("parent_id", parent_id)
    try:
        parent_id_val = int(raw_parent)
    except (TypeError, ValueError):
        raise BizError(ErrorCode.BAD_REQUEST, message="parent_id 必须为整数")
    upload_file = form.get("file")
    if upload_file is None or isinstance(upload_file, str):
        raise BizError(ErrorCode.BAD_REQUEST, message="缺少文件字段 file")
    result = await file_service.upload_file(db, storage, user, parent_id_val, upload_file)
    return UploadOut(
        **result.file.model_dump(),
        version_no=result.version_no,
        storage_object_id=result.storage_object_id,
        sha256=result.sha256,
    )


@router.get("/{file_id}/download")
async def download(file_id: int, request: Request, user: CurrentUser, db: DB, storage: StorageDep):
    file, obj, version, etag = await file_service.download_file(db, storage, user, file_id)
    size = int(obj.size)

    base_headers = {
        "Accept-Ranges": "bytes",
        "ETag": etag,
        "Cache-Control": "private, no-store",           # 私有禁缓存：公共缓存不得绕过鉴权
        "X-Content-Type-Options": "nosniff",
        "Content-Disposition": file_service.content_disposition(file.name),
    }

    range_header = request.headers.get("Range")
    if_range = request.headers.get("If-Range")
    if range_header:
        # If-Range：校验不过（内容已变）→ 忽略 Range，按 200 全量返回
        honor_range = not if_range or if_range.strip() == etag
        parsed = file_service.parse_range_header(range_header, size) if honor_range else None
        if parsed == "invalid":
            raise BizError(
                ErrorCode.RANGE_NOT_SATISFIABLE,
                data={"range": range_header, "size": size},
            )
        if parsed:
            start, length = parsed
            headers = {
                **base_headers,
                "Content-Range": f"bytes {start}-{start + length - 1}/{size}",
                "Content-Length": str(length),
            }
            return StreamingResponse(
                file_service.iter_object(storage, obj, start, length),
                status_code=206, media_type="application/octet-stream", headers=headers,
            )
        # 解析失败/If-Range 不匹配 → 落到 200 全量

    headers = {**base_headers, "Content-Length": str(size)}
    return StreamingResponse(
        file_service.iter_object(storage, obj, 0, size),
        status_code=200, media_type="application/octet-stream", headers=headers,
    )


@router.delete("/{file_id}")
async def trash(file_id: int, user: CurrentUser, db: DB) -> dict:
    return await file_service.trash_file(db, user, file_id)
