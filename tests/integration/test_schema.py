from datetime import UTC, datetime, timedelta

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError

from classification.taxonomy import default_taxonomy
from common.domains import normalize_domain
from database.models import (
    AssignmentOrigin,
    Category,
    DataSource,
    Domain,
    DomainCategory,
    Evidence,
    EvidenceType,
    TaxonomyVersion,
)


def make_domain(session, raw="example.com"):
    n = normalize_domain(raw)
    d = Domain(domain=n.display, normalized_domain=n.ascii, registrable_domain=n.registrable,
               public_suffix=n.public_suffix)
    session.add(d)
    session.flush()
    return d


def test_migrations_match_models(engine):
    from alembic.autogenerate import compare_metadata
    from alembic.migration import MigrationContext

    from database.models import Base

    with engine.connect() as conn:
        diff = compare_metadata(MigrationContext.configure(conn), Base.metadata)
    assert diff == []


def test_seeded_reference_data(session):
    assert session.scalar(select(TaxonomyVersion.version).where(TaxonomyVersion.is_current)) == default_taxonomy().version
    assert session.get(Category, "phishing").kind == "risk"
    assert session.get(Category, "banking").parent_id == "group-business-technology"
    assert session.scalar(select(DataSource).where(DataSource.slug == "cat-egory-ai")).license.slug == "CC-BY-4.0"


def test_duplicate_domains_rejected(session):
    make_domain(session, "Example.com")
    with pytest.raises(IntegrityError):
        make_domain(session, "EXAMPLE.COM.")


def test_normalized_domain_must_be_lowercase(session):
    session.add(Domain(domain="X.com", normalized_domain="X.com", registrable_domain="x.com", public_suffix="com"))
    with pytest.raises(IntegrityError):
        session.flush()


def test_enum_check_constraint(session):
    d = make_domain(session)
    with pytest.raises(IntegrityError):
        session.execute(text("UPDATE domains SET classification_status = 'bogus' WHERE id = :id"), {"id": d.id})


def test_confidence_range_enforced(session):
    d = make_domain(session)
    with pytest.raises(IntegrityError):
        session.execute(text("UPDATE domains SET confidence = 1.5 WHERE id = :id"), {"id": d.id})


def test_category_history_and_single_current_primary(session):
    d = make_domain(session)
    t0 = datetime(2026, 1, 1, tzinfo=UTC)
    first = DomainCategory(domain_id=d.id, category_id="blogs", is_primary=True, confidence=0.8,
                           origin=AssignmentOrigin.AI, taxonomy_version="1.0.0", valid_from=t0)
    session.add(first)
    session.flush()

    # a second current primary is refused
    nested = session.begin_nested()
    session.add(DomainCategory(domain_id=d.id, category_id="news", is_primary=True, origin=AssignmentOrigin.AI,
                               taxonomy_version="1.0.0"))
    with pytest.raises(IntegrityError):
        session.flush()
    nested.rollback()

    # re-classification: close the old row, open a new one -> history is kept
    t1 = t0 + timedelta(days=30)
    first.valid_to = t1
    session.add(DomainCategory(domain_id=d.id, category_id="news", is_primary=True, confidence=0.95,
                               origin=AssignmentOrigin.HUMAN, taxonomy_version="1.0.0", valid_from=t1))
    session.flush()

    def primary_at(ts):
        return session.scalar(select(DomainCategory.category_id).where(
            DomainCategory.domain_id == d.id, DomainCategory.is_primary, DomainCategory.valid_from <= ts,
            (DomainCategory.valid_to.is_(None)) | (DomainCategory.valid_to > ts)))

    assert primary_at(t0 + timedelta(days=1)) == "blogs"
    assert primary_at(t1 + timedelta(days=1)) == "news"


def test_unknown_category_rejected_by_fk(session):
    d = make_domain(session)
    session.add(DomainCategory(domain_id=d.id, category_id="made-up", origin=AssignmentOrigin.AI,
                               taxonomy_version="1.0.0"))
    with pytest.raises(IntegrityError):
        session.flush()


def test_evidence_needs_exactly_one_owner(session):
    session.add(Evidence(type=EvidenceType.HTML, observation="orphan"))
    with pytest.raises(IntegrityError):
        session.flush()


def test_downgrade_and_upgrade_roundtrip(engine):
    cfg = Config("alembic.ini")
    command.downgrade(cfg, "base")
    with engine.connect() as conn:
        assert conn.execute(text("SELECT count(*) FROM pg_tables WHERE schemaname='public' "
                                 "AND tablename <> 'alembic_version'")).scalar() == 0
    command.upgrade(cfg, "head")
    from sqlalchemy.orm import Session

    from classification.taxonomy import default_taxonomy
    from database.seed import sync_builtin_sources, sync_taxonomy

    with Session(engine) as s, s.begin():
        sync_taxonomy(s, default_taxonomy())
        sync_builtin_sources(s)
