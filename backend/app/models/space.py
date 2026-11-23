"""空间与成员：文件属于"空间"而非用户；个人空间=单成员空间（仅本人可见的根）。"""
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.core.time import utcnow
from app.db.base import Base
from app.db.types import Int64PK

SPACE_TYPE_PERSONAL = "PERSONAL"
SPACE_TYPE_TEAM = "TEAM"

ROLE_VIEWER = "VIEWER"
ROLE_EDITOR = "EDITOR"
ROLE_ADMIN = "ADMIN"
ROLE_OWNER = "OWNER"


class Space(Base):
    __tablename__ = "space"
    __table_args__ = {
        "mysql_charset": "utf8mb4",
        "mysql_collate": "utf8mb4_0900_ai_ci",
    }

    id: Mapped[int] = mapped_column(Int64PK, primary_key=True, autoincrement=True)
    type: Mapped[str] = mapped_column(String(16), nullable=False)
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    owner_id: Mapped[int] = mapped_column(Int64PK, ForeignKey("user.id"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=utcnow, server_default="CURRENT_TIMESTAMP"
    )


class SpaceMember(Base):
    __tablename__ = "space_member"
    __table_args__ = (
        UniqueConstraint("space_id", "user_id", name="uq_space_member_space_user"),
        {"mysql_charset": "utf8mb4", "mysql_collate": "utf8mb4_0900_ai_ci"},
    )

    id: Mapped[int] = mapped_column(Int64PK, primary_key=True, autoincrement=True)
    space_id: Mapped[int] = mapped_column(Int64PK, ForeignKey("space.id"), nullable=False)
    user_id: Mapped[int] = mapped_column(Int64PK, ForeignKey("user.id"), nullable=False)
    role: Mapped[str] = mapped_column(String(16), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=utcnow, server_default="CURRENT_TIMESTAMP"
    )
