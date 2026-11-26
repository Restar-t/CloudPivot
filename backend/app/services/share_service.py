"""分享服务（M5）：成员侧创建/列表/取消 + 访客侧授权与访问网关。

安全语义（PLAN §2.5，T04/T06 的实现依据）：
- 创建：ADMIN+；随机 uuid4 token 防枚举；提取码 bcrypt 存储；有效期/次数可选；
- 访客授权：独立于空间成员体系；错误码按 token+IP 计数防爆破（Redis，5 次锁 15 分钟）；
  名额扣减用原子条件更新（visit_count < max_visits），幂等键重试不重复扣；
- 每次数据请求实时校验五件事：分享状态 / 有效期 / 次数（会话已计则放行）/
  文件状态 NORMAL / 创建者仍有 ADMIN+ 分享权限；auth_epoch 不匹配 → 拒绝；
- 会话绑定版本：历史会话只能读取创建时的版本；
- 传输中周期复核（间隔配置化）：下载流生成器内按 chunk 计数近似间隔执行。
"""
import logging
import uuid
from datetime import timedelta

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.errors import BizError, ErrorCode
from app.core.logging import space_id_var
from app.core.security import hash_password, verify_password
from app.core.time import utcnow
from app.models.file_version import FileVersion
from app.models.share import (
    SHARE_STATUS_ACTIVE,
    SHARE_STATUS_CANCELLED,
    Share,
    ShareSession,
)
from app.models.space import ROLE_ADMIN, SpaceMember
from app.models.storage_object import OBJECT_STATUS_ACTIVE, StorageObject
from app.models.user import User
from app.models.user_file import FILE_STATUS_NORMAL, UserFile

logger = logging.getLogger(__name__)

_CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # 去 0/O/1/I 的易读字母表
SAFE_CODE_LENGTH = 4


def generate_extract_code() -> str:
    import secrets

    return "".join(secrets.choice(_CODE_ALPHABET) for _ in range(SAFE_CODE_LENGTH))


# ---------- 成员侧 ----------
async def create_share(
    db: AsyncSession, user: User, file_id: int,
    extract_code: str | None, expire_days: int | None, max_visits: int | None,
) -> dict:
    row = await db.get(UserFile, file_id)
    if row is None or row.is_folder or row.status != FILE_STATUS_NORMAL:
        raise BizError(ErrorCode.NOT_FOUND)
    # 分享要求 ADMIN+（OWNER 天然满足；个人空间即属主）
    membership = (
        await db.execute(
            select(SpaceMember).where(
                SpaceMember.space_id == row.space_id, SpaceMember.user_id == user.id
            )
        )
    ).scalar_one_or_none()
    if membership is None or membership.role not in (ROLE_ADMIN, "OWNER"):
        raise BizError(ErrorCode.FORBIDDEN, message="分享需要 ADMIN 及以上权限")

    code = (extract_code or generate_extract_code()).strip()
    if not (4 <= len(code) <= 16):
        raise BizError(ErrorCode.BAD_REQUEST, message="提取码长度需为 4-16 位")

    expire_at = utcnow() + timedelta(days=expire_days) if expire_days else None
    share = Share(
        share_token=uuid.uuid4().hex,
        file_id=file_id,
        owner_id=user.id,
        extract_code_hash=hash_password(code),
        max_visits=max_visits,
        expire_at=expire_at,
        status=SHARE_STATUS_ACTIVE,
        auth_epoch=1,
    )
    db.add(share)
    await db.commit()
    await db.refresh(share)
    logger.info(
        "share created",
        extra={"action": "share_create", "share_id": share.id, "file_id": file_id,
               "max_visits": max_visits, "expire_days": expire_days, "user_id": user.id},
    )
    return {
        "id": share.id, "token": share.share_token, "extract_code": code,
        "max_visits": share.max_visits, "expire_at": share.expire_at,
        "file_name": row.name, "file_size": row.size,
    }


