"""认证服务：注册 / 登录（爆破防护）/ 刷新轮换 + 重用检测 / 退出 / 改密。

Redis 键约定：
- login:fail:{username}:{ip}   登录失败计数（TTL = 窗口 15min）
- login:lock:{username}:{ip}   锁定标记（TTL = 渐进退避时长）
- auth:deny:{jti}              已退出 access 令牌黑名单（TTL = 剩余有效期）
- auth:min_iat:{user_id}       改密后旧 access 的最早可接受签发时间
"""
import logging
import uuid

from redis.asyncio import Redis
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.errors import BizError, ErrorCode
from app.core.logging import user_id_var
from app.core.security import (
    access_ttl_seconds,
    create_access_token,
    dummy_verify,
    generate_refresh_token,
    hash_password,
    hash_refresh_token,
    refresh_ttl,
    verify_password,
)
from app.core.time import epoch_now, utcnow
from app.models.login_log import RESULT_FAILED, RESULT_LOCKED, RESULT_SUCCESS, LoginLog
from app.models.refresh_session import (
    REASON_FAMILY_REVOKED,
    REASON_LOGOUT,
    REASON_PASSWORD_CHANGED,
    REASON_REUSE_DETECTED,
    REASON_ROTATED,
    RefreshSession,
)
from app.models.user import User
from app.schemas.auth import LoginResult, TokenPair, UserOut

logger = logging.getLogger(__name__)


def _fail_key(username: str, ip: str) -> str:
    return f"login:fail:{username.lower()}:{ip}"


def _lock_key(username: str, ip: str) -> str:
    return f"login:lock:{username.lower()}:{ip}"


def _min_iat_key(user_id: int) -> str:
    return f"auth:min_iat:{user_id}"


def _deny_key(jti: str) -> str:
    return f"auth:deny:{jti}"


async def _write_login_log(
    db: AsyncSession, username: str, ip: str, result: str, detail: str = ""
) -> None:
    db.add(LoginLog(username=username, ip=ip, result=result, detail=detail[:255]))
    await db.commit()


# ---------- 注册 ----------
async def register(db: AsyncSession, username: str, password: str) -> UserOut:
    try:
        user = User(username=username, password_hash=hash_password(password))
        db.add(user)
        await db.commit()
        await db.refresh(user)
    except IntegrityError as exc:
        await db.rollback()
        raise BizError(ErrorCode.USERNAME_EXISTS) from exc
    logger.info("user registered", extra={"action": "register", "target": username})
    return UserOut.model_validate(user)


# ---------- 登录 ----------
async def login(db: AsyncSession, redis: Redis, username: str, password: str, ip: str) -> LoginResult:
    settings = get_settings()

    if await redis.exists(_lock_key(username, ip)):
        await _write_login_log(db, username, ip, RESULT_LOCKED, "locked window")
        raise BizError(ErrorCode.LOGIN_LOCKED)

    user = (
        await db.execute(select(User).where(User.username == username))
    ).scalar_one_or_none()

    ok = verify_password(password, user.password_hash) if user else (dummy_verify(password) or False)
    if not user or not ok or not user.is_active:
        await _register_login_failure(redis, username, ip, settings.login_fail_threshold,
                                      settings.login_fail_window_seconds,
                                      settings.login_lock_max_seconds)
        await _write_login_log(db, username, ip, RESULT_FAILED,
                               "wrong password" if user else "no such user")
        # 统一口径：不区分"用户不存在"与"密码错误"，防枚举
        raise BizError(ErrorCode.BAD_CREDENTIALS)

    await redis.delete(_fail_key(username, ip))
    await _write_login_log(db, username, ip, RESULT_SUCCESS)
    user_id_var.set(user.id)
    pair = await _issue_family(db, user.id)
    return LoginResult(**pair.model_dump(), user=UserOut.model_validate(user))


async def _register_login_failure(
    redis: Redis, username: str, ip: str, threshold: int, window: int, lock_max: int
) -> None:
    """失败计数；达到阈值后按超额次数指数退避锁定（60s 起、封顶 lock_max）。"""
    key = _fail_key(username, ip)
    count = await redis.incr(key)
    if count == 1:
        await redis.expire(key, window)
    if count >= threshold:
        ttl = min(60 * (2 ** (count - threshold)), lock_max)
        await redis.set(_lock_key(username, ip), "1", ex=ttl)
        logger.warning(
            "login locked",
            extra={"action": "login_lock", "target": username, "count": count, "ttl": ttl},
        )


# ---------- 刷新会话族 ----------
async def _issue_family(db: AsyncSession, user_id: int) -> TokenPair:
    """登录时开新族；返回 token 对（刷新令牌只在响应中出现一次）。"""
    token = generate_refresh_token()
    db.add(
        RefreshSession(
            family_id=uuid.uuid4().hex,
            user_id=user_id,
            token_hash=hash_refresh_token(token),
            expires_at=utcnow() + refresh_ttl(),
        )
    )
    await db.commit()
    access, _claims = create_access_token(user_id)
    return TokenPair(access_token=access, refresh_token=token, expires_in=access_ttl_seconds())


async def _rotate_in_family(db: AsyncSession, session: RefreshSession) -> TokenPair:
    """轮换：旧行 revoke(rotated)，同族写入新行。"""
    session.revoked_at = utcnow()
    session.revoked_reason = REASON_ROTATED
    token = generate_refresh_token()
    db.add(
        RefreshSession(
            family_id=session.family_id,
            user_id=session.user_id,
            token_hash=hash_refresh_token(token),
            expires_at=utcnow() + refresh_ttl(),
        )
    )
    await db.commit()
    access, _claims = create_access_token(session.user_id)
    return TokenPair(access_token=access, refresh_token=token, expires_in=access_ttl_seconds())


