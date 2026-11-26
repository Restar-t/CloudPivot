"""m3 files: storage_object / file_version / quota_usage / operation_log

Revision ID: 0004
Revises: 0003
Create Date: 2026-11-24

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0004"
down_revision: Union[str, None] = "0003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

MYSQL_TABLE_KWARGS = {
    "mysql_charset": "utf8mb4",
    "mysql_collate": "utf8mb4_0900_ai_ci",
}


def upgrade() -> None:
    op.create_table(
        "storage_object",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("size", sa.BigInteger(), nullable=False),
        sa.Column("bucket", sa.String(length=63), nullable=False),
        sa.Column("object_key", sa.String(length=64), nullable=False),
        sa.Column("ref_count", sa.BigInteger(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_storage_object")),
        sa.UniqueConstraint("sha256", "size", name="uq_storage_object_content"),
        **MYSQL_TABLE_KWARGS,
    )
    op.create_index("ix_storage_object_status", "storage_object", ["status"], unique=False)
    op.create_table(
        "file_version",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("file_id", sa.BigInteger(), nullable=False),
        sa.Column("version_no", sa.BigInteger(), nullable=False),
        sa.Column("storage_object_id", sa.BigInteger(), nullable=False),
        sa.Column("size", sa.BigInteger(), nullable=False),
        sa.Column("created_by", sa.BigInteger(), nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_file_version")),
        sa.UniqueConstraint("file_id", "version_no", name="uq_file_version_no"),
        **MYSQL_TABLE_KWARGS,
    )
    op.create_table(
        "quota_usage",
        sa.Column("space_id", sa.BigInteger(), nullable=False),
        sa.Column("used_bytes", sa.BigInteger(), nullable=False),
        sa.Column("reserved_bytes", sa.BigInteger(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.PrimaryKeyConstraint("space_id", name=op.f("pk_quota_usage")),
        **MYSQL_TABLE_KWARGS,
    )
    op.create_table(
        "operation_log",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=True),
        sa.Column("action", sa.String(length=32), nullable=False),
        sa.Column("target_type", sa.String(length=32), nullable=False),
        sa.Column("target_id", sa.BigInteger(), nullable=True),
        sa.Column("detail", sa.String(length=512), nullable=False),
        sa.Column("ip", sa.String(length=45), nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_operation_log")),
        **MYSQL_TABLE_KWARGS,
    )
    op.create_index(
        "ix_operation_log_user_created", "operation_log", ["user_id", "created_at"], unique=False
    )


def downgrade() -> None:
    op.drop_index("ix_operation_log_user_created", table_name="operation_log")
    op.drop_table("operation_log")
    op.drop_table("quota_usage")
    op.drop_table("file_version")
    op.drop_index("ix_storage_object_status", table_name="storage_object")
    op.drop_table("storage_object")
