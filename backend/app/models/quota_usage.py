"""空间用量：used_bytes 含回收站与历史版本；reserved_bytes 为进行中上传预留（M6 启用）。

个人空间继承用户额度（user.quota_bytes）；团队空间独立额度（M8）。
行在首次用量变更时懒创建；配额校验一律在事务内对行加锁（FOR UPDATE）后进行。
"""
from datetime import datetime

from sqlalchemy import BigInteger, DateTime, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column

from app.core.time import utcnow
from app.db.base import Base
from app.db.types import Int64PK


class QuotaUsage(Base):
    __tablename__ = "quota_usage"
    __table_args__ = {
        "mysql_charset": "utf8mb4",
        "mysql_collate": "utf8mb4_0900_ai_ci",
    }

    space_id: Mapped[int] = mapped_column(
        Int64PK, ForeignKey("space.id"), primary_key=True
    )
    used_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    reserved_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=utcnow, onupdate=utcnow,
        server_default="CURRENT_TIMESTAMP",
    )
