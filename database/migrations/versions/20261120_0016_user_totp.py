"""second factor for staff sign-in: TOTP secret (encrypted), last accepted step, backup code hashes

Revision ID: 0016
Revises: 0015
Create Date: 2026-11-20
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0016"
down_revision: Union[str, Sequence[str], None] = "0015"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("users", sa.Column("totp_secret", sa.Text(), nullable=True))
    op.add_column("users", sa.Column("totp_enabled_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("users", sa.Column("totp_last_step", sa.BigInteger(), nullable=True))
    op.add_column("users", sa.Column("totp_backup_codes", postgresql.JSONB(astext_type=sa.Text()),
                                     server_default=sa.text("'[]'::jsonb"), nullable=False))


def downgrade() -> None:
    for column in ("totp_backup_codes", "totp_last_step", "totp_enabled_at", "totp_secret"):
        op.drop_column("users", column)
