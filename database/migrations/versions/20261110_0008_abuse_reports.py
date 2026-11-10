"""abuse reports

Prepared abuse reports to registrars and hosting networks about confirmed threats (sent by a person).

Revision ID: 0008
Revises: 0007
Create Date: 2026-11-10
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0008"
down_revision: Union[str, Sequence[str], None] = "0007"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "abuse_reports",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("domain_id", sa.BigInteger(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("recipient", sa.String(length=254), nullable=True),
        sa.Column("organisation", sa.String(length=200), nullable=True),
        sa.Column("threat_types", postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'[]'::jsonb"),
                  nullable=False),
        sa.Column("subject", sa.Text(), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("lookup", postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'::jsonb"),
                  nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("decided_by_id", sa.BigInteger(), nullable=True),
        sa.CheckConstraint("status IN ('draft', 'sent', 'dismissed', 'no_contact')",
                           name=op.f("ck_abuse_reports_status_known")),
        sa.ForeignKeyConstraint(["domain_id"], ["domains.id"], name=op.f("fk_abuse_reports_domain_id_domains"),
                                ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["decided_by_id"], ["users.id"], name=op.f("fk_abuse_reports_decided_by_id_users"),
                                ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_abuse_reports")),
    )
    op.create_index("ix_abuse_reports_domain_id", "abuse_reports", ["domain_id"])
    op.create_index("ix_abuse_reports_status", "abuse_reports", ["status", "created_at"])


def downgrade() -> None:
    op.drop_table("abuse_reports")