async def list_shares(db: AsyncSession, user: User) -> list[dict]:
    rows = (await db.execute(
        select(Share).where(Share.owner_id == user.id).order_by(Share.created_at.desc())
    )).scalars().all()
    out = []
    for s in rows:
        file = await db.get(UserFile, s.file_id)
        out.append({
            "id": s.id, "token": s.share_token,
            "file_name": file.name if file else "（已删除）",
            "file_size": file.size if file else 0,
            "file_exists": bool(file and file.status == FILE_STATUS_NORMAL),
            "visit_count": s.visit_count, "max_visits": s.max_visits,
            "expire_at": s.expire_at, "status": s.status,
            "last_visit_at": s.last_visit_at, "created_at": s.created_at,
        })
    return out


async def cancel_share(db: AsyncSession, user: User, share_id: int) -> dict:
    share = await db.get(Share, share_id)
    if share is None or share.owner_id != user.id:
        raise BizError(ErrorCode.NOT_FOUND)
    if share.status == SHARE_STATUS_CANCELLED:
        return {"cancelled": True, "auth_epoch": share.auth_epoch}  # 幂等
    share.status = SHARE_STATUS_CANCELLED
    share.auth_epoch += 1  # 旧会话全拒的开关
    await db.commit()
    logger.info(
        "share cancelled",
        extra={"action": "share_cancel", "share_id": share.id, "epoch": share.auth_epoch},
    )
    return {"cancelled": True, "auth_epoch": share.auth_epoch}


# ---------- 访客侧 ----------
async def _fail_count(redis, token: str, ip: str) -> None:
    """错码计数：token+IP 维度，5 次锁 15 分钟（与登录爆破同参）。"""
    settings = get_settings()
    key = f"share:fail:{token}:{ip}"
    count = await redis.incr(key)
    if count == 1:
        await redis.expire(key, settings.login_fail_window_seconds)
    if count >= settings.login_fail_threshold:
        ttl = min(60 * (2 ** (count - settings.login_fail_threshold)), settings.login_lock_max_seconds)
        await redis.set(f"share:lock:{token}:{ip}", "1", ex=ttl)


async def auth_share(
    db: AsyncSession, redis, token: str, extract_code: str,
    ip: str, idempotency_key: str | None,
) -> dict:
    lock_key = f"share:lock:{token}:{ip}"
    if await redis.exists(lock_key):
        raise BizError(ErrorCode.LOGIN_LOCKED, message="尝试次数过多，请稍后再试")

    share = (await db.execute(select(Share).where(Share.share_token == token))).scalar_one_or_none()
    # 统一 404 口径：不存在 / 已取消 / 已过期 都不给区分信息（防枚举）
    now = utcnow()
    if share is None or share.status != SHARE_STATUS_ACTIVE:
        raise BizError(ErrorCode.SHARE_NOT_FOUND)
    if share.expire_at is not None and share.expire_at <= now:
        raise BizError(ErrorCode.SHARE_NOT_FOUND)

    if not verify_password(extract_code, share.extract_code_hash):
        await _fail_count(redis, token, ip)
        raise BizError(ErrorCode.EXTRACT_CODE_WRONG)

    await redis.delete(f"share:fail:{token}:{ip}")

    file = await db.get(UserFile, share.file_id)
    if file is None or file.status != FILE_STATUS_NORMAL:
        raise BizError(ErrorCode.SHARE_NOT_FOUND)
    version = await db.get(FileVersion, file.current_version_id) if file.current_version_id else None
    if version is None:
        raise BizError(ErrorCode.INTERNAL, message="文件版本缺失", http_status=500)

    # 幂等重试：同 share + 同幂等键 → 返回原会话（不重复扣名额）
    if idempotency_key:
        existing = (
            await db.execute(
                select(ShareSession).where(
                    ShareSession.share_id == share.id,
                    ShareSession.idempotency_key == idempotency_key,
                )
            )
        ).scalar_one_or_none()
        if existing is not None and existing.expires_at > now:
            return await _auth_result(db, share, existing)

    # 名额原子扣减（同时承担统计计数）：无条件计数；有上限时 WHERE 子句原子约束
    #   UPDATE ... SET visit_count=visit_count+1 WHERE id=? AND visit_count < max_visits
    now = utcnow()
    if share.max_visits is not None:
        cond = (
            update(Share)
            .where(Share.id == share.id, Share.visit_count < share.max_visits)
            .values(visit_count=share.visit_count + 1, last_visit_at=now)
        )
    else:
        cond = update(Share).where(Share.id == share.id).values(
            visit_count=share.visit_count + 1, last_visit_at=now
        )
    res = await db.execute(cond)
    if res.rowcount == 0:
        raise BizError(ErrorCode.SHARE_EXHAUSTED)

    ttl = timedelta(hours=get_settings().share_session_ttl_hours)
    session = ShareSession(
        share_id=share.id,
        session_token=uuid.uuid4().hex,
        version_id=version.id,
        auth_epoch=share.auth_epoch,
        idempotency_key=idempotency_key,
        counted=1,  # 每个会话固定消耗一次"访问"（统计口径：下载会话数）
        expires_at=now + ttl,
    )
    db.add(session)
    await db.commit()
    await db.refresh(session)
    return await _auth_result(db, share, session)


