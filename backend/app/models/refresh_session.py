"""可撤销刷新会话。

设计（PLAN §五.1：刷新令牌轮换并检测重用）：
- 每次登录开一个"会话族"（family_id）；
- 每次刷新 = 旧行 revoke(reason=rotated) + 同族新行（token_hash 轮换）；
- 已 revoke 的令牌再次出现 = 重用攻击 → 撤销整个族；
- 库中只存 token 的 SHA-256，泄露数据库不能直接换 token。
"""
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, String, text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.time import utcnow
from app.db.base import Base
from app.db.types import Int64PK

REASON_ROTATED = "rotated"
REASON_LOGOUT = "logout"
REASON_PASSWORD_CHANGED = "password_changed"
REASON_REUSE_DETECTED = "reuse_detected"
REASON_FAMILY_REVOKED = "family_revoked"


class RefreshSession(Base):
    __tablename__ = "refresh_session"
    __table_args__ = (
        Index("ix_refresh_session_user_id", "user_id"),
        Index("ix_refresh_session_expires", "expires_at"),
        {"mysql_charset": "utf8mb4", "mysql_collate": "utf8mb4_0900_ai_ci"},
    )

    id: Mapped[int] = mapped_column(Int64PK, primary_key=True, autoincrement=True)
    family_id: Mapped[str] = mapped_column(String(36), nullable=False)
    user_id: Mapped[int] = mapped_column(Int64PK, ForeignKey("user.id"), nullable=False)
    # 当前族内有效的刷新令牌摘要（历史行保留哈希用于重用检测）
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    revoked_at: Mapped[datetime] = mapped_column(DateTime, nullable=True)
    revoked_reason: Mapped[str] = mapped_column(String(32), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=utcnow, server_default=text("CURRENT_TIMESTAMP")
    )
