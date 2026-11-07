"""Integration tests run against a real, disposable PostgreSQL database.

Set CATEGORY_TEST_DATABASE_URL to an EMPTY database you are happy to wipe, e.g.
    postgresql+psycopg://category@127.0.0.1:55432/category_test
The schema is built with the real Alembic migrations, then the reference data is seeded.
"""
from __future__ import annotations

import os

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from classification.taxonomy import default_taxonomy
from database.seed import sync_builtin_sources, sync_taxonomy

URL = os.environ.get("CATEGORY_TEST_DATABASE_URL")


def pytest_collection_modifyitems(items):
    for item in items:
        if "tests/integration" in str(item.fspath):
            item.add_marker(pytest.mark.integration)
            if not URL:
                item.add_marker(pytest.mark.skip(reason="CATEGORY_TEST_DATABASE_URL not set"))


@pytest.fixture(scope="session")
def engine():
    eng = create_engine(URL)
    with eng.begin() as conn:
        conn.execute(text("DROP SCHEMA public CASCADE; CREATE SCHEMA public;"))
    os.environ["CATEGORY_DATABASE_URL"] = URL
    from common.config import get_settings

    get_settings.cache_clear()
    command.upgrade(Config("alembic.ini"), "head")
    with Session(eng) as s, s.begin():
        sync_taxonomy(s, default_taxonomy())
        sync_builtin_sources(s)
    yield eng
    eng.dispose()


@pytest.fixture
def session(engine):
    """Each test runs in a transaction that is rolled back."""
    conn = engine.connect()
    trans = conn.begin()
    s = Session(bind=conn, join_transaction_mode="create_savepoint")
    yield s
    s.close()
    trans.rollback()
    conn.close()


# --------------------------------------------------------------------------- API fixtures (Phase 2)


@pytest.fixture
def clean(engine):
    """Remove everything except reference data (categories, sources, licences)."""
    with engine.begin() as conn:
        # TRUNCATE ... CASCADE on users would also wipe source_licenses (reviewed_by FK), so users are deleted.
        conn.execute(text("TRUNCATE domains, audit_logs, api_keys RESTART IDENTITY CASCADE"))
        conn.execute(text("DELETE FROM users"))
    yield


@pytest.fixture
def fake_redis():
    import fakeredis

    return fakeredis.FakeRedis()


@pytest.fixture
def client(engine, fake_redis):
    from fastapi.testclient import TestClient

    from backend.app.deps import get_redis
    from backend.app.main import create_app
    from database.session import get_session

    app = create_app()

    def _session():
        with Session(engine) as s:
            yield s

    app.dependency_overrides[get_session] = _session
    app.dependency_overrides[get_redis] = lambda: fake_redis
    return TestClient(app)


@pytest.fixture
def make_key(engine, clean):
    """make_key(role="admin", scopes=None, **kw) -> (headers, key_row_id)"""
    from backend.cli import create_key, create_user
    from database.models import Role

    counter = {"n": 0}

    def _make(role="admin", scopes=None, **kw):
        counter["n"] += 1
        with Session(engine) as s, s.begin():
            user = create_user(s, email=f"user{counter['n']}@example.org", name="t", role=Role(role))
            key, secret = create_key(s, user, name="test", scopes=scopes, **kw)
            key_id = key.id
        return {"Authorization": f"Bearer {secret}"}, key_id

    return _make


@pytest.fixture
def admin(make_key):
    return make_key("admin")[0]
