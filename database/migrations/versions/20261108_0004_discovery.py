"""discovery scans and source ranks

A scan reason "discovery" for first scans of domains found through discovery sources (queued under a daily
budget), and the position of a domain in its source's list so that popular domains are scanned first.

Revision ID: 0004
Revises: 0003
Create Date: 2026-11-08
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0004"
down_revision: Union[str, Sequence[str], None] = "0003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

REASONS_OLD = ("manual", "import", "scheduled", "community", "rescan")
REASONS_NEW = REASONS_OLD + ("discovery",)


def _reasons(values: tuple[str, ...]) -> None:
    op.drop_constraint(op.f("ck_scan_jobs_scan_reason"), "scan_jobs", type_="check")
    op.create_check_constraint(op.f("ck_scan_jobs_scan_reason"), "scan_jobs",
                               f"reason IN ({', '.join(repr(v) for v in values)})")


def upgrade() -> None:
    _reasons(REASONS_NEW)
    op.add_column("domain_source_records", sa.Column("source_rank", sa.Integer(), nullable=True))
    op.create_index("ix_domain_source_records_source_rank", "domain_source_records", ["source_id", "source_rank"])


def downgrade() -> None:
    op.drop_index("ix_domain_source_records_source_rank", table_name="domain_source_records")
    op.drop_column("domain_source_records", "source_rank")
    op.execute("UPDATE scan_jobs SET reason = 'import' WHERE reason = 'discovery'")
    _reasons(REASONS_OLD)
