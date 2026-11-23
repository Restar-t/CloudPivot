"""m1 auth: user / login_log / refresh_session

Revision ID: 0002
Revises: 0001
Create Date: 2026-11-23

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0002"
down_revision: Union[str, None] = "0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

MYSQL_TABLE_KWARGS = {
    "mysql_charset": "utf8mb4",
    "mysql_collate": "utf8mb4_0900_ai_ci",
}


def upgrade() -> None:
    op.create_table(
        "user",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("username", sa.String(length=32), nullable=False),
        sa.Column("password_hash", sa.String(length=128), nullable=False),
        sa.Column("quota_bytes", sa.BigInteger(), server_default="10737418240", nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("1"), nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_user")),
        sa.UniqueConstraint("username", name=op.f("uq_user_username")),
        **MYSQL_TABLE_KWARGS,
    )
    op.create_table(
        "login_log",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("username", sa.String(length=32), nullable=False),
        sa.Column("ip", sa.String(length=45), nullable=False),
        sa.Column("result", sa.String(length=16), nullable=False),
        sa.Column("detail", sa.String(length=255), nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_login_log")),
        **MYSQL_TABLE_KWARGS,
    )
    op.create_index(
        "ix_login_log_username_created", "login_log", ["username", "created_at"],
        unique=False,
    )
    op.create_table(
        "refresh_session",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("family_id", sa.String(length=36), nullable=False),
        sa.Column(
            "user_id",
            sa.BigInteger(),
            sa.ForeignKey("user.id", name=op.f("fk_refresh_session_user_id_user")),
            nullable=False,
        ),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("revoked_at", sa.DateTime(), nullable=True),
        sa.Column("revoked_reason", sa.String(length=32), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_refresh_session")),
        sa.UniqueConstraint("token_hash", name=op.f("uq_refresh_session_token_hash")),
        **MYSQL_TABLE_KWARGS,
    )
    op.create_index("ix_refresh_session_user_id", "refresh_session", ["user_id"], unique=False)
    op.create_index("ix_refresh_session_expires", "refresh_session", ["expires_at"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_refresh_session_expires", table_name="refresh_session")
    op.drop_index("ix_refresh_session_user_id", table_name="refresh_session")
    op.drop_table("refresh_session")
    op.drop_index("ix_login_log_username_created", table_name="login_log")
    op.drop_table("login_log")
    op.drop_table("user")
