"""USOM report queue: report e-mails and where each confirmed threat stands with the national list

``usom_mails``: one e-mail to the national CERT listing our confirmed threats its list does not have.
``usom_reports``: one row per domain, reported (by which mail) or set aside, and when the list first had it after
the report.

Revision ID: 0015
Revises: 0014
Create Date: 2026-11-19
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0015"
down_revision: str | Sequence[str] | None = "0014"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "usom_mails",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("recipient", sa.String(length=254), nullable=False),
        sa.Column("subject", sa.Text(), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("domains", postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'[]'::jsonb"),
                  nullable=False),
        sa.Column("provider", sa.String(length=32), nullable=True),
        sa.Column("provider_id", sa.String(length=200), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("sent_by_id", sa.BigInteger(), nullable=True),
        sa.CheckConstraint("status IN ('queued', 'sent', 'failed')", name=op.f("ck_usom_mails_status_known")),
        sa.ForeignKeyConstraint(["sent_by_id"], ["users.id"], name=op.f("fk_usom_mails_sent_by_id_users"),
                                ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_usom_mails")),
    )
    op.create_table(
        "usom_reports",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("domain_id", sa.BigInteger(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("status_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("decided_by_id", sa.BigInteger(), nullable=True),
        sa.Column("mail_id", sa.BigInteger(), nullable=True),
        sa.Column("listed_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("status IN ('reported', 'dismissed')", name=op.f("ck_usom_reports_status_known")),
        sa.ForeignKeyConstraint(["domain_id"], ["domains.id"], name=op.f("fk_usom_reports_domain_id_domains"),
                                ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["decided_by_id"], ["users.id"], name=op.f("fk_usom_reports_decided_by_id_users"),
                                ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["mail_id"], ["usom_mails.id"], name=op.f("fk_usom_reports_mail_id_usom_mails"),
                                ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_usom_reports")),
        sa.UniqueConstraint("domain_id", name=op.f("uq_usom_reports_domain_id")),
    )
    op.create_index("ix_usom_reports_status", "usom_reports", ["status", "status_at"])


def downgrade() -> None:
    op.drop_table("usom_reports")
    op.drop_table("usom_mails")
