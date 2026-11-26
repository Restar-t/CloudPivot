"""分片上传服务（M6 优级核心）：init / parts / status / complete / cancel / 秒传。

锁序（统一，PLAN §2.3-4）：空间额度行 → 目标文件行 → 任务行 → 对象行。
提交事务内重检：角色 / generation / 取消态 / 配额（预留已覆盖大小，此处复核行存在）。
命名冲突（完成时目标已改名/移动/删除/被抢先更新）→ 409 UPLOAD_CONFLICT + 保留限时任务
（预留不释放，用户可选"重试/另存"），绝不静默覆盖。

秒传边界（T13）：init 仅在"本人个人空间存在可读、非回收站引用的同内容对象"时返回 instant；
跨用户一律完整上传，提交时服务端按 (sha256,size) 物理去重。仅知他人 hash 探测不到任何存在性。
"""
import hashlib
import io
import logging
import os
import tempfile
import uuid
from datetime import timedelta

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.errors import BizError, ErrorCode
from app.core.logging import space_id_var
from app.core.time import utcnow
from app.models.file_version import FileVersion
from app.models.upload import EVENT_FILE_CREATED, EVENT_VERSION_CREATED, Outbox
from app.models.space import ROLE_EDITOR, ROLE_VIEWER, SpaceMember
from app.models.storage_object import OBJECT_STATUS_ACTIVE, StorageObject
from app.models.upload import (
    TASK_CANCELLED,
    TASK_COMPLETED,
    TASK_CREATED,
    TASK_EXPIRED,
    TASK_FAILED,
    TASK_UPLOADING,
    TASK_VERIFYING,
    TERMINAL_STATES,
    UploadPart,
    UploadTask,
)
from app.models.user import User
from app.models.user_file import FILE_STATUS_NORMAL, UserFile
from app.services.permission import ensure_role, get_personal_space
from app.services.quota_service import get_or_create_usage_locked, get_space_quota_limit

logger = logging.getLogger(__name__)


def _part_key(upload_id: str, part_no: int) -> str:
    return f"up/{upload_id}/part-{part_no:05d}"


def _merged_key(upload_id: str) -> str:
    return f"up/{upload_id}/merged"


def _expected_part_size(size: int, chunk_size: int, part_no: int, chunk_count: int) -> int:
    return chunk_size if part_no < chunk_count else size - chunk_size * (chunk_count - 1)


