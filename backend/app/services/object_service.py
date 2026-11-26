"""物理对象引用服务（M7.4）：统一的复用/复活/登记入口 + 两阶段删除。

- resolve_or_register：上传去重的唯一入口。
  ACTIVE 同内容 → 复用（ref++）；仅 DELETING/DELETED 同内容 → 复活（行转 ACTIVE，
  指向本次上传的新 key/桶，ref=1）；都没有 → 登记新行。活跃唯一键 (sha256,size)
  使得任意时刻同内容至多一行，三态切换都在该行上进行。
- 两阶段删除：零引用 → 置 DELETING（提交，阻止新引用）→ 物理删 MinIO → 行转 DELETED
  （保留供审计/复活；物理删除失败留在 DELETING 由对账循环重试）。
"""
import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.storage_object import OBJECT_STATUS_ACTIVE, OBJECT_STATUS_DELETED, OBJECT_STATUS_DELETING, StorageObject

logger = logging.getLogger(__name__)


async def find_object(db: AsyncSession, sha256: str, size: int) -> StorageObject | None:
    """同内容对象行（任意状态；唯一键保证至多一行）。"""
    return (
        await db.execute(
            select(StorageObject).where(StorageObject.sha256 == sha256, StorageObject.size == size)
        )
    ).scalar_one_or_none()


async def resolve_or_register(
    db: AsyncSession, sha256: str, size: int, bucket: str, key: str
) -> tuple[StorageObject, bool]:
    """返回 (对象行, 是否复用既有字节)。DELETING 行等待策略：直接复活为新 key（见模块说明）。"""
    obj = await find_object(db, sha256, size)
    if obj is not None and obj.status == OBJECT_STATUS_ACTIVE:
        obj.ref_count += 1
        return obj, True
    if obj is not None:
        # DELETING/DELETED 行复活：旧 key 的字节已（将）被删，新行必须指向本次上传的新 key
        revived = obj.status == OBJECT_STATUS_DELETING
        obj.status = OBJECT_STATUS_ACTIVE
        obj.bucket = bucket
        obj.object_key = key
        obj.ref_count = 1
        logger.info(
            "object revived",
            extra={"object_id": obj.id, "from_status": "DELETING" if revived else "DELETED", "key": key},
        )
        return obj, False
    fresh = StorageObject(
        sha256=sha256, size=size, bucket=bucket, object_key=key,
        ref_count=1, status=OBJECT_STATUS_ACTIVE,
    )
    db.add(fresh)
    await db.flush()
    return fresh, False


async def release_object(db: AsyncSession, obj: StorageObject) -> str | None:
    """版本引用释放：ref--；归零 → DELETING（返回待物理删除的 (bucket,key)，None=仍被引用）。

    调用方须在事务提交后执行物理删除，并调用 mark_deleted/保留 DELETING 由对账重试。
    """
    obj.ref_count -= 1
    if obj.ref_count > 0:
        return None
    obj.status = OBJECT_STATUS_DELETING
    return obj.bucket, obj.object_key


async def mark_deleted(db: AsyncSession, obj: StorageObject) -> None:
    obj.status = OBJECT_STATUS_DELETED
    await db.commit()


async def active_object(db: AsyncSession, object_id: int) -> StorageObject | None:
    obj = await db.get(StorageObject, object_id)
    if obj is None or obj.status != OBJECT_STATUS_ACTIVE:
        return None
    return obj
