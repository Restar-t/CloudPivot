"""后台一致性循环（M7.2/M7.3）：outbox 调度 + 对账/GC。

降级说明（PLAN §七 预案）：以 FastAPI lifespan 内的 asyncio 周期任务实现
"任务表轮询 + 对账"，未引入独立 Celery 进程；重试上限/幂等/告警语义与计划一致，
取舍已记入 docs/known-limitations.md（触发止损条件：单机部署，无需分布式队列）。

outbox：按 id 顺序取 PENDING → 交付（消费者注册表，M8 填充缩略图/索引；当前空表=直接置
DONE）→ 异常 retry_count+1，达上限置 FAILED + 告警日志；消费者按 event id 幂等。

对账/GC（每轮全部执行，异常互不影响）：
1. 过期上传任务 → EXPIRED + 释放预留（恰一次）；
2. 回收站超保留期 → 自动彻底删除（30 天，配置化）；
3. candidate 桶孤儿对象清理（无对象行引用 且 无活跃上传任务，超宽限期）；
4. ref_count 与版本引用核对：偏差修复 + 告警；被版本引用但对象行缺失 → 告警不掩盖；
5. DELETING 残留重试物理删除 → DELETED。
"""
import asyncio
import json
import logging
from datetime import timedelta

from sqlalchemy import func, select

from app.core.config import get_settings
from app.core.time import utcnow
from app.models.file_version import FileVersion
from app.models.upload import OUTBOX_DONE, OUTBOX_FAILED, OUTBOX_PENDING, Outbox
from app.models.quota_usage import QuotaUsage
from app.models.share import SHARE_STATUS_ACTIVE, Share
from app.models.storage_object import OBJECT_STATUS_ACTIVE, OBJECT_STATUS_DELETING, StorageObject
from app.models.upload import (
    TASK_CREATED,
    TASK_UPLOADING,
    TASK_VERIFYING,
    UploadTask,
)
from app.models.user_file import FILE_STATUS_TRASH, UserFile

logger = logging.getLogger(__name__)

_outbox_consumers: dict[str, object] = {}  # event_type -> async fn(event_id, payload)（M8 注册）
MAX_RETRY = 5
_GRACE_MINUTES = 10  # DELETING 残留重试最短间隔（演示可收紧）


def register_consumer(event_type: str, handler) -> None:
    """M8 注册消费者（缩略图/索引）；当前为占位。"""
    _outbox_consumers[event_type] = handler


async def dispatch_outbox_once(db) -> dict:
    rows = (await db.execute(
        select(Outbox).where(Outbox.status == OUTBOX_PENDING).order_by(Outbox.id).limit(50)
    )).scalars().all()
    done = failed = 0
    for row in rows:
        handler = _outbox_consumers.get(row.event_type)
        try:
            if handler is not None:
                payload = json.loads(row.payload) if row.payload else {}
                await handler(row.id, payload)  # 消费者按 event id 幂等
            row.status = OUTBOX_DONE
            done += 1
        except Exception as exc:  # noqa: BLE001 — 重试上限内继续
            row.retry_count += 1
            if row.retry_count >= MAX_RETRY:
                row.status = OUTBOX_FAILED
                failed += 1
                logger.error(
                    "outbox event failed permanently",
                    extra={"event_id": row.id, "event_type": row.event_type, "error": str(exc)},
                )
            else:
                logger.warning(
                    "outbox event delivery retry",
                    extra={"event_id": row.id, "retry": row.retry_count, "error": str(exc)},
                )
    await db.commit()
    return {"done": done, "failed": failed}


async def reconcile_once(db, storage) -> dict:
    report = {}
    report["expired_tasks"] = await _expire_upload_tasks(db)
    report["trash_retention"] = await _purge_expired_trash(db, storage)
    report["orphan_candidates"] = await _collect_orphan_candidates(db, storage)
    report["ref_reconcile"] = await _reconcile_ref_counts(db)
    report["deleting_retry"] = await _retry_deleting(db, storage)
    return report


async def _expire_upload_tasks(db) -> int:
    now = utcnow()
    rows = (await db.execute(
        select(UploadTask).where(
            UploadTask.status.in_([TASK_CREATED, TASK_UPLOADING, TASK_VERIFYING]),
            UploadTask.expire_at <= now,
        )
    )).scalars().all()
    count = 0
    for task in rows:
        task.status = "EXPIRED"
        usage = (await db.execute(
            select(QuotaUsage).where(QuotaUsage.space_id == task.space_id)
        )).scalar_one_or_none()
        if usage is not None:
            usage.reserved_bytes = max(usage.reserved_bytes - task.reserved_bytes, 0)
        count += 1
    if count:
        await db.commit()
        logger.warning("expired upload tasks released", extra={"count": count})
    return count


async def _purge_expired_trash(db, storage) -> int:
    settings = get_settings()
    deadline = utcnow() - timedelta(days=settings.trash_retention_days)
    rows = (await db.execute(
        select(UserFile).where(UserFile.status == FILE_STATUS_TRASH, UserFile.deleted_at <= deadline)
    )).scalars().all()
    # 仅顶层（父链无更早 TRASH 目录），子树由目录释放
    count = 0
    for row in rows:
        if await _has_trash_ancestor(db, row):
            continue
        from app.services.trash_service import _release_tree

        released = await _release_tree(db, storage, await db.get(UserFile, row.id))
        count += 1
        for bucket, key in released["purged_objects"]:
            try:
                await storage.delete(bucket, key)
            except Exception as exc:  # noqa: BLE001
                logger.error("retention purge delete failed", extra={"key": key, "error": str(exc)})
    if count:
        await db.commit()
        logger.warning("trash retention purged", extra={"roots": count})
    return count


