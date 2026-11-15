"""model comparisons

Answers of several models about the evaluation sites (python -m classification.evaluation compare). Kept apart
from ai_predictions so that nothing that publishes, reviews or measures public accuracy ever reads them.

Revision ID: 0009
Revises: 0008
Create Date: 2026-11-15
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0009"
down_revision: Union[str, Sequence[str], None] = "0008"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "model_comparisons",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("domain_id", sa.BigInteger(), nullable=False),
        sa.Column("scan_result_id", sa.BigInteger(), nullable=True),
        sa.Column("provider", sa.String(length=32), nullable=False),
        sa.Column("model", sa.String(length=100), nullable=False),
        sa.Column("answered_by", sa.String(length=100), nullable=True),
        sa.Column("prompt_version", sa.String(length=32), nullable=False),
        sa.Column("taxonomy_version", sa.String(length=32), nullable=False),
        sa.Column("validation_status", sa.String(length=32), nullable=False),
        sa.Column("validation_errors", postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'[]'::jsonb"),
                  nullable=False),
        sa.Column("primary_category_id", sa.String(length=64), nullable=True),
        sa.Column("secondary_category_ids", postgresql.ARRAY(sa.String(length=64)), server_default=sa.text("'{}'"),
                  nullable=False),
        sa.Column("risk_flags", postgresql.ARRAY(sa.String(length=64)), server_default=sa.text("'{}'"), nullable=False),
        sa.Column("confidence", sa.Numeric(precision=4, scale=3), nullable=True),
        sa.Column("screenshot_available", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("used_screenshot", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("injection_suspected", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("raw_response", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("input_tokens", sa.Integer(), nullable=True),
        sa.Column("output_tokens", sa.Integer(), nullable=True),
        sa.Column("latency_ms", sa.Integer(), nullable=True),
        sa.Column("request_id", sa.String(length=100), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("confidence IS NULL OR (confidence >= 0 AND confidence <= 1)",
                           name=op.f("ck_model_comparisons_confidence_range")),
        sa.ForeignKeyConstraint(["domain_id"], ["domains.id"], name=op.f("fk_model_comparisons_domain_id_domains"),
                                ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["scan_result_id"], ["scan_results.id"],
                                name=op.f("fk_model_comparisons_scan_result_id_scan_results"), ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_model_comparisons")),
    )
    op.create_index("ix_model_comparisons_domain_model", "model_comparisons", ["domain_id", "model", "created_at"])
    op.create_index("ix_model_comparisons_created_at", "model_comparisons", ["created_at"])


def downgrade() -> None:
    op.drop_table("model_comparisons")
