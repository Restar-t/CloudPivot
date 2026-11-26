"""文件接口：rename / upload / download / delete（M2-M3）+ move / copy / batch-delete /
zip / search（M4）。下载与 ZIP 一律走应用网关（PLAN §2.5）。"""
from fastapi import APIRouter, Query, Request
from fastapi.responses import StreamingResponse

from app.core.errors import BizError, ErrorCode
from app.core.response import ApiRoute
from app.deps import CurrentUser, DB, StorageDep
from app.schemas.file import (
    BatchOpOut,
    CopyRequest,
    FileItemOut,
    IdsRequest,
    MoveRequest,
    PageOut,
    RenameRequest,
    UploadOut,
)
from app.services import file_ops_service, file_service, folder_service, preview_service, version_service, zip_service

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


@router.get("/{file_id}/preview")
async def preview_file(file_id: int, user: CurrentUser, db: DB, storage: StorageDep):
    """成员预览：魔数识别 + 类型决策（图片/PDF/文本 inline；SVG/HTML 强制下载）。"""
    from app.core.config import get_settings

    file, obj, version, etag = await file_service.download_file(db, storage, user, file_id)
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
    common = {
        "Cache-Control": "private, no-store",
        "X-Content-Type-Options": "nosniff",
        "X-Preview-Reason": decision.reason,
        "ETag": etag,
    }
    if decision.disposition == "attachment":
        return StreamingResponse(
            file_service.iter_object(storage, obj, 0, size),
            status_code=200, media_type=decision.content_type,
            headers={**common, "Content-Disposition": file_service.content_disposition(file.name),
                     "Content-Length": str(size)},
        )
    return StreamingResponse(
        file_service.iter_object(storage, obj, 0, size),
        status_code=200,
        headers={
            **common,
            "Content-Type": decision.content_type,
            "Content-Security-Policy": "default-src 'none'; img-src 'self'; style-src 'unsafe-inline'; sandbox",
            "Content-Length": str(size),
        },
    )


@router.get("/{file_id}/versions")
async def list_versions(file_id: int, user: CurrentUser, db: DB) -> dict:
    return await version_service.list_versions(db, user, file_id)


@router.post("/{file_id}/versions/{version_no}/restore")
async def restore_version(file_id: int, version_no: int, user: CurrentUser, db: DB) -> dict:
    return await version_service.restore_version(db, user, file_id, version_no)


@router.delete("/{file_id}/versions/{version_no}")
async def delete_version(
    file_id: int, version_no: int, user: CurrentUser, db: DB, storage: StorageDep
) -> dict:
    return await version_service.delete_version(db, storage, user, file_id, version_no)


@router.delete("/{file_id}")
async def trash(file_id: int, user: CurrentUser, db: DB) -> dict:
    return await file_service.trash_file(db, user, file_id)


# ---------- M4：移动 / 复制 / 批量 / ZIP / 搜索 ----------
@router.post("/move")
async def move(body: MoveRequest, user: CurrentUser, db: DB) -> BatchOpOut:
    return BatchOpOut(results=await file_ops_service.move_files(db, user, body.file_ids, body.target_folder_id))


@router.post("/copy")
async def copy(body: CopyRequest, user: CurrentUser, db: DB) -> BatchOpOut:
    return BatchOpOut(results=await file_ops_service.copy_files(db, user, body.file_ids, body.target_folder_id))


@router.post("/batch-delete")
async def batch_delete(body: IdsRequest, user: CurrentUser, db: DB) -> BatchOpOut:
    return BatchOpOut(results=await file_ops_service.batch_trash(db, user, body.file_ids))


@router.get("/zip")
async def zip_download(
    ids: str, request: Request, user: CurrentUser, db: DB, storage: StorageDep
):
    """批量打包下载：ids=逗号分隔文件 id；逐文件鉴权预校验后流式输出。"""
    try:
        file_ids = [int(x) for x in ids.split(",") if x.strip()]
    except ValueError:
        raise BizError(ErrorCode.BAD_REQUEST, message="ids 必须为逗号分隔的文件 id")
    plan = await zip_service.build_plan(db, user, file_ids)
    filename = "批量下载.zip"
    headers = {
        "Content-Disposition": file_service.content_disposition(filename),
        "Cache-Control": "private, no-store",
        "X-Content-Type-Options": "nosniff",
    }
    return StreamingResponse(
        zip_service.zip_stream(storage, plan),
        status_code=200, media_type="application/zip", headers=headers,
    )


@router.get("/search")
async def search(
    q: str = Query(default="", max_length=100),
    user: CurrentUser = None, db: DB = None,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    sort: str = Query(default="name", pattern="^(name|size|uploaded_at)$"),
    order: str = Query(default="asc", pattern="^(asc|desc)$"),
) -> PageOut:
    rows, total = await file_ops_service.search_files(db, user, q.strip(), page, page_size, sort, order)
    return PageOut(
        items=[FileItemOut.model_validate(r) for r in rows],
        total=total, page=page, page_size=page_size,
    )
