"""逻辑文件/目录（三层模型的顶层：user_file → file_version → storage_object）。

关键设计：
- parent_id=0 表示空间根（不用 NULL：MySQL 唯一索引对 NULL 不约束，根级会失去重名保护）
- "同目录活跃唯一"用 alive 列实现：NORMAL → 1，TRASH/DELETED → NULL，
  唯一索引 (space_id, parent_id, name, alive) 对 NULL 放行，软删除后同名可再建
- generation 随改名/移动/删除/切版本 +1，供上传任务冲突校验（M6）
- 回收站 30 天保留以 deleted_at 计（M4）
"""
from datetime import datetime

from sqlalchemy import BigInteger, Boolean, DateTime, Index, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.core.time import utcnow
from app.db.base import Base
from app.db.types import Int64PK

FILE_STATUS_NORMAL = "NORMAL"
FILE_STATUS_TRASH = "TRASH"
FILE_STATUS_DELETED = "DELETED"

ROOT_PARENT_ID = 0


class UserFile(Base):
    __tablename__ = "user_file"
    __table_args__ = (
        Index(
            "uq_user_file_dir_active_name",
            "space_id", "parent_id", "name", "alive",
            unique=True,
        ),
        Index("ix_user_file_parent", "space_id", "parent_id", "status"),
        {"mysql_charset": "utf8mb4", "mysql_collate": "utf8mb4_0900_ai_ci"},
    )

    id: Mapped[int] = mapped_column(Int64PK, primary_key=True, autoincrement=True)
    space_id: Mapped[int] = mapped_column(Int64PK, nullable=False)  # FK space.id（应用层保证）
    parent_id: Mapped[int] = mapped_column(BigInteger, nullable=False, default=ROOT_PARENT_ID)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    is_folder: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    current_version_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)  # M3 接 file_version
    generation: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default=FILE_STATUS_NORMAL)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    alive: Mapped[int | None] = mapped_column(Integer, nullable=True, default=1)  # NORMAL→1 否则 NULL
    size: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)  # 逻辑大小快照
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=utcnow, server_default="CURRENT_TIMESTAMP"
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=utcnow, onupdate=utcnow,
        server_default="CURRENT_TIMESTAMP",
    )
