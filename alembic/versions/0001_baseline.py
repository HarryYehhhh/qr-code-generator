"""baseline: existing qr_codes table

Revision ID: 0001_baseline
Revises:
Create Date: 2026-05-03

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0001_baseline"
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "qr_codes",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("qr_token", sa.String(length=10), nullable=False),
        sa.Column("url", sa.String(length=2048), nullable=False),
        sa.Column("status", sa.String(), nullable=False, server_default="active"),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("click_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_clicked_at", sa.DateTime(), nullable=True),
        sa.Column("deleted_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("qr_token"),
    )
    op.create_index("ix_qr_codes_qr_token", "qr_codes", ["qr_token"])


def downgrade() -> None:
    op.drop_index("ix_qr_codes_qr_token", table_name="qr_codes")
    op.drop_table("qr_codes")
