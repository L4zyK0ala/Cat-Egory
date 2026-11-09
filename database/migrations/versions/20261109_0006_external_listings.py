"""external listings

Results of checking our domains against outside lists (first: the national SGB/USOM malicious-address list).
Internal only.

Revision ID: 0006
Revises: 0005
Create Date: 2026-11-09
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0006"
down_revision: Union[str, Sequence[str], None] = "0005"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "external_listings",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("domain_id", sa.BigInteger(), nullable=False),
        sa.Column("source", sa.String(length=32), nullable=False),
        sa.Column("listed", sa.Boolean(), nullable=False),
        sa.Column("matches", postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'[]'::jsonb"),
                  nullable=False),
        sa.Column("checked_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["domain_id"], ["domains.id"], name=op.f("fk_external_listings_domain_id_domains"),
                                ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_external_listings")),
        sa.UniqueConstraint("domain_id", "source", name=op.f("uq_external_listings_domain_id_source")),
    )
    op.create_index("ix_external_listings_checked", "external_listings", ["source", "checked_at"])


def downgrade() -> None:
    op.drop_index("ix_external_listings_checked", table_name="external_listings")
    op.drop_table("external_listings")
