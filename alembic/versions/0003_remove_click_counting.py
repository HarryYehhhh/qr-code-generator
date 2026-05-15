"""remove click counting (ADR-0003)

Drop the qr_click_stats table and the click_count / last_clicked_at columns
on qr_codes. The click pipeline (worker, Redis Stream consumer, hourly flush
job) was removed in ADR-0003 to focus the MVP on the redirect path.

Revision ID: 0003_remove_click_counting
Revises: 0002_add_qr_click_stats
Create Date: 2026-05-15

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0003_remove_click_counting"
down_revision: Union[str, Sequence[str], None] = "0002_add_qr_click_stats"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_index("ix_qr_click_stats_hour_bucket", table_name="qr_click_stats")
    op.drop_table("qr_click_stats")
    with op.batch_alter_table("qr_codes") as batch_op:
        batch_op.drop_column("click_count")
        batch_op.drop_column("last_clicked_at")


def downgrade() -> None:
    with op.batch_alter_table("qr_codes") as batch_op:
        batch_op.add_column(sa.Column("click_count", sa.Integer(), nullable=False, server_default="0"))
        batch_op.add_column(sa.Column("last_clicked_at", sa.DateTime(), nullable=True))
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
