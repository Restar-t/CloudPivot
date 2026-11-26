"""m5 share: share / share_session

Revision ID: 0005
Revises: 0004
Create Date: 2026-11-26

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0005"
down_revision: Union[str, None] = "0004"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

MYSQL_TABLE_KWARGS = {
    "mysql_charset": "utf8mb4",
    "mysql_collate": "utf8mb4_0900_ai_ci",
}


def upgrade() -> None:
    op.create_table(
        "share",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("share_token", sa.String(length=36), nullable=False),
        sa.Column("file_id", sa.BigInteger(), nullable=False),
        sa.Column("owner_id", sa.BigInteger(), nullable=False),
        sa.Column("extract_code_hash", sa.String(length=128), nullable=False),
        sa.Column("max_visits", sa.BigInteger(), nullable=True),
        sa.Column("visit_count", sa.BigInteger(), nullable=False),
        sa.Column("expire_at", sa.DateTime(), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("auth_epoch", sa.BigInteger(), nullable=False),
        sa.Column("last_visit_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_share")),
        sa.UniqueConstraint("share_token", name=op.f("uq_share_share_token")),
        **MYSQL_TABLE_KWARGS,
    )
    op.create_index("ix_share_owner_created", "share", ["owner_id", "created_at"], unique=False)
    op.create_index("ix_share_status_expire", "share", ["status", "expire_at"], unique=False)
    op.create_table(
        "share_session",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("share_id", sa.BigInteger(), nullable=False),
        sa.Column("session_token", sa.String(length=36), nullable=False),
        sa.Column("version_id", sa.BigInteger(), nullable=False),
        sa.Column("auth_epoch", sa.BigInteger(), nullable=False),
        sa.Column("idempotency_key", sa.String(length=64), nullable=True),
        sa.Column("counted", sa.BigInteger(), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("last_used_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_share_session")),
        sa.UniqueConstraint("session_token", name=op.f("uq_share_session_token")),
        sa.UniqueConstraint("share_id", "idempotency_key", name="uq_share_session_idem"),
        **MYSQL_TABLE_KWARGS,
    )
    op.create_index("ix_share_session_share", "share_session", ["share_id"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_share_session_share", table_name="share_session")
    op.drop_table("share_session")
    op.drop_index("ix_share_status_expire", table_name="share")
    op.drop_index("ix_share_owner_created", table_name="share")
    op.drop_table("share")
