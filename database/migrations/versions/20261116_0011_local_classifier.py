"""local classifier (Yaş Mama)

Training snapshot, trained models and shadow-mode guesses of the local classifier (classification/local.py).
Internal only: nothing that publishes, reviews or measures public accuracy reads these tables.

Revision ID: 0011
Revises: 0010
Create Date: 2026-11-16
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0011"
down_revision: Union[str, Sequence[str], None] = "0010"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

CONFIDENCE = sa.Numeric(precision=4, scale=3)


def _range(table: str, column: str) -> sa.CheckConstraint:
    return sa.CheckConstraint(f"{column} IS NULL OR ({column} >= 0 AND {column} <= 1)",
                              name=op.f(f"ck_{table}_{column}_range"))


def upgrade() -> None:
    op.create_table(
        "training_examples",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("domain_id", sa.BigInteger(), nullable=False),
        sa.Column("label", sa.String(length=64), nullable=False),
        sa.Column("origin", sa.String(length=16), nullable=False),
        sa.Column("confidence", CONFIDENCE, nullable=True),
        sa.Column("scan_result_id", sa.BigInteger(), nullable=True),
        sa.Column("host", sa.String(length=253), nullable=False),
        sa.Column("title", sa.Text(), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("text", sa.Text(), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        _range("training_examples", "confidence"),
        sa.ForeignKeyConstraint(["domain_id"], ["domains.id"], name=op.f("fk_training_examples_domain_id_domains"),
                                ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["scan_result_id"], ["scan_results.id"],
                                name=op.f("fk_training_examples_scan_result_id_scan_results"), ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_training_examples")),
        sa.UniqueConstraint("domain_id", name=op.f("uq_training_examples_domain_id")),
    )
    op.create_table(
        "local_models",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("version", sa.String(length=32), nullable=False),
        sa.Column("object_key", sa.Text(), nullable=True),
        sa.Column("is_current", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("train_examples", sa.Integer(), nullable=False),
        sa.Column("test_examples", sa.Integer(), nullable=False),
        sa.Column("metrics", postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'::jsonb"),
                  nullable=False),
        sa.Column("trained_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_local_models")),
        sa.UniqueConstraint("version", name=op.f("uq_local_models_version")),
    )
    op.create_table(
        "local_predictions",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("domain_id", sa.BigInteger(), nullable=False),
        sa.Column("scan_result_id", sa.BigInteger(), nullable=True),
        sa.Column("ai_prediction_id", sa.BigInteger(), nullable=True),
        sa.Column("model_version", sa.String(length=32), nullable=False),
        sa.Column("primary_category_id", sa.String(length=64), nullable=False),
        sa.Column("confidence", CONFIDENCE, nullable=False),
        sa.Column("top", postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'[]'::jsonb"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        _range("local_predictions", "confidence"),
        sa.ForeignKeyConstraint(["domain_id"], ["domains.id"], name=op.f("fk_local_predictions_domain_id_domains"),
                                ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["scan_result_id"], ["scan_results.id"],
                                name=op.f("fk_local_predictions_scan_result_id_scan_results"), ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["ai_prediction_id"], ["ai_predictions.id"],
                                name=op.f("fk_local_predictions_ai_prediction_id_ai_predictions"), ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_local_predictions")),
    )
    op.create_index("ix_local_predictions_ai_prediction_id", "local_predictions", ["ai_prediction_id"])
    op.create_index("ix_local_predictions_created_at", "local_predictions", ["created_at"])


def downgrade() -> None:
    op.drop_table("local_predictions")
    op.drop_table("local_models")
    op.drop_table("training_examples")
