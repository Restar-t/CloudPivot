"""文件版本（三层模型中层）：file_version 是引用物理对象的事实来源。

- 唯一 (file_id, version_no)；current_version_id 只是 user_file 上的指针，不重复计数
- 配额口径 = 空间内所有保留版本逻辑大小之和（含回收站；同内容多版本分别计，M7 恢复/删版本沿用）
"""
from datetime import datetime

from sqlalchemy import BigInteger, DateTime, ForeignKey, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.core.time import utcnow
from app.db.base import Base
from app.db.types import Int64PK


class FileVersion(Base):
    __tablename__ = "file_version"
    __table_args__ = (
        UniqueConstraint("file_id", "version_no", name="uq_file_version_no"),
        {"mysql_charset": "utf8mb4", "mysql_collate": "utf8mb4_0900_ai_ci"},
    )

    id: Mapped[int] = mapped_column(Int64PK, primary_key=True, autoincrement=True)
    file_id: Mapped[int] = mapped_column(Int64PK, ForeignKey("user_file.id"), nullable=False)
    version_no: Mapped[int] = mapped_column(BigInteger, nullable=False, default=1)
    storage_object_id: Mapped[int] = mapped_column(
        Int64PK, ForeignKey("storage_object.id"), nullable=False
    )
    size: Mapped[int] = mapped_column(BigInteger, nullable=False)  # 逻辑大小（计配额）
    created_by: Mapped[int] = mapped_column(Int64PK, ForeignKey("user.id"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=utcnow, server_default="CURRENT_TIMESTAMP"
    )
