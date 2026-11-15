"""What the public API must survive once it is on the internet: oversized bodies, crashes, key guessing, odd methods."""
import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session


def build(engine, fake_redis, *, crash=False):
    from backend.app.deps import get_redis
    from backend.app.main import create_app
    from database.session import get_session

    app = create_app()

    def _session():
        with Session(engine) as s:
            yield s

    app.dependency_overrides[get_session] = _session
    app.dependency_overrides[get_redis] = lambda: fake_redis
    if crash:
        @app.get("/boom")
        def boom():
            raise RuntimeError("secret detail that must not leave the server")
    return app


@pytest.fixture
def public(engine, fake_redis, clean, monkeypatch):
    """The public instance (the shared ``client`` fixture is the admin one, which has room for file uploads)."""
    from common.config import get_settings

    monkeypatch.setattr(get_settings(), "admin_routes", False)
    return TestClient(build(engine, fake_redis))


def counted(method, route, status):
    from backend.app.main import REQUESTS

    return REQUESTS.labels(method, route, status)._value.get()


# --------------------------------------------------------------------------- request body size

def test_a_body_larger_than_the_limit_is_refused_by_its_declared_size(public):
    before = counted("POST", "unmatched", 413)
    r = public.post("/api/v1/lookup", content=b"x" * (1024 * 1024 + 1), headers={"Content-Type": "application/json"})
    assert r.status_code == 413
    assert r.json()["error"]["code"] == "payload_too_large" and r.json()["error"]["request_id"]
    assert r.headers["X-Content-Type-Options"] == "nosniff"
    assert counted("POST", "unmatched", 413) == before + 1  # refused before routing, still counted


def test_a_streamed_body_is_cut_off_at_the_limit(public):
    """No Content-Length (chunked): the size is only known while reading, so the bytes are counted."""
    def chunks():
        for _ in range(64):
            yield b"x" * (64 * 1024)

    r = public.post("/api/v1/lookup", content=chunks(), headers={"Content-Type": "application/json"})
    assert r.status_code == 413 and r.json()["error"]["code"] == "payload_too_large"
    assert r.json()["error"]["request_id"] == r.headers["X-Request-ID"]


def test_a_normal_bulk_lookup_still_fits(public):
    names = [f"site-{i}.example.com" for i in range(100)]
    assert public.post("/api/v1/lookup", json={"domains": names}).status_code == 200


def test_the_public_api_has_the_small_limit_and_the_admin_api_room_for_uploads(engine, fake_redis, monkeypatch):
    from backend.app.body_limit import request_limit
    from common.config import get_settings

    settings = get_settings()
    monkeypatch.setattr(settings, "admin_routes", False)
    assert request_limit(settings) == settings.max_request_bytes == 1024 * 1024
    monkeypatch.setattr(settings, "admin_routes", True)
    assert request_limit(settings) > settings.max_upload_bytes


# --------------------------------------------------------------------------- unhandled errors

def test_a_crash_answers_with_the_envelope_and_is_counted(engine, fake_redis, clean):
    client = TestClient(build(engine, fake_redis, crash=True), raise_server_exceptions=False)
    before = counted("GET", "/boom", 500)
    r = client.get("/boom")
    assert r.status_code == 500
    assert r.json()["error"]["code"] == "internal_error" and r.json()["error"]["request_id"] == r.headers["X-Request-ID"]
    assert "secret detail" not in r.text and "Traceback" not in r.text
    assert r.headers["X-Content-Type-Options"] == "nosniff"
    assert counted("GET", "/boom", 500) == before + 1  # the ApiErrors alert reads this counter


# --------------------------------------------------------------------------- metric labels

def test_unknown_methods_share_one_metric_label(client, clean):
    """The method is client-chosen text; as a label it would let anyone grow the process's memory."""
    from backend.app.main import REQUESTS

    for method in ("AAAA", "BBBB", "CCCC"):
        client.request(method, "/healthz")
    methods = {sample.labels["method"] for metric in REQUESTS.collect() for sample in metric.samples}
    assert "OTHER" in methods and not {"AAAA", "BBBB", "CCCC"} & methods


# --------------------------------------------------------------------------- API key guessing

BOGUS = {"Authorization": "Bearer cat_abcdefghij_" + "0" * 43}


def test_repeated_wrong_keys_are_throttled(client, clean):
    from common.config import get_settings

    limit = get_settings().auth_failure_limit_per_minute
    for _ in range(limit):
        assert client.get("/api/v1/categories", headers=BOGUS).status_code == 401
    r = client.get("/api/v1/categories", headers=BOGUS)
    assert r.status_code == 429 and r.json()["error"]["code"] == "rate_limited" and int(r.headers["Retry-After"]) > 0
    assert client.get("/api/v1/categories").status_code == 200  # visitors without a key are not affected


def test_a_valid_key_does_not_count_as_a_failure(client, make_key):
    from common.config import get_settings

    headers, _ = make_key("admin")
    for _ in range(get_settings().auth_failure_limit_per_minute + 5):
        assert client.get("/api/v1/admin/me", headers=headers).status_code == 200


def test_wrong_keys_still_get_401_when_redis_is_down(engine, clean):
    import fakeredis

    server = fakeredis.FakeServer()
    server.connected = False
    client = TestClient(build(engine, fakeredis.FakeRedis(server=server)))
    assert client.get("/api/v1/categories", headers=BOGUS).status_code == 401


# --------------------------------------------------------------------------- transport

@pytest.mark.parametrize("path", ["/healthz", "/api/v1/categories", "/api/v1/does-not-exist"])
def test_hsts_is_sent(client, clean, path):
    assert client.get(path).headers["Strict-Transport-Security"].startswith("max-age=")


def test_readiness_uses_the_shared_redis_client(client, clean):
    r = client.get("/readyz")
    assert r.status_code == 200 and r.json()["checks"] == {"database": "ok", "redis": "ok"}


# --------------------------------------------------------------------------- watching the public instance

def test_the_public_api_serves_its_counters_on_a_separate_port_only(engine, fake_redis, clean, monkeypatch):
    import socket
    import urllib.request

    from common.config import get_settings

    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    settings = get_settings()
    monkeypatch.setattr(settings, "admin_routes", False)
    monkeypatch.setattr(settings, "metrics_port", port)
    with TestClient(build(engine, fake_redis)) as public:  # entering runs the startup hook
        assert public.get("/metrics").status_code == 404   # what the tunnel forwards has no metrics
        public.get("/api/v1/categories")
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/metrics", timeout=5) as r:  # noqa: S310
            text = r.read().decode()
    assert 'category_http_requests_total{method="GET",route="/api/v1/categories",status="200"}' in text
    assert "process_resident_memory_bytes" in text
    assert "category_domains" not in text  # the database numbers stay on the admin instance


def test_the_admin_instance_opens_no_extra_port(engine, fake_redis, clean, monkeypatch):
    import socket

    from common.config import get_settings

    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    monkeypatch.setattr(get_settings(), "metrics_port", port)
    with TestClient(build(engine, fake_redis)), socket.socket() as s:
        assert s.connect_ex(("127.0.0.1", port)) != 0
