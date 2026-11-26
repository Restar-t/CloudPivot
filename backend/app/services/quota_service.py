"""配额服务：事务内增量维护 used_bytes，校验失败抛 5xxx。

口径（PLAN §2.2）：配额 = 空间内所有保留版本逻辑大小之和（含回收站）；
个人空间额度取 owner 的 user.quota_bytes；行懒创建，变更前 FOR UPDATE 加锁。
"""
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import BizError, ErrorCode
from app.models.quota_usage import QuotaUsage
from app.models.space import ROLE_OWNER, Space
from app.models.user import User


async def get_space_quota_limit(db: AsyncSession, space: Space) -> int:
    """空间额度：个人空间取 owner 的 user.quota_bytes（团队空间独立额度，M8 落库）。"""
    owner = await db.get(User, space.owner_id)
    if owner is None:
        raise BizError(ErrorCode.INTERNAL, message="空间所有者缺失", http_status=500)
    return int(owner.quota_bytes)


async def get_or_create_usage_locked(db: AsyncSession, space_id: int) -> QuotaUsage:
    """取用量行并加锁（FOR UPDATE）；不存在则创建。必须在事务中调用。"""
    usage = await db.execute(
        select(QuotaUsage).where(QuotaUsage.space_id == space_id).with_for_update()
    )
    row = usage.scalar_one_or_none()
    if row is None:
        row = QuotaUsage(space_id=space_id, used_bytes=0, reserved_bytes=0)
        db.add(row)
        await db.flush()
    return row


async def check_and_add_usage(db: AsyncSession, space_id: int, limit: int, delta: int) -> None:
    """事务内校验 used+delta <= limit 并累加；超限抛 5001（HTTP 507）。"""
    usage = await get_or_create_usage_locked(db, space_id)
    if usage.used_bytes + delta > limit:
        raise BizError(ErrorCode.QUOTA_EXCEEDED, data={
            "quota_bytes": limit, "used_bytes": usage.used_bytes, "need_bytes": delta,
        })
    usage.used_bytes += delta


async def add_usage(db: AsyncSession, space_id: int, delta: int) -> None:
    """不加额度检查的纯累加/扣减（删除释放等内部场景）。"""
    usage = await get_or_create_usage_locked(db, space_id)
    usage.used_bytes += delta


async def get_usage_summary(db: AsyncSession, space_id: int, limit: int) -> dict:
    usage = await db.execute(select(QuotaUsage).where(QuotaUsage.space_id == space_id))
    row = usage.scalar_one_or_none()
    used = int(row.used_bytes) if row else 0
    return {"quota_bytes": limit, "used_bytes": used, "remaining_bytes": max(limit - used, 0)}


__all__ = [
    "get_space_quota_limit",
    "get_or_create_usage_locked",
    "check_and_add_usage",
    "add_usage",
    "get_usage_summary",
    "ROLE_OWNER",
]
