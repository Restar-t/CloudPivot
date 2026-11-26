"""上传任务/分片/outbox（M6 优级核心：可靠大文件传输）。

upload_task 状态机（PLAN §2.3）：
  CREATED → UPLOADING → VERIFYING → COMPLETED；另有 CANCELLED / EXPIRED / FAILED。
  终态不可复活；COMPLETED 重复 complete 返回同一 result_version_id（幂等）；
  取消与完成在同一任务行锁（SELECT ... FOR UPDATE）下线性化。

配额：init 时原子预留（used+reserved+size ≤ limit），成功消耗（used+=size, reserved-=size）
与取消/过期释放（reserved-=size）都只允许发生一次（由状态机守卫）。

分片落盘：candidate 桶 up/{upload_id}/part-{n:05d}（不可变 key）；合并产物
up/{upload_id}/merged，提交成功后由 storage_object 正式引用（candidate 亦可为正式桶，
M7 GC 规则：candidate 中无任务引用且无对象行的对象为孤儿）。

outbox：本地消息表——业务事务内写事件行，M7 调度器轮询投递（幂等消费）。
"""
from datetime import datetime

from sqlalchemy import BigInteger, DateTime, ForeignKey, Index, Integer, String, UniqueConstraint, text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.time import utcnow
from app.db.base import Base
from app.db.types import Int64PK

TASK_CREATED = "CREATED"
TASK_UPLOADING = "UPLOADING"
TASK_VERIFYING = "VERIFYING"
TASK_COMPLETED = "COMPLETED"
TASK_CANCELLED = "CANCELLED"
TASK_EXPIRED = "EXPIRED"
TASK_FAILED = "FAILED"

TERMINAL_STATES = {TASK_COMPLETED, TASK_CANCELLED, TASK_EXPIRED, TASK_FAILED}

OUTBOX_PENDING = "PENDING"
OUTBOX_DONE = "DONE"


class UploadTask(Base):
    __tablename__ = "upload_task"
    __table_args__ = (
        Index("ix_upload_task_user_status", "user_id", "status"),
        Index("ix_upload_task_expire", "expire_at"),
        {"mysql_charset": "utf8mb4", "mysql_collate": "utf8mb4_0900_ai_ci"},
    )

    id: Mapped[int] = mapped_column(Int64PK, primary_key=True, autoincrement=True)
    upload_id: Mapped[str] = mapped_column(String(36), unique=True, nullable=False)
    user_id: Mapped[int] = mapped_column(Int64PK, ForeignKey("user.id"), nullable=False)
    space_id: Mapped[int] = mapped_column(Int64PK, ForeignKey("space.id"), nullable=False)
    parent_id: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    # 同名已存在 → "建新版本"意图：目标文件与发起时的 generation 快照
    target_file_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    target_generation: Mapped[int | None] = mapped_column(Integer, nullable=True)
    file_name: Mapped[str] = mapped_column(String(255), nullable=False)
    declared_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    size: Mapped[int] = mapped_column(BigInteger, nullable=False)
    reserved_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    chunk_size: Mapped[int] = mapped_column(BigInteger, nullable=False)
    chunk_count: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default=TASK_CREATED)
    expire_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    result_version_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=utcnow, server_default=text("CURRENT_TIMESTAMP")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=utcnow, onupdate=utcnow,
        server_default=text("CURRENT_TIMESTAMP"),
    )


class UploadPart(Base):
    __tablename__ = "upload_part"
    __table_args__ = (
        UniqueConstraint("task_id", "part_no", name="uq_upload_part_no"),
        {"mysql_charset": "utf8mb4", "mysql_collate": "utf8mb4_0900_ai_ci"},
    )

    id: Mapped[int] = mapped_column(Int64PK, primary_key=True, autoincrement=True)
    task_id: Mapped[int] = mapped_column(Int64PK, ForeignKey("upload_task.id"), nullable=False)
    part_no: Mapped[int] = mapped_column(Integer, nullable=False)  # 从 1 开始
    size: Mapped[int] = mapped_column(BigInteger, nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    etag: Mapped[str] = mapped_column(String(64), nullable=False, default="")


class Outbox(Base):
    __tablename__ = "outbox"
    __table_args__ = (
        Index("ix_outbox_status_created", "status", "created_at"),
        {"mysql_charset": "utf8mb4", "mysql_collate": "utf8mb4_0900_ai_ci"},
    )

    id: Mapped[int] = mapped_column(Int64PK, primary_key=True, autoincrement=True)
    event_type: Mapped[str] = mapped_column(String(32), nullable=False)
    payload: Mapped[str] = mapped_column(String(1024), nullable=False)  # JSON
    status: Mapped[str] = mapped_column(String(16), nullable=False, default=OUTBOX_PENDING)
    retry_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=utcnow, server_default=text("CURRENT_TIMESTAMP")
    )


EVENT_FILE_CREATED = "file.created"
EVENT_VERSION_CREATED = "version.created"
EVENT_FILE_TRASHED = "file.trashed"
EVENT_FILE_RESTORED = "file.restored"
EVENT_FILE_DELETED = "file.deleted"
EVENT_SHARE_REVOKED = "share.revoked"
