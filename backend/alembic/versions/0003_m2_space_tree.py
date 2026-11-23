"""m2 space tree: space / space_member / user_file

Revision ID: 0003
Revises: 0002
Create Date: 2026-11-23

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0003"
down_revision: Union[str, None] = "0002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

MYSQL_TABLE_KWARGS = {
    "mysql_charset": "utf8mb4",
    "mysql_collate": "utf8mb4_0900_ai_ci",
}


def upgrade() -> None:
    op.create_table(
        "space",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("type", sa.String(length=16), nullable=False),
        sa.Column("name", sa.String(length=64), nullable=False),
        sa.Column("owner_id", sa.BigInteger(), nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_space")),
        **MYSQL_TABLE_KWARGS,
    )
    op.create_table(
        "space_member",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("space_id", sa.BigInteger(), nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("role", sa.String(length=16), nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_space_member")),
        sa.UniqueConstraint("space_id", "user_id", name="uq_space_member_space_user"),
        **MYSQL_TABLE_KWARGS,
    )
    op.create_table(
        "user_file",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("space_id", sa.BigInteger(), nullable=False),
        sa.Column("parent_id", sa.BigInteger(), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("is_folder", sa.Boolean(), nullable=False),
        sa.Column("current_version_id", sa.BigInteger(), nullable=True),
        sa.Column("generation", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("deleted_at", sa.DateTime(), nullable=True),
        sa.Column("alive", sa.Integer(), nullable=True),
        sa.Column("size", sa.BigInteger(), nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_user_file")),
        **MYSQL_TABLE_KWARGS,
    )
    op.create_index(
        "uq_user_file_dir_active_name", "user_file",
        ["space_id", "parent_id", "name", "alive"], unique=True,
    )
    op.create_index(
        "ix_user_file_parent", "user_file",
        ["space_id", "parent_id", "status"], unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_user_file_parent", table_name="user_file")
    op.drop_index("uq_user_file_dir_active_name", table_name="user_file")
    op.drop_table("user_file")
    op.drop_table("space_member")
    op.drop_table("space")
