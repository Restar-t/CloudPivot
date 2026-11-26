"""m6 uploads: upload_task / upload_part / outbox

Revision ID: 0006
Revises: 0005
Create Date: 2026-11-26

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0006"
down_revision: Union[str, None] = "0005"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

MYSQL_TABLE_KWARGS = {
    "mysql_charset": "utf8mb4",
    "mysql_collate": "utf8mb4_0900_ai_ci",
}


def upgrade() -> None:
    op.create_table(
        "upload_task",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("upload_id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("space_id", sa.BigInteger(), nullable=False),
        sa.Column("parent_id", sa.BigInteger(), nullable=False),
        sa.Column("target_file_id", sa.BigInteger(), nullable=True),
        sa.Column("target_generation", sa.Integer(), nullable=True),
        sa.Column("file_name", sa.String(length=255), nullable=False),
        sa.Column("declared_sha256", sa.String(length=64), nullable=True),
        sa.Column("size", sa.BigInteger(), nullable=False),
        sa.Column("reserved_bytes", sa.BigInteger(), nullable=False),
        sa.Column("chunk_size", sa.BigInteger(), nullable=False),
        sa.Column("chunk_count", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("expire_at", sa.DateTime(), nullable=False),
        sa.Column("result_version_id", sa.BigInteger(), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_upload_task")),
        sa.UniqueConstraint("upload_id", name=op.f("uq_upload_task_upload_id")),
        **MYSQL_TABLE_KWARGS,
    )
    op.create_index("ix_upload_task_user_status", "upload_task", ["user_id", "status"], unique=False)
    op.create_index("ix_upload_task_expire", "upload_task", ["expire_at"], unique=False)
    op.create_table(
        "upload_part",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("task_id", sa.BigInteger(), nullable=False),
        sa.Column("part_no", sa.Integer(), nullable=False),
        sa.Column("size", sa.BigInteger(), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("etag", sa.String(length=64), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_upload_part")),
        sa.UniqueConstraint("task_id", "part_no", name="uq_upload_part_no"),
        **MYSQL_TABLE_KWARGS,
    )
    op.create_table(
        "outbox",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("event_type", sa.String(length=32), nullable=False),
        sa.Column("payload", sa.String(length=1024), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("retry_count", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_outbox")),
        **MYSQL_TABLE_KWARGS,
    )
    op.create_index("ix_outbox_status_created", "outbox", ["status", "created_at"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_outbox_status_created", table_name="outbox")
    op.drop_table("outbox")
    op.drop_table("upload_part")
    op.drop_index("ix_upload_task_expire", table_name="upload_task")
    op.drop_index("ix_upload_task_user_status", table_name="upload_task")
    op.drop_table("upload_task")
