"""registry rule origin and source kind

Allows category assignments whose origin is a registry allocation rule (only public bodies may register
.gov.tr, only YÖK-recognised universities .edu.tr, ...) and a data source of kind "rule".

Revision ID: 0003
Revises: 0002
Create Date: 2026-11-08
"""

from typing import Sequence, Union

from alembic import op

revision: str = "0003"
down_revision: Union[str, Sequence[str], None] = "0002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

ORIGINS_OLD = ("ai", "human", "community", "source")
ORIGINS_NEW = ORIGINS_OLD + ("rule",)
KINDS_OLD = ("manual", "file_import", "community", "blocklist", "threat_feed", "dataset", "ai")
KINDS_NEW = KINDS_OLD + ("rule",)


def _in(column: str, values: tuple[str, ...]) -> str:
    return f"{column} IN ({', '.join(repr(v) for v in values)})"


def _replace(table: str, name: str, condition: str) -> None:
    op.drop_constraint(op.f(name), table, type_="check")
    op.create_check_constraint(op.f(name), table, condition)


def upgrade() -> None:
    for table in ("domain_categories", "url_categories"):
        _replace(table, f"ck_{table}_assignment_origin", _in("origin", ORIGINS_NEW))
    _replace("data_sources", "ck_data_sources_source_kind", _in("kind", KINDS_NEW))


def downgrade() -> None:
    # Rows created by the rule set must go first, or the old constraints cannot be restored.
    op.execute("DELETE FROM domain_categories WHERE origin = 'rule'")
    op.execute("DELETE FROM url_categories WHERE origin = 'rule'")
    op.execute("DELETE FROM data_sources WHERE kind = 'rule'")
    for table in ("domain_categories", "url_categories"):
        _replace(table, f"ck_{table}_assignment_origin", _in("origin", ORIGINS_OLD))
    _replace("data_sources", "ck_data_sources_source_kind", _in("kind", KINDS_OLD))
