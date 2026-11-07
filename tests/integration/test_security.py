"""Cross-cutting security checks on the HTTP surface."""
import pytest

PUBLIC = ["/api/v1/categories", "/api/v1/stats", "/api/v1/feeds", "/healthz"]


@pytest.mark.parametrize("path", PUBLIC + ["/api/v1/domain/nothing.example.com", "/api/v1/admin/me"])
def test_security_headers_everywhere(client, clean, path):
    r = client.get(path)
    assert r.headers["X-Content-Type-Options"] == "nosniff"
    assert r.headers["X-Frame-Options"] == "DENY"
    assert "default-src 'none'" in r.headers["Content-Security-Policy"]
    assert r.headers["X-Request-ID"]
    assert "server" not in {k.lower() for k in r.headers} or "uvicorn" not in r.headers.get("server", "")


def test_errors_use_the_envelope_and_leak_nothing(client, clean):
    for r in (client.get("/api/v1/does-not-exist"), client.delete("/api/v1/categories"),
              client.post("/api/v1/admin/domains", content=b"{not json", headers={"Content-Type": "application/json"})):
        body = r.json()
        assert set(body) == {"error"} and body["error"]["request_id"]
        assert "Traceback" not in r.text and "sqlalchemy" not in r.text.lower()


@pytest.mark.parametrize("payload", ["' OR 1=1 --", "example.com; DROP TABLE domains", "<script>alert(1)</script>.com",
                                     "%00example.com", "example.com\r\nX-Injected: 1", "../../etc/passwd"])
def test_hostile_domain_input_is_rejected(client, clean, payload):
    from urllib.parse import quote

    r = client.get(f"/api/v1/domain/{quote(payload, safe='')}")
    assert r.status_code in (404, 422)
    assert "<script>" not in r.text or r.headers["content-type"].startswith("application/json")


def test_api_key_only_in_header(client, admin):
    key = admin["Authorization"].removeprefix("Bearer ")
    assert client.get(f"/api/v1/admin/me?api_key={key}").status_code == 401
    assert client.get("/api/v1/admin/me", headers={"Authorization": f"Bearer {key}"}).status_code == 200


def test_request_id_is_sanitised(client, clean):
    r = client.get("/healthz", headers={"X-Request-ID": "x" * 500})
    assert len(r.headers["X-Request-ID"]) <= 64
    r = client.get("/healthz", headers={"X-Request-ID": "id\twith-tab"})
    assert r.headers["X-Request-ID"] != "id\twith-tab" and r.headers["X-Request-ID"].isalnum()


def test_cors_only_for_configured_origin(engine, fake_redis, monkeypatch):
    from fastapi.testclient import TestClient
    from sqlalchemy.orm import Session

    from backend.app.deps import get_redis
    from backend.app.main import create_app
    from common.config import get_settings
    from database.session import get_session

    monkeypatch.setattr(get_settings(), "cors_origins", ["http://ui.example.org"])
    app = create_app()

    def _session():
        with Session(engine) as s:
            yield s

    app.dependency_overrides[get_session] = _session
    app.dependency_overrides[get_redis] = lambda: fake_redis
    c = TestClient(app)
    ok = c.options("/api/v1/categories", headers={"Origin": "http://ui.example.org", "Access-Control-Request-Method": "GET"})
    assert ok.headers.get("access-control-allow-origin") == "http://ui.example.org"
    assert "access-control-allow-credentials" not in ok.headers
    bad = c.get("/api/v1/categories", headers={"Origin": "http://evil.example.com"})
    assert "access-control-allow-origin" not in bad.headers


def test_upload_is_not_echoed_as_html(client, admin):
    data = b"<html><script>alert(1)</script></html>\n"
    r = client.post("/api/v1/admin/domains/import", headers=admin, files={"file": ("x.txt", data, "text/html")})
    assert r.headers["content-type"].startswith("application/json")