async def _has_trash_ancestor(db, row) -> bool:
    from app.models.user_file import ROOT_PARENT_ID

    cur = row.parent_id
    for _ in range(256):
        if cur == ROOT_PARENT_ID:
            return False
        parent = await db.get(UserFile, cur)
        if parent is None:
            return False
        if parent.status == FILE_STATUS_TRASH:
            return True
        cur = parent.parent_id
    return False


async def _collect_orphan_candidates(db, storage) -> int:
    """candidate 桶孤儿清理：key 不被任何对象行引用、且所属任务不活跃、超宽限期 → 删除。"""
    settings = get_settings()
    grace = timedelta(hours=settings.candidate_grace_hours)
    now = utcnow()
    deleted = 0
    try:
        objects = storage.list_objects(storage.bucket_candidate)  # 异步生成器：(key, last_modified)
    except AttributeError:
        return 0  # 测试替身未实现 list_objects 时跳过
    active_prefixes = set()
    tasks = (await db.execute(
        select(UploadTask.upload_id).where(
            UploadTask.status.in_([TASK_CREATED, TASK_UPLOADING, TASK_VERIFYING])
        )
    )).scalars().all()
    for uid in tasks:
        active_prefixes.add(f"up/{uid}/")
    referenced_keys = set((await db.execute(
        select(StorageObject.object_key).where(StorageObject.status.in_([OBJECT_STATUS_ACTIVE, OBJECT_STATUS_DELETING]))
    )).scalars().all())

    async for key, last_modified in objects:
        if key in referenced_keys:
            continue
        if any(key.startswith(p) for p in active_prefixes):
            continue
        lm = last_modified or now
        if now - lm < grace:
            continue
        try:
            await storage.delete(storage.bucket_candidate, key)
            deleted += 1
        except Exception as exc:  # noqa: BLE001
            logger.warning("orphan delete failed", extra={"key": key, "error": str(exc)})
    if deleted:
        logger.warning("orphan candidates collected", extra={"count": deleted})
    return deleted


async def _reconcile_ref_counts(db) -> dict:
    """ref_count 与版本引用核对：偏差修复+告警；被引用但缺失 → 告警不掩盖。"""
    mismatches = 0
    missing = 0
    obj_ids = (await db.execute(
        select(StorageObject.id, StorageObject.ref_count).where(
            StorageObject.status.in_([OBJECT_STATUS_ACTIVE, OBJECT_STATUS_DELETING])
        )
    )).all()
    for obj_id, ref in obj_ids:
        actual = await db.scalar(
            select(func.count()).select_from(FileVersion).where(FileVersion.storage_object_id == obj_id)
        )
        if actual != ref:
            obj = await db.get(StorageObject, obj_id)
            logger.error(
                "ref count mismatch — fixed",
                extra={"object_id": obj_id, "stored": ref, "actual": actual},
            )
            obj.ref_count = actual
            mismatches += 1
    # 被版本引用但对象行缺失（严重不一致：只告警，不删版本行掩盖）
    versioned_ids = (await db.execute(
        select(FileVersion.storage_object_id).distinct()
    )).scalars().all()
    for oid in set(versioned_ids):
        if await db.get(StorageObject, oid) is None:
            logger.error("referenced storage_object missing — DO NOT mask", extra={"object_id": oid})
            missing += 1
    if mismatches or missing:
        await db.commit()
    return {"mismatches_fixed": mismatches, "missing_alerted": missing}


async def _retry_deleting(db, storage) -> int:
    deadline = utcnow() - timedelta(minutes=_GRACE_MINUTES)
    rows = (await db.execute(
        select(StorageObject).where(
            StorageObject.status == OBJECT_STATUS_DELETING, StorageObject.created_at <= deadline
        )
    )).scalars().all()
    count = 0
    for obj in rows:
        try:
            await storage.delete(obj.bucket, obj.object_key)
        except Exception as exc:  # noqa: BLE001 — 下轮继续
            logger.warning("deleting retry failed", extra={"key": obj.object_key, "error": str(exc)})
            continue
        obj.status = "DELETED"
        count += 1
    if count:
        await db.commit()
    return count


# ---------- 生命周期托管 ----------
_tasks: dict[str, asyncio.Task] = {}


async def start_background_loops(app) -> None:
    settings = get_settings()
    interval = settings.background_interval_seconds

    async def outbox_loop():
        while True:
            try:
                from app.db.session import get_session_factory

                async with get_session_factory()() as db:
                    await dispatch_outbox_once(db)
            except Exception:  # noqa: BLE001
                logger.exception("outbox loop iteration failed")
            await asyncio.sleep(min(interval, 15))

    async def reconcile_loop():
        while True:
            await asyncio.sleep(interval)
            try:
                from app.db.session import get_session_factory

                async with get_session_factory()() as db:
                    report = await reconcile_once(db, app.state.storage)
                    if any(v for v in report.values()):
                        logger.info("reconcile cycle", extra=report)
            except Exception:  # noqa: BLE001
                logger.exception("reconcile loop iteration failed")

    _tasks["outbox"] = asyncio.create_task(outbox_loop())
    _tasks["reconcile"] = asyncio.create_task(reconcile_loop())
    logger.info("background loops started", extra={"interval": interval})


async def stop_background_loops() -> None:
    for name, task in _tasks.items():
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        logger.info("background loop stopped", extra={"loop": name})
    _tasks.clear()
