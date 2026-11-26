"""物理对象（三层模型底层）：按服务端核验的 (sha256, size) 全局唯一，引用计数管理生命周期。

- ref_count = 指向它的 file_version 行数（对账汇总值，M7 校验）
- status: ACTIVE 正常；DELETING 零引用待物理删除（M7 状态机）；DELETED 已删除
- M3 不做秒传/跨用户免上传，但物理去重由唯一约束自然产生：
  相同内容再次上传时复用对象（ref_count+1），不再写第二份字节
"""
from datetime import datetime

from sqlalchemy import BigInteger, DateTime, Index, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.core.time import utcnow
from app.db.base import Base
from app.db.types import Int64PK

OBJECT_STATUS_ACTIVE = "ACTIVE"
OBJECT_STATUS_DELETING = "DELETING"
OBJECT_STATUS_DELETED = "DELETED"


class StorageObject(Base):
    __tablename__ = "storage_object"
    __table_args__ = (
        UniqueConstraint("sha256", "size", name="uq_storage_object_content"),
        Index("ix_storage_object_status", "status"),
        {"mysql_charset": "utf8mb4", "mysql_collate": "utf8mb4_0900_ai_ci"},
    )

    id: Mapped[int] = mapped_column(Int64PK, primary_key=True, autoincrement=True)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    size: Mapped[int] = mapped_column(BigInteger, nullable=False)
    bucket: Mapped[str] = mapped_column(String(63), nullable=False)
    object_key: Mapped[str] = mapped_column(String(64), nullable=False)  # UUID，不含用户输入
    ref_count: Mapped[int] = mapped_column(BigInteger, nullable=False, default=1)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default=OBJECT_STATUS_ACTIVE)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=utcnow, server_default="CURRENT_TIMESTAMP"
    )
