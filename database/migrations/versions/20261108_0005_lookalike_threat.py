"""lookalike-domain threat type

Taxonomy 1.1.0 adds the security-threat category ``lookalike-domain`` (code 908): names imitating a well-known
site or brand, confirmed by a reviewer.

Revision ID: 0005
Revises: 0004
Create Date: 2026-11-08
"""

from typing import Sequence, Union

from alembic import op

revision: str = "0005"
down_revision: Union[str, Sequence[str], None] = "0004"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

OLD = ("malware", "phishing", "scam", "fraud", "command-and-control", "malicious-downloads", "suspicious")
NEW = OLD + ("lookalike-domain",)


def _types(values: tuple[str, ...]) -> None:
    op.drop_constraint(op.f("ck_threat_intelligence_threat_type"), "threat_intelligence", type_="check")
    op.create_check_constraint(op.f("ck_threat_intelligence_threat_type"), "threat_intelligence",
                               f"threat_type IN ({', '.join(repr(v) for v in values)})")


def upgrade() -> None:
    _types(NEW)


def downgrade() -> None:
    op.execute("DELETE FROM threat_intelligence WHERE threat_type = 'lookalike-domain'")
    _types(OLD)
