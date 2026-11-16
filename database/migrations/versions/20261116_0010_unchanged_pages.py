"""unchanged pages keep their AI answer

``scan_results.content_fingerprint``: a hash of the page's normalised visible text. ``reused_prediction_id``: set
when a re-scan found the page unchanged and kept the earlier AI answer instead of asking the model again
(classification/unchanged.py).

Revision ID: 0010
Revises: 0009
Create Date: 2026-11-16
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0010"
down_revision: Union[str, Sequence[str], None] = "0009"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("scan_results", sa.Column("content_fingerprint", sa.String(length=64), nullable=True))
    op.add_column("scan_results", sa.Column("reused_prediction_id", sa.BigInteger(), nullable=True))
    op.create_foreign_key(op.f("fk_scan_results_reused_prediction_id_ai_predictions"), "scan_results",
                          "ai_predictions", ["reused_prediction_id"], ["id"], ondelete="SET NULL")
    op.create_index("ix_scan_results_reused_created", "scan_results", ["created_at"],
                    postgresql_where=sa.text("reused_prediction_id IS NOT NULL"))


def downgrade() -> None:
    op.drop_index("ix_scan_results_reused_created", table_name="scan_results")
    op.drop_constraint(op.f("fk_scan_results_reused_prediction_id_ai_predictions"), "scan_results", type_="foreignkey")
    op.drop_column("scan_results", "reused_prediction_id")
    op.drop_column("scan_results", "content_fingerprint")
