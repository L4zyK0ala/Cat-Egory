"""one-page scans: a scan (and a detector verdict) may be of one page a threat list named

``scan_results.url_id`` / ``detector_verdicts.url_id``: set when the scan was of a single URL (threats/pages.py)
rather than the domain's home page. Such scans never stand for the domain (category, re-scan schedule, threat
lifecycle).

Revision ID: 0014
Revises: 0013
Create Date: 2026-11-18
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0014"
down_revision: Union[str, Sequence[str], None] = "0013"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("scan_results", sa.Column("url_id", sa.BigInteger(), nullable=True))
    op.create_foreign_key("fk_scan_results_url_id_urls", "scan_results", "urls", ["url_id"], ["id"],
                          ondelete="SET NULL")
    op.create_index("ix_scan_results_url", "scan_results", ["url_id"], postgresql_where=sa.text("url_id IS NOT NULL"))
    op.add_column("detector_verdicts", sa.Column("url_id", sa.BigInteger(), nullable=True))
    op.create_foreign_key("fk_detector_verdicts_url_id_urls", "detector_verdicts", "urls", ["url_id"], ["id"],
                          ondelete="SET NULL")


def downgrade() -> None:
    op.drop_constraint("fk_detector_verdicts_url_id_urls", "detector_verdicts", type_="foreignkey")
    op.drop_column("detector_verdicts", "url_id")
    op.drop_index("ix_scan_results_url", table_name="scan_results")
    op.drop_constraint("fk_scan_results_url_id_urls", "scan_results", type_="foreignkey")
    op.drop_column("scan_results", "url_id")