# ---------- init ----------
async def init_upload(
    db: AsyncSession, user: User, parent_id: int, file_name: str, size: int,
    declared_sha256: str | None,
) -> dict:
    from app.core.filename import validate_filename
    from app.services.file_service import _check_extension  # 入口白名单复用

    settings = get_settings()
    validate_filename(file_name)
    _check_extension(file_name)
    if size <= 0:
        raise BizError(ErrorCode.BAD_REQUEST, message="文件大小必须大于 0")
    if size > settings.upload_max_file_size:
        raise BizError(ErrorCode.FILE_TOO_LARGE)

    # 秒传预检（T13）：仅本人个人空间的 NORMAL 文件引用的同内容对象
    instant = None
    if declared_sha256:
        instant = await _instant_hit(db, user.id, declared_sha256, size)

    space = await get_personal_space(db, user.id)
    await ensure_role(db, user.id, space.id, ROLE_EDITOR)
    space_id_var.set(space.id)

    # 目标目录校验
    if parent_id != 0:
        parent = await db.get(UserFile, parent_id)
        if parent is None or not parent.is_folder or parent.status != FILE_STATUS_NORMAL:
            raise BizError(ErrorCode.NOT_FOUND)

    # 同名检查：存在 NORMAL 同名文件 → 新版本意图（绑定 target_file_id + generation 快照）
    existing = await db.scalar(
        select(UserFile).where(
            UserFile.space_id == space.id, UserFile.parent_id == parent_id,
            UserFile.name == file_name, UserFile.status == FILE_STATUS_NORMAL,
        )
    )
    target_file_id = existing.id if existing else None
    target_generation = existing.generation if existing else None

    # 秒传命中：直接建版本/新文件（预留不需要——内容已存在，走 used 直接计）
    if instant is not None:
        result = await _commit_instant(db, user, space.id, parent_id, file_name,
                                       target_file_id, instant, size)
        return {**result, "instant": True, "upload_id": None, "chunk_size": settings.upload_chunk_size}

    # 配额原子预留：used + reserved + size ≤ limit（行锁保证并发安全）
    limit = await get_space_quota_limit(db, space)
    usage = await get_or_create_usage_locked(db, space.id)
    if usage.used_bytes + usage.reserved_bytes + size > limit:
        raise BizError(ErrorCode.QUOTA_EXCEEDED, data={
            "quota_bytes": limit, "used_bytes": usage.used_bytes,
            "reserved_bytes": usage.reserved_bytes, "need_bytes": size,
        })
    usage.reserved_bytes += size

    chunk_size = settings.upload_chunk_size
    chunk_count = max(1, -(-size // chunk_size))  # ceil
    task = UploadTask(
        upload_id=uuid.uuid4().hex, user_id=user.id, space_id=space.id,
        parent_id=parent_id, target_file_id=target_file_id,
        target_generation=target_generation,
        file_name=file_name, declared_sha256=declared_sha256,
        size=size, reserved_bytes=size, chunk_size=chunk_size,
        chunk_count=chunk_count, status=TASK_CREATED,
        expire_at=utcnow() + timedelta(hours=settings.upload_task_ttl_hours),
    )
    db.add(task)
    await db.commit()
    await db.refresh(task)
    logger.info(
        "upload init",
        extra={"action": "upload_init", "upload_id": task.upload_id, "size": size,
               "chunks": chunk_count, "target_file": target_file_id, "user_id": user.id},
    )
    return {
        "instant": False, "upload_id": task.upload_id,
        "chunk_size": chunk_size, "chunk_count": chunk_count,
        "target_file_id": target_file_id,
        "expire_at": task.expire_at,
    }


async def _instant_hit(db: AsyncSession, user_id: int, sha256: str, size: int) -> StorageObject | None:
    """秒传对象：本人个人空间、NORMAL 文件、当前版本引用的 ACTIVE 对象。"""
    space = await get_personal_space(db, user_id)
    row = await db.execute(
        select(StorageObject)
        .join(FileVersion, FileVersion.storage_object_id == StorageObject.id)
        .join(UserFile, UserFile.id == FileVersion.file_id)
        .where(
            StorageObject.sha256 == sha256, StorageObject.size == size,
            StorageObject.status == OBJECT_STATUS_ACTIVE,
            UserFile.space_id == space.id, UserFile.status == FILE_STATUS_NORMAL,
            FileVersion.id == UserFile.current_version_id,
        )
        .limit(1)
    )
    return row.scalar_one_or_none()


async def _commit_instant(
    db: AsyncSession, user: User, space_id: int, parent_id: int, file_name: str,
    target_file_id: int | None, obj: StorageObject, size: int,
) -> dict:
    """秒传提交：不再上传字节，直接建新版本（或新文件），配额按逻辑大小计。"""
    space = await get_personal_space(db, user.id)
    limit = await get_space_quota_limit(db, space)
    usage = await get_or_create_usage_locked(db, space_id)
    if usage.used_bytes + size > limit:
        raise BizError(ErrorCode.QUOTA_EXCEEDED, data={
            "quota_bytes": limit, "used_bytes": usage.used_bytes, "need_bytes": size,
        })
    usage.used_bytes += size

    obj.ref_count += 1
    version, file = await _create_version_row(
        db, user.id, space_id, parent_id, file_name, target_file_id, obj.id, size
    )
    db.add(Outbox(
        event_type=EVENT_FILE_CREATED if target_file_id is None else EVENT_VERSION_CREATED,
        payload=f'{{"file_id": {file.id}, "version_id": {version.id}}}',
    ))
    await db.commit()
    await db.refresh(file)
    logger.info(
        "instant upload",
        extra={"action": "upload_instant", "file_id": file.id, "size": size, "user_id": user.id},
    )
    return {"file": {"id": file.id, "name": file.name, "size": size}, "version_no": version.version_no}


# ---------- 分片 ----------
async def put_part(
    db: AsyncSession, storage, user: User, upload_id: str, part_no: int,
    declared_sha256: str, data: bytes,
) -> dict:
    task = await _load_task(db, upload_id)
    await ensure_role(db, user.id, task.space_id, ROLE_EDITOR)
    if task.user_id != user.id:
        raise BizError(ErrorCode.NOT_FOUND)
    await _check_active(db, task)  # 终态/过期 → 409

    if not (1 <= part_no <= task.chunk_count):
        raise BizError(ErrorCode.BAD_REQUEST, message=f"分片号需在 1-{task.chunk_count}")
    expected_size = _expected_part_size(task.size, task.chunk_size, part_no, task.chunk_count)
    if len(data) != expected_size:
        raise BizError(ErrorCode.UPLOAD_CONFLICT, data={
            "expected_size": expected_size, "got_size": len(data),
        })

    import hashlib

    actual = hashlib.sha256(data).hexdigest()
    if actual != declared_sha256.lower():
        raise BizError(ErrorCode.CHECKSUM_MISMATCH, data={"part_no": part_no})

    # 幂等：同号已存在 → 同校验和返回原结果；不同内容 → 409
    existing = (
        await db.execute(
            select(UploadPart).where(UploadPart.task_id == task.id, UploadPart.part_no == part_no)
        )
    ).scalar_one_or_none()
    if existing is not None:
        if existing.sha256 == actual:
            return {"part_no": part_no, "etag": existing.etag, "received": await _received_count(db, task.id)}
        raise BizError(ErrorCode.UPLOAD_CONFLICT, message="同一分片号内容不同（409）",
                       data={"part_no": part_no})

    etag = await storage.put_stream(
        storage.bucket_candidate, _part_key(task.upload_id, part_no), io.BytesIO(data), len(data),
    )
    db.add(UploadPart(task_id=task.id, part_no=part_no, size=len(data), sha256=actual,
                      etag=str(etag)[:64]))
    if task.status == TASK_CREATED:
        task.status = TASK_UPLOADING
    await db.commit()
    return {"part_no": part_no, "etag": str(etag)[:64], "received": await _received_count(db, task.id)}


async def _received_count(db: AsyncSession, task_id: int) -> int:
    return int(await db.scalar(
        select(func.count()).select_from(UploadPart).where(UploadPart.task_id == task_id)
    ) or 0)


# ---------- 状态（断点续传依据：DB + MinIO 双源核对） ----------
async def task_status(db: AsyncSession, storage, user: User, upload_id: str) -> dict:
    task = await _load_task(db, upload_id)
    await ensure_role(db, user.id, task.space_id, ROLE_VIEWER)
    if task.user_id != user.id:
        raise BizError(ErrorCode.NOT_FOUND)
    await _check_active(db, task)

    received_rows = (await db.execute(
        select(UploadPart.part_no, UploadPart.size, UploadPart.sha256)
        .where(UploadPart.task_id == task.id).order_by(UploadPart.part_no)
    )).all()
    received = [r[0] for r in received_rows]

    # MinIO 双源核对：DB 记录存在但对象缺失 → 视为未收到（需要重传）
    missing_in_storage = []
    for part_no in received:
        try:
            await storage.stat(storage.bucket_candidate, _part_key(upload_id, part_no))
        except Exception:  # noqa: BLE001 — 对象缺失即需要重传
            missing_in_storage.append(part_no)
    usable = [p for p in received if p not in missing_in_storage]

    return {
        "upload_id": task.upload_id, "status": task.status,
        "file_name": task.file_name, "size": task.size,
        "chunk_size": task.chunk_size, "chunk_count": task.chunk_count,
        "received_parts": usable, "missing_parts": [
            n for n in range(1, task.chunk_count + 1) if n not in usable
        ],
        "corrupted_parts": missing_in_storage,
        "expire_at": task.expire_at,
        "result_version_id": task.result_version_id,
    }


# ---------- 完成 ----------
async def complete_upload(db: AsyncSession, storage, user: User, upload_id: str) -> dict:
    """合并 → 全量核验 → 提交事务（锁序：额度→目标文件→任务→对象）。幂等/线性化见状态机。"""
    # 任务行锁（FOR UPDATE）——取消与完成在此线性化
    task = (
        await db.execute(
            select(UploadTask).where(UploadTask.upload_id == upload_id).with_for_update()
        )
    ).scalar_one_or_none()
    if task is None:
        raise BizError(ErrorCode.NOT_FOUND)
    if task.user_id != user.id:
        raise BizError(ErrorCode.NOT_FOUND)

    # 终态语义
    if task.status == TASK_COMPLETED:
        version = await db.get(FileVersion, task.result_version_id) if task.result_version_id else None
        return {"completed": True, "duplicate": True,
                "file_id": version.file_id if version else None,
                "version_id": task.result_version_id}
    if task.status in (TASK_CANCELLED, TASK_EXPIRED, TASK_FAILED):
        raise BizError(ErrorCode.UPLOAD_CONFLICT, message=f"任务已终态（{task.status}）")
    await _expire_if_due(db, task)
    if task.status in TERMINAL_STATES:
        raise BizError(ErrorCode.UPLOAD_CONFLICT, message=f"任务已终态（{task.status}）")

    # 分片完整性
    received = (await db.execute(
        select(UploadPart.part_no).where(UploadPart.task_id == task.id)
    )).scalars().all()
    if len(received) != task.chunk_count or set(received) != set(range(1, task.chunk_count + 1)):
        raise BizError(ErrorCode.UPLOAD_CONFLICT, message="分片不完整", data={
            "chunk_count": task.chunk_count, "received": sorted(received),
        })

    task.status = TASK_VERIFYING
    await db.commit()

    # 合并：按序读分片 → 临时文件 + 全量流式 SHA-256/大小核验（不信任分片 etag 与客户端声明）
    merged_key = _merged_key(task.upload_id)
    hasher = hashlib.sha256()
    total = 0
    fd, tmp_path = tempfile.mkstemp(suffix=".merge")
    checksum_failed: BizError | None = None
    try:
        with os.fdopen(fd, "wb") as out:
            for part_no in range(1, task.chunk_count + 1):
                part_key = _part_key(task.upload_id, part_no)
                expected = _expected_part_size(task.size, task.chunk_size, part_no, task.chunk_count)
                part_bytes = 0
                async for chunk in storage.get_range(storage.bucket_candidate, part_key):
                    hasher.update(chunk)
                    total += len(chunk)
                    part_bytes += len(chunk)
                    out.write(chunk)
                if part_bytes != expected:
                    checksum_failed = BizError(ErrorCode.UPLOAD_CONFLICT, message="分片实际大小不符",
                                               data={"part_no": part_no, "expected": expected,
                                                     "actual": part_bytes})
                    break
        if checksum_failed is None and total != task.size:
            checksum_failed = BizError(ErrorCode.CHECKSUM_MISMATCH,
                                       data={"expected": task.size, "actual": total})
        actual_sha = hasher.hexdigest()
        if (checksum_failed is None and task.declared_sha256
                and actual_sha != task.declared_sha256.lower()):
            checksum_failed = BizError(ErrorCode.CHECKSUM_MISMATCH, data={
                "declared": task.declared_sha256, "actual": actual_sha,
            })
        if checksum_failed is None:
            with open(tmp_path, "rb") as fh:
                await storage.put_stream(storage.bucket_candidate, merged_key, fh, total)
    finally:
        os.unlink(tmp_path)

    if checksum_failed is not None:
        # 校验失败 → FAILED（终态）+ 释放预留（一次）
        task.status = TASK_FAILED
        usage = await get_or_create_usage_locked(db, task.space_id)
        usage.reserved_bytes -= task.reserved_bytes
        await db.commit()
        raise checksum_failed

    # 提交事务（锁序：额度→目标文件→任务→对象）
    try:
        await ensure_role(db, user.id, task.space_id, ROLE_EDITOR)            # ⓪ 角色
        usage = await get_or_create_usage_locked(db, task.space_id)          # ① 额度
        target: UserFile | None = None
        if task.target_file_id:
            target = await db.get(UserFile, task.target_file_id)              # ② 目标文件
            if target is None or target.status != FILE_STATUS_NORMAL:
                raise BizError(ErrorCode.UPLOAD_CONFLICT, message="目标文件已删除或不在正常状态")
            if task.target_generation is not None and target.generation != task.target_generation:
                raise BizError(ErrorCode.UPLOAD_CONFLICT, message="目标文件已被其他操作更新（改名/移动/新版本）")
        if task.status in (TASK_CANCELLED, TASK_EXPIRED):                    # ③ 任务（行锁内重检）
            raise BizError(ErrorCode.UPLOAD_CONFLICT, message="任务已被取消或过期")

        # 去重查找：同内容 ACTIVE 对象 → 复用（ref++），否则登记 merged
        obj = (
            await db.execute(
                select(StorageObject).where(
                    StorageObject.sha256 == actual_sha, StorageObject.size == total
                )
            )
        ).scalar_one_or_none()
        if obj is not None:
            obj.ref_count += 1
            merged_used = False
        else:
            obj = StorageObject(
                sha256=actual_sha, size=total, bucket=storage.bucket_candidate,
                object_key=merged_key, ref_count=1, status=OBJECT_STATUS_ACTIVE,
            )
            db.add(obj)
            await db.flush()
            merged_used = True

        version, file = await _create_version_row(
            db, user.id, task.space_id, task.parent_id, task.file_name,
            task.target_file_id, obj.id, total,
        )
        # 预留转已用（一次性）
        usage.used_bytes += total
        usage.reserved_bytes -= task.reserved_bytes
        task.status = TASK_COMPLETED
        task.result_version_id = version.id
        db.add(Outbox(
            event_type=EVENT_FILE_CREATED if task.target_file_id is None else EVENT_VERSION_CREATED,
            payload=f'{{"file_id": {file.id}, "version_id": {version.id}, "via": "chunked"}}',
        ))
        await db.commit()
    except BizError:
        await db.rollback()
        raise  # 409/5xxx：预留按规则保留（限时任务）
    except Exception:
        await db.rollback()
        raise

    # 提交后清理：未被引用的 merged（去重复用场景）与分片对象（尽力而为）
    if not merged_used:
        try:
            await storage.delete(storage.bucket_candidate, merged_key)
        except Exception:  # noqa: BLE001 — M7 GC 兜底
            pass
    for part_no in range(1, task.chunk_count + 1):
        try:
            await storage.delete(storage.bucket_candidate, _part_key(task.upload_id, part_no))
        except Exception:  # noqa: BLE001
            pass

    logger.info(
        "upload completed",
        extra={"action": "upload_complete", "upload_id": upload_id, "file_id": file.id,
               "size": total, "sha256": actual_sha[:16], "user_id": user.id},
    )
    return {
        "completed": True, "duplicate": False,
        "file": {"id": file.id, "name": file.name, "size": total},
        "version_id": version.id, "version_no": version.version_no, "sha256": actual_sha,
    }


# ---------- 取消 ----------
async def cancel_upload(db: AsyncSession, storage, user: User, upload_id: str) -> dict:
    task = (
        await db.execute(
            select(UploadTask).where(UploadTask.upload_id == upload_id).with_for_update()
        )
    ).scalar_one_or_none()
    if task is None:
        raise BizError(ErrorCode.NOT_FOUND)
    if task.user_id != user.id:
        raise BizError(ErrorCode.NOT_FOUND)

    if task.status == TASK_COMPLETED:
        # 完成先提交 → 取消请求返回已完成（线性化语义）
        return {"cancelled": False, "completed": True,
                "version_id": task.result_version_id}
    if task.status in (TASK_CANCELLED, TASK_EXPIRED):
        return {"cancelled": True, "duplicate": True}  # 幂等：预留已释放过

    task.status = TASK_CANCELLED
    # 预留只释放一次（终态守卫保证不再进入）
    usage = await get_or_create_usage_locked(db, task.space_id)
    usage.reserved_bytes -= task.reserved_bytes
    await db.commit()

    # 分片清理（尽力而为）
    for part_no in range(1, task.chunk_count + 1):
        try:
            await storage.delete(storage.bucket_candidate, _part_key(task.upload_id, part_no))
        except Exception:  # noqa: BLE001
            pass
    try:
        await storage.delete(storage.bucket_candidate, _merged_key(task.upload_id))
    except Exception:  # noqa: BLE001
        pass

    logger.info("upload cancelled", extra={"upload_id": upload_id, "user_id": user.id})
    return {"cancelled": True, "duplicate": False}


# ---------- 内部 ----------
async def _load_task(db: AsyncSession, upload_id: str) -> UploadTask:
    task = (await db.execute(select(UploadTask).where(UploadTask.upload_id == upload_id))).scalar_one_or_none()
    if task is None:
        raise BizError(ErrorCode.NOT_FOUND)
    return task


async def _expire_if_due(db: AsyncSession, task: UploadTask) -> None:
    """惰性过期：任何触达点发现超时非终态任务 → EXPIRED + 释放预留（一次）。"""
    if task.status in (TASK_CREATED, TASK_UPLOADING, TASK_VERIFYING) and task.expire_at <= utcnow():
        task.status = TASK_EXPIRED
        usage = await get_or_create_usage_locked(db, task.space_id)
        usage.reserved_bytes -= task.reserved_bytes
        await db.commit()
        logger.info("upload expired", extra={"upload_id": task.upload_id})


async def _check_active(db: AsyncSession, task: UploadTask) -> None:
    if task.status in TERMINAL_STATES:
        raise BizError(ErrorCode.UPLOAD_CONFLICT, message=f"任务已终态（{task.status}）")
    await _expire_if_due(db, task)
    if task.status in TERMINAL_STATES:
        raise BizError(ErrorCode.UPLOAD_CONFLICT, message=f"任务已终态（{task.status}）")


async def _create_version_row(
    db: AsyncSession, user_id: int, space_id: int, parent_id: int, name: str,
    target_file_id: int | None, object_id: int, size: int,
) -> tuple[FileVersion, UserFile]:
    if target_file_id is not None:
        file = await db.get(UserFile, target_file_id)
        max_no = await db.scalar(
            select(func.max(FileVersion.version_no)).where(FileVersion.file_id == target_file_id)
        )
        version = FileVersion(
            file_id=target_file_id, version_no=int(max_no or 0) + 1,
            storage_object_id=object_id, size=size, created_by=user_id,
        )
        db.add(version)
        await db.flush()
        file.current_version_id = version.id
        file.size = size
        file.generation += 1
        return version, file
    file = UserFile(
        space_id=space_id, parent_id=parent_id, name=name, is_folder=False,
        status=FILE_STATUS_NORMAL, alive=1, generation=0, size=size,
    )
    db.add(file)
    await db.flush()
    version = FileVersion(
        file_id=file.id, version_no=1, storage_object_id=object_id,
        size=size, created_by=user_id,
    )
    db.add(version)
    await db.flush()
    file.current_version_id = version.id
    return version, file
