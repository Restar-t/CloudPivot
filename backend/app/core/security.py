"""密码哈希与令牌原语。

- 密码：bcrypt；先 SHA-256 再 bcrypt（规避 bcrypt 72 字节截断，且 hex 输入无空字节问题）
- 访问令牌：短期 JWT（默认 15 分钟），payload 含 sub/jti/iat/exp/type
- 刷新令牌：随机 opaque token（secrets.token_urlsafe），库中只存 SHA-256 摘要；
  轮换与重用检测的会话族逻辑见 services/auth_service.py
"""
import hashlib
import secrets
import uuid
from datetime import timedelta

import bcrypt
import jwt

from app.core.config import get_settings
from app.core.errors import BizError, ErrorCode
from app.core.time import epoch_now

# 用户不存在时用于拉平校验耗时的哑哈希（惰性生成，轮数与生产一致）
_dummy_hash: bytes | None = None


def _prehash(password: str) -> bytes:
    return hashlib.sha256(password.encode("utf-8")).hexdigest().encode("ascii")


def hash_password(password: str, rounds: int | None = None) -> str:
    rounds = rounds if rounds is not None else get_settings().password_hash_rounds
    salt = bcrypt.gensalt(rounds=rounds)
    return bcrypt.hashpw(_prehash(password), salt).decode("ascii")


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return bcrypt.checkpw(_prehash(password), password_hash.encode("ascii"))
    except ValueError:
        return False


def dummy_verify(password: str) -> None:
    """用户不存在时执行一次等价开销的校验，避免通过响应时间枚举用户名。"""
    global _dummy_hash
    if _dummy_hash is None:
        _dummy_hash = bcrypt.hashpw(_prehash("timing-equalizer"), bcrypt.gensalt())
    bcrypt.checkpw(_prehash(password), _dummy_hash)


# ---------- 访问令牌（JWT）----------
def create_access_token(user_id: int) -> tuple[str, dict]:
    """返回 (token, claims)；claims 供调用方做 jti 黑名单等簿记。"""
    settings = get_settings()
    now = epoch_now()
    claims = {
        "sub": str(user_id),
        "jti": uuid.uuid4().hex,
        "iat": now,
        "exp": now + settings.jwt_access_ttl_minutes * 60,
        "type": "access",
    }
    token = jwt.encode(claims, settings.jwt_secret_key, algorithm=settings.jwt_algorithm)
    return token, claims


def decode_access_token(token: str) -> dict:
    """校验并解码；过期 → TOKEN_EXPIRED，其余无效 → UNAUTHENTICATED。"""
    settings = get_settings()
    try:
        payload = jwt.decode(
            token, settings.jwt_secret_key, algorithms=[settings.jwt_algorithm]
        )
    except jwt.ExpiredSignatureError as exc:
        raise BizError(ErrorCode.TOKEN_EXPIRED) from exc
    except jwt.InvalidTokenError as exc:
        raise BizError(ErrorCode.UNAUTHENTICATED) from exc
    if payload.get("type") != "access":
        raise BizError(ErrorCode.UNAUTHENTICATED)
    return payload


def access_ttl_seconds() -> int:
    return get_settings().jwt_access_ttl_minutes * 60


# ---------- 刷新令牌（opaque）----------
def generate_refresh_token() -> str:
    return secrets.token_urlsafe(48)


def hash_refresh_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def refresh_ttl() -> timedelta:
    return timedelta(days=get_settings().jwt_refresh_ttl_days)
