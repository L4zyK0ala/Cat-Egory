"""demand: daily totals of looked-up domains and feed downloads

Totals only, never who asked (backend/app/demand.py). The ``demand`` data source is added by
``python -m database.seed``, which the migrate service runs after the migrations.

Revision ID: 0012
Revises: 0011
Create Date: 2026-11-16
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0012"
down_revision: Union[str, Sequence[str], None] = "0011"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "demand_daily",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("day", sa.Date(), nullable=False),
        sa.Column("domain", sa.String(length=253), nullable=False),
        sa.Column("known", sa.Boolean(), nullable=False),
        sa.Column("lookups", sa.Integer(), nullable=False),
        sa.Column("callers", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_demand_daily")),
        sa.UniqueConstraint("day", "domain", name=op.f("uq_demand_daily_day_domain")),
    )
    op.create_index("ix_demand_daily_domain", "demand_daily", ["domain"])
    op.create_table(
        "feed_downloads_daily",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("day", sa.Date(), nullable=False),
        sa.Column("feed", sa.String(length=100), nullable=False),
        sa.Column("downloads", sa.Integer(), nullable=False),
        sa.Column("callers", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_feed_downloads_daily")),
        sa.UniqueConstraint("day", "feed", name=op.f("uq_feed_downloads_daily_day_feed")),
    )


def downgrade() -> None:
    op.drop_table("feed_downloads_daily")
    op.drop_table("demand_daily")