async def refresh(db: AsyncSession, redis: Redis, refresh_token: str) -> TokenPair:
    token_hash = hash_refresh_token(refresh_token)
    session = (
        await db.execute(select(RefreshSession).where(RefreshSession.token_hash == token_hash))
    ).scalar_one_or_none()

    if session is None:
        raise BizError(ErrorCode.UNAUTHENTICATED, message="刷新令牌无效")

    if session.revoked_at is not None:
        # 重用检测：旧 refresh 再次出现 → 撤销整个会话族
        await _revoke_family(db, session.family_id, REASON_REUSE_DETECTED)
        logger.warning(
            "refresh token reuse detected, family revoked",
            extra={"action": "refresh_reuse", "family": session.family_id,
                   "user_id": session.user_id},
        )
        raise BizError(ErrorCode.SESSION_REVOKED, message="检测到令牌重用，会话已撤销")

    if session.expires_at <= utcnow():
        raise BizError(ErrorCode.TOKEN_EXPIRED, message="刷新令牌已过期，请重新登录")

    user = await db.get(User, session.user_id)
    if user is None or not user.is_active:
        raise BizError(ErrorCode.UNAUTHENTICATED)
    return await _rotate_in_family(db, session)


async def _revoke_family(db: AsyncSession, family_id: str, reason: str) -> None:
    await db.execute(
        update(RefreshSession)
        .where(RefreshSession.family_id == family_id, RefreshSession.revoked_at.is_(None))
        .values(revoked_at=utcnow(), revoked_reason=reason)
    )
    await db.commit()


# ---------- 退出 ----------
async def logout(
    db: AsyncSession, redis: Redis, user: User, refresh_token: str | None,
    access_jti: str, access_exp: int,
) -> None:
    """撤销当前 refresh 会话；access 令牌进 jti 黑名单（剩余有效期内在 Redis 中衰减）。"""
    if refresh_token:
        token_hash = hash_refresh_token(refresh_token)
        session = (
            await db.execute(
                select(RefreshSession).where(
                    RefreshSession.token_hash == token_hash, RefreshSession.user_id == user.id
                )
            )
        ).scalar_one_or_none()
        if session is not None and session.revoked_at is None:
            session.revoked_at = utcnow()
            session.revoked_reason = REASON_LOGOUT
            await db.commit()
    remaining = max(access_exp - epoch_now(), 1)
    await redis.set(_deny_key(access_jti), "1", ex=remaining)
    logger.info("user logged out", extra={"action": "logout", "user_id": user.id})


# ---------- 改密 ----------
async def change_password(
    db: AsyncSession, redis: Redis, user: User, old_password: str, new_password: str,
    current_refresh_token: str | None,
) -> TokenPair:
    if not verify_password(old_password, user.password_hash):
        await _write_login_log(db, user.username, "", RESULT_FAILED, "wrong old password on change")
        raise BizError(ErrorCode.BAD_CREDENTIALS, message="原密码错误")

    user.password_hash = hash_password(new_password)
    await db.flush()

    # 撤销除当前族外的全部会话；当前族稍后轮换，本设备保持登录
    current_family: str | None = None
    if current_refresh_token:
        current_family = (
            await db.execute(
                select(RefreshSession.family_id).where(
                    RefreshSession.token_hash == hash_refresh_token(current_refresh_token),
                    RefreshSession.user_id == user.id,
                )
            )
        ).scalar_one_or_none()
    cond = RefreshSession.revoked_at.is_(None)
    if current_family:
        cond = cond & (RefreshSession.family_id != current_family)
    await db.execute(
        update(RefreshSession).where(cond).values(
            revoked_at=utcnow(), revoked_reason=REASON_PASSWORD_CHANGED
        )
    )
    # 旧 access（iat 早于改密时刻）全部失效
    await redis.set(_min_iat_key(user.id), str(epoch_now()), ex=refresh_ttl().days * 86400)

    if current_family:
        active = (
            await db.execute(
                select(RefreshSession).where(
                    RefreshSession.family_id == current_family,
                    RefreshSession.revoked_at.is_(None),
                )
            )
        ).scalar_one_or_none()
        if active:
            return await _rotate_in_family(db, active)

    await db.commit()
    return await _issue_family(db, user.id)


# ---------- access 令牌屏障查询（get_current_user 调用）----------
async def access_denied(redis: Redis, user_id: int, jti: str, iat: int) -> bool:
    """退出黑名单 + 改密时间线检查。Redis 异常向上抛（由路由层转 503）。"""
    min_iat = await redis.get(_min_iat_key(user_id))
    if min_iat is not None and iat < int(min_iat):
        return True
    return bool(await redis.exists(_deny_key(jti)))


# 供 _revoke_family 之外的场景复用（如管理端未来批量踢出）
async def revoke_user_sessions(db: AsyncSession, user_id: int, reason: str) -> None:
    await db.execute(
        update(RefreshSession)
        .where(RefreshSession.user_id == user_id, RefreshSession.revoked_at.is_(None))
        .values(revoked_at=utcnow(), revoked_reason=reason or REASON_FAMILY_REVOKED)
    )
    await db.commit()
