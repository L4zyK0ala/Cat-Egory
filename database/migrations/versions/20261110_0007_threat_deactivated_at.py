"""threat deactivated_at

When a threat record stopped being active (withdrawn or expired), so changes can be published as a feed.

Revision ID: 0007
Revises: 0006
Create Date: 2026-11-10
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0007"
down_revision: Union[str, Sequence[str], None] = "0006"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("threat_intelligence", sa.Column("deactivated_at", sa.DateTime(timezone=True), nullable=True))
    op.create_index("ix_threat_intelligence_first_seen", "threat_intelligence", ["first_seen"])
    op.create_index("ix_threat_intelligence_deactivated_at", "threat_intelligence", ["deactivated_at"],
                    postgresql_where=sa.text("deactivated_at IS NOT NULL"))
    op.create_index("ix_domain_categories_valid_from", "domain_categories", ["valid_from"])


def downgrade() -> None:
    op.drop_index("ix_domain_categories_valid_from", table_name="domain_categories")
    op.drop_index("ix_threat_intelligence_deactivated_at", table_name="threat_intelligence")
    op.drop_index("ix_threat_intelligence_first_seen", table_name="threat_intelligence")
    op.drop_column("threat_intelligence", "deactivated_at")
