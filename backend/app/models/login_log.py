"""登录日志：无论成败都落一行，供审计与爆破分析（T 系列"记录日志与定位原因"）。"""
from datetime import datetime

from sqlalchemy import DateTime, Index, String
from sqlalchemy.orm import Mapped, mapped_column

from app.core.time import utcnow
from app.db.base import Base
from app.db.types import Int64PK

RESULT_SUCCESS = "SUCCESS"
RESULT_FAILED = "FAILED"
RESULT_LOCKED = "LOCKED"


class LoginLog(Base):
    __tablename__ = "login_log"
    __table_args__ = (
        Index("ix_login_log_username_created", "username", "created_at"),
        {"mysql_charset": "utf8mb4", "mysql_collate": "utf8mb4_0900_ai_ci"},
    )

    id: Mapped[int] = mapped_column(Int64PK, primary_key=True, autoincrement=True)
    username: Mapped[str] = mapped_column(String(32), nullable=False)
    ip: Mapped[str] = mapped_column(String(45), nullable=False, default="")
    result: Mapped[str] = mapped_column(String(16), nullable=False)
    detail: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=utcnow, server_default="CURRENT_TIMESTAMP"
    )
