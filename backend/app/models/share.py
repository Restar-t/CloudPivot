"""分享与分享访问会话（M5）。

share：
- share_token 随机 uuid4（防枚举，不使用自增 id 出现在 URL）；
- extract_code_hash bcrypt 存储；auth_epoch 撤销/重置开关（取消 +1，旧会话全部失效）；
- visit_count 按"下载会话"计数（同会话的预览与任意次 Range 只计一次），
  扣减用原子条件更新（visit_count < max_visits）；max_visits/expire_at 为 NULL 表示不限。

share_session：
- session_token 随机 uuid4，仅凭它可访问（独立于用户登录体系）；
- 绑定 version_id + auth_epoch：历史会话不能自动读取后续新版本，撤销后全拒；
- idempotency_key：客户端重试不重复扣名额；
- counted：该会话是否已消耗名额（会话内预览/Range 不重复计）。
"""
from datetime import datetime

from sqlalchemy import BigInteger, DateTime, ForeignKey, Index, String, text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.time import utcnow
from app.db.base import Base
from app.db.types import Int64PK

SHARE_STATUS_ACTIVE = "ACTIVE"
SHARE_STATUS_CANCELLED = "CANCELLED"


class Share(Base):
    __tablename__ = "share"
    __table_args__ = (
        Index("ix_share_owner_created", "owner_id", "created_at"),
        Index("ix_share_status_expire", "status", "expire_at"),
        {"mysql_charset": "utf8mb4", "mysql_collate": "utf8mb4_0900_ai_ci"},
    )

    id: Mapped[int] = mapped_column(Int64PK, primary_key=True, autoincrement=True)
    share_token: Mapped[str] = mapped_column(String(36), unique=True, nullable=False)
    file_id: Mapped[int] = mapped_column(Int64PK, ForeignKey("user_file.id"), nullable=False)
    owner_id: Mapped[int] = mapped_column(Int64PK, ForeignKey("user.id"), nullable=False)
    extract_code_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    max_visits: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    visit_count: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    expire_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default=SHARE_STATUS_ACTIVE)
    auth_epoch: Mapped[int] = mapped_column(BigInteger, nullable=False, default=1)
    last_visit_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=utcnow, server_default=text("CURRENT_TIMESTAMP")
    )


class ShareSession(Base):
    __tablename__ = "share_session"
    __table_args__ = (
        Index("ix_share_session_share", "share_id"),
        Index("uq_share_session_idem", "share_id", "idempotency_key", unique=True),
        {"mysql_charset": "utf8mb4", "mysql_collate": "utf8mb4_0900_ai_ci"},
    )

    id: Mapped[int] = mapped_column(Int64PK, primary_key=True, autoincrement=True)
    share_id: Mapped[int] = mapped_column(Int64PK, ForeignKey("share.id"), nullable=False)
    session_token: Mapped[str] = mapped_column(String(36), unique=True, nullable=False)
    version_id: Mapped[int] = mapped_column(Int64PK, ForeignKey("file_version.id"), nullable=False)
    auth_epoch: Mapped[int] = mapped_column(BigInteger, nullable=False)
    idempotency_key: Mapped[str | None] = mapped_column(String(64), nullable=True)
    counted: Mapped[bool] = mapped_column(BigInteger, nullable=False, default=0)  # 0/1
    expires_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=utcnow, server_default=text("CURRENT_TIMESTAMP")
    )
