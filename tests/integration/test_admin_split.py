"""The public API must not contain admin, review, moderation or metrics endpoints at all."""
import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session


@pytest.fixture
def public_client(engine, fake_redis, monkeypatch):
    from backend.app.deps import get_redis
    from backend.app.main import create_app
    from common.config import get_settings
    from database.session import get_session

    monkeypatch.setenv("CATEGORY_ADMIN_ROUTES", "false")
    get_settings.cache_clear()
    app = create_app()

    def _session():
        with Session(engine) as s:
            yield s

    app.dependency_overrides[get_session] = _session
    app.dependency_overrides[get_redis] = lambda: fake_redis
    yield TestClient(app)
    get_settings.cache_clear()  # settings cached during this test say "public"; the other tests use the admin instance


ADMIN_PATHS = [("get", "/api/v1/admin/me"), ("get", "/api/v1/admin/scans"), ("post", "/api/v1/admin/domains"),
               ("get", "/api/v1/admin/reviews"), ("post", "/api/v1/admin/feeds/build"),
               ("get", "/api/v1/admin/submissions"), ("get", "/api/v1/admin/providers"), ("get", "/api/v1/admin/threats"),
               ("post", "/api/v1/admin/threats/withdraw"), ("get", "/metrics")]


@pytest.mark.parametrize(("method", "path"), ADMIN_PATHS)
def test_public_api_has_no_admin_endpoints(public_client, admin, method, path):
    r = getattr(public_client, method)(path, headers=admin)  # even with a valid admin key
    assert r.status_code == 404


def test_public_api_keeps_public_endpoints_and_hides_admin_docs(public_client):
    assert public_client.get("/api/v1/categories").status_code == 200
    assert public_client.get("/api/v1/feeds").status_code == 200
    assert public_client.post("/api/v1/community/submit", json={}).status_code == 422  # exists, validates
    paths = public_client.get("/api/v1/openapi.json").json()["paths"]
    assert not [p for p in paths if p.startswith("/api/v1/admin") or p == "/metrics"]


def test_admin_instance_has_them(client, admin):
    assert client.get("/api/v1/admin/me", headers=admin).status_code == 200
    assert client.get("/metrics").status_code == 200
