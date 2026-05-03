"""add qr_click_stats

Revision ID: 0002_add_qr_click_stats
Revises: 0001_baseline
Create Date: 2026-05-03

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0002_add_qr_click_stats"
down_revision: Union[str, Sequence[str], None] = "0001_baseline"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "qr_click_stats",
        sa.Column("qr_token", sa.String(length=10), nullable=False),
        sa.Column("hour_bucket", sa.DateTime(timezone=True), nullable=False),
        sa.Column("click_count", sa.Integer(), nullable=False, server_default="0"),
        sa.PrimaryKeyConstraint("qr_token", "hour_bucket"),
    )
    op.create_index(
        "ix_qr_click_stats_hour_bucket",
        "qr_click_stats",
        ["hour_bucket"],
    )


def downgrade() -> None:
    op.drop_index("ix_qr_click_stats_hour_bucket", table_name="qr_click_stats")
    op.drop_table("qr_click_stats")
