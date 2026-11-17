"""threat candidates from outside lists, and our own detector's verdicts

``threat_feed_entries``: hosts third-party threat lists name, kept as candidates only (threats/feeds.py).
``detector_verdicts``: what the page-proof detector said about scanned pages (threats/detector.py). The
``cat-egory-detector`` data source is added by ``python -m database.seed``.

Revision ID: 0013
Revises: 0012
Create Date: 2026-11-17
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0013"
down_revision: Union[str, Sequence[str], None] = "0012"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "threat_feed_entries",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("source_id", sa.BigInteger(), nullable=False),
        sa.Column("host", sa.String(length=253), nullable=False),
        sa.Column("url", sa.Text(), nullable=True),
        sa.Column("relevance", sa.SmallInteger(), server_default=sa.text("0"), nullable=False),
        sa.Column("brand", sa.String(length=100), nullable=True),
        sa.Column("is_listed", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("first_seen", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("last_seen", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("picked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("domain_id", sa.BigInteger(), nullable=True),
        sa.ForeignKeyConstraint(["source_id"], ["data_sources.id"], name=op.f("fk_threat_feed_entries_source_id_data_sources"),
                                ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["domain_id"], ["domains.id"], name=op.f("fk_threat_feed_entries_domain_id_domains"),
                                ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_threat_feed_entries")),
        sa.UniqueConstraint("source_id", "host", name=op.f("uq_threat_feed_entries_source_id_host")),
    )
    op.create_index("ix_threat_feed_entries_host", "threat_feed_entries", ["host"])
    op.create_index("ix_threat_feed_entries_pick", "threat_feed_entries", ["is_listed", "picked_at", "relevance"])
    op.create_table(
        "detector_verdicts",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("domain_id", sa.BigInteger(), nullable=False),
        sa.Column("scan_result_id", sa.BigInteger(), nullable=True),
        sa.Column("rule", sa.String(length=64), nullable=False),
        sa.Column("positive", sa.Boolean(), nullable=False),
        sa.Column("signals", postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'::jsonb"),
                  nullable=False),
        sa.Column("action", sa.String(length=16), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["domain_id"], ["domains.id"], name=op.f("fk_detector_verdicts_domain_id_domains"),
                                ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["scan_result_id"], ["scan_results.id"],
                                name=op.f("fk_detector_verdicts_scan_result_id_scan_results"), ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_detector_verdicts")),
    )
    op.create_index("ix_detector_verdicts_domain", "detector_verdicts", ["domain_id", "created_at"])
    op.create_index("ix_detector_verdicts_rule", "detector_verdicts", ["rule", "created_at"])


def downgrade() -> None:
    op.drop_table("detector_verdicts")
    op.drop_table("threat_feed_entries")
