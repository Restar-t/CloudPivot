"""操作审计日志：核心写操作（上传/下载/删除/重命名…）逐条落库，供审计与定位原因。"""
from datetime import datetime

from sqlalchemy import BigInteger, DateTime, Index, String
from sqlalchemy.orm import Mapped, mapped_column

from app.core.time import utcnow
from app.db.base import Base
from app.db.types import Int64PK

ACTION_UPLOAD = "upload"
ACTION_DOWNLOAD = "download"
ACTION_DELETE = "delete"  # 进回收站
ACTION_RENAME = "rename"
ACTION_FOLDER_CREATE = "folder_create"


class OperationLog(Base):
    __tablename__ = "operation_log"
    __table_args__ = (
        Index("ix_operation_log_user_created", "user_id", "created_at"),
        {"mysql_charset": "utf8mb4", "mysql_collate": "utf8mb4_0900_ai_ci"},
    )

    id: Mapped[int] = mapped_column(Int64PK, primary_key=True, autoincrement=True)
    user_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)  # 匿名分享操作为 NULL（M5）
    action: Mapped[str] = mapped_column(String(32), nullable=False)
    target_type: Mapped[str] = mapped_column(String(32), nullable=False, default="")
    target_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    detail: Mapped[str] = mapped_column(String(512), nullable=False, default="")
    ip: Mapped[str] = mapped_column(String(45), nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=utcnow, server_default="CURRENT_TIMESTAMP"
    )