async def _auth_result(db: AsyncSession, share: Share, session: ShareSession) -> dict:
    file = await db.get(UserFile, share.file_id)
    return {
        "session_token": session.session_token,
        "expires_at": session.expires_at,
        "file": {
            "id": share.file_id, "name": file.name if file else "（已删除）",
            "size": file.size if file else 0,
            "updated_at": file.updated_at if file else None,
        },
    }


async def validate_session(db: AsyncSession, token: str, session_token: str) -> tuple[Share, ShareSession, FileVersion, UserFile]:
    """五点实时校验（每次数据请求都执行）：返回 (share, session, version, file)。"""
    share = (await db.execute(select(Share).where(Share.share_token == token))).scalar_one_or_none()
    if share is None or share.status != SHARE_STATUS_ACTIVE:
        raise BizError(ErrorCode.SHARE_NOT_FOUND)                      # ① 状态
    now = utcnow()
    if share.expire_at is not None and share.expire_at <= now:
        raise BizError(ErrorCode.SHARE_NOT_FOUND)                      # ② 有效期

    session = (
        await db.execute(select(ShareSession).where(ShareSession.session_token == session_token))
    ).scalar_one_or_none()
    if session is None or session.share_id != share.id:
        raise BizError(ErrorCode.SHARE_NOT_FOUND)
    if session.expires_at <= now:
        raise BizError(ErrorCode.SHARE_NOT_FOUND)                      # ③ 会话有效期
    if session.auth_epoch != share.auth_epoch:
        raise BizError(ErrorCode.SHARE_NOT_FOUND)                      # ④ epoch（撤销后全拒）

    file = await db.get(UserFile, share.file_id)
    if file is None or file.status != FILE_STATUS_NORMAL:
        raise BizError(ErrorCode.SHARE_NOT_FOUND)                      # ⑤a 文件状态
    membership = (
        await db.execute(
            select(SpaceMember.role).where(
                SpaceMember.space_id == file.space_id, SpaceMember.user_id == share.owner_id
            )
        )
    ).scalar_one_or_none()
    if membership not in (ROLE_ADMIN, "OWNER"):
        raise BizError(ErrorCode.SHARE_NOT_FOUND)                      # ⑤b 创建者分享权限

    version = await db.get(FileVersion, session.version_id)
    if version is None or version.file_id != file.id:
        raise BizError(ErrorCode.INTERNAL, message="分享版本缺失", http_status=500)
    obj = await db.get(StorageObject, version.storage_object_id)
    if obj is None or obj.status != OBJECT_STATUS_ACTIVE:
        raise BizError(ErrorCode.INTERNAL, message="分享内容缺失", http_status=500)
    space_id_var.set(file.space_id)
    return share, session, version, file


def transfer_guard(storage, share: Share, session: ShareSession):
    """传输中周期复核：返回包装器——每 8 个 chunk 检查一次时间间隔，到期重跑五点校验，
    失效即中断流（撤销语义增强项）。校验用独立短 DB 会话，不占用请求会话。
    """
    from app.db.session import get_session_factory

    interval = get_settings().share_transfer_recheck_seconds
    state = {"chunks": 0, "last": utcnow()}

    async def check():
        if (utcnow() - state["last"]).total_seconds() < interval:
            return
        state["last"] = utcnow()
        factory = get_session_factory()
        async with factory() as db:
            await validate_session(db, share.share_token, session.session_token)

    async def wrapped(chunk_iter):
        async for chunk in chunk_iter:
            yield chunk
            state["chunks"] += 1
            if state["chunks"] % 8 == 0:
                await check()

    return wrapped
