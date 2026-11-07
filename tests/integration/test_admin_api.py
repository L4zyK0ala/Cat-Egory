import time
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from common.config import get_settings
from database.models import ApiKey, AuditLog, Domain, DomainSourceRecord, ScanJob

ADD = "/api/v1/admin/domains"


# ----------------------------------------------------------------------------- authentication / authorisation


def test_admin_requires_key(client, clean):
    r = client.post(ADD, json={"domains": ["example.com"]})
    assert r.status_code == 401
    assert r.json()["error"]["code"] == "authentication_required"
    assert r.json()["error"]["request_id"]
    assert r.headers["WWW-Authenticate"] == "Bearer"


@pytest.mark.parametrize("header", ["Bearer nonsense", "Bearer cat_abcdefghij_" + "x" * 43, "Basic abc"])
def test_bad_keys_rejected(client, clean, header):
    r = client.get("/api/v1/admin/me", headers={"Authorization": header})
    assert r.status_code == 401


def test_whoami(client, admin):
    body = client.get("/api/v1/admin/me", headers=admin).json()
    assert body["role"] == "admin" and "domains:write" in body["scopes"]


def test_scope_enforced(client, make_key):
    headers, _ = make_key("admin", scopes=["scans:read"])
    r = client.post(ADD, headers=headers, json={"domains": ["example.com"]})
    assert r.status_code == 403 and r.json()["error"]["code"] == "insufficient_scope"
    assert client.get("/api/v1/admin/scans", headers=headers).status_code == 200


def test_contributor_cannot_manage_domains(client, make_key):
    headers, _ = make_key("contributor")
    assert client.post(ADD, headers=headers, json={"domains": ["example.com"]}).status_code == 403


def test_revoked_and_expired_keys(client, engine, make_key):
    headers, key_id = make_key("admin")
    assert client.get("/api/v1/admin/me", headers=headers).status_code == 200
    with Session(engine) as s, s.begin():
        s.get(ApiKey, key_id).revoked_at = datetime.now(UTC)
    assert client.get("/api/v1/admin/me", headers=headers).status_code == 401

    headers, key_id = make_key("admin")
    with Session(engine) as s, s.begin():
        s.get(ApiKey, key_id).expires_at = datetime.now(UTC) - timedelta(seconds=1)
    assert client.get("/api/v1/admin/me", headers=headers).status_code == 401


def test_deactivated_user_loses_access(client, engine, make_key):
    headers, key_id = make_key("admin")
    with Session(engine) as s, s.begin():
        s.execute(text("UPDATE users SET is_active = false WHERE id = (SELECT user_id FROM api_keys WHERE id = :k)"),
                  {"k": key_id})
    assert client.get("/api/v1/admin/me", headers=headers).status_code == 401


def test_last_used_recorded(client, engine, make_key):
    headers, key_id = make_key("admin")
    client.get("/api/v1/admin/me", headers=headers)
    with Session(engine) as s:
        assert s.get(ApiKey, key_id).last_used_at is not None


# ----------------------------------------------------------------------------- adding domains


def test_add_domains_normalises_dedupes_and_reports(client, engine, admin):
    r = client.post(ADD, headers=admin, json={"domains": [
        "Example.COM", "https://example.com/path", "bücher.de", "localhost", "10.0.0.1", "foo.org"]})
    assert r.status_code == 200, r.text
    body = r.json()
    assert (body["received"], body["valid"], body["created"], body["duplicates_in_input"]) == (6, 3, 3, 1)
    assert {e["code"] for e in body["invalid"]} == {"no_suffix", "ip_address"}
    assert [e["row"] for e in body["invalid"]] == [4, 5]
    assert body["scans_queued"] == 3
    assert "xn--bcher-kva.de" in body["created_domains"]

    again = client.post(ADD, headers=admin, json={"domains": ["EXAMPLE.com", "new.org"]}).json()
    assert (again["created"], again["existing"], again["scans_queued"]) == (1, 1, 1)

    with Session(engine) as s:
        assert s.scalar(select(func.count()).select_from(Domain)) == 4
        d = s.scalar(select(Domain).where(Domain.normalized_domain == "xn--bcher-kva.de"))
        assert d.domain == "bücher.de" and d.source_license == "CC-BY-4.0"
        assert s.scalar(select(func.count()).select_from(DomainSourceRecord)) == 4
        assert s.scalar(select(func.count()).select_from(ScanJob)) == 4


def test_scan_modes(client, engine, admin):
    client.post(ADD, headers=admin, json={"domains": ["a.com"], "scan": "none"})
    r = client.post(ADD, headers=admin, json={"domains": ["a.com", "b.com"], "scan": "all"}).json()
    assert r["scans_queued"] == 2
    r = client.post(ADD, headers=admin, json={"domains": ["a.com", "b.com"], "scan": "all"}).json()
    assert r["scans_queued"] == 0  # already have open jobs


def test_unknown_source_and_limits(client, admin, monkeypatch):
    r = client.post(ADD, headers=admin, json={"domains": ["a.com"], "source": "stevenblack-hosts"})
    assert r.status_code == 422 and r.json()["error"]["code"] == "unknown_source"
    assert client.post(ADD, headers=admin, json={"domains": []}).status_code == 422
    monkeypatch.setattr(get_settings(), "max_import_rows", 2)
    assert client.post(ADD, headers=admin, json={"domains": ["a.com", "b.com", "c.com"]}).status_code == 413


def test_validation_error_does_not_echo_input(client, admin):
    r = client.post(ADD, headers=admin, json={"domains": "secret-looking-value", "priority": 99})
    assert r.status_code == 422
    assert "secret-looking-value" not in r.text
    assert r.json()["error"]["code"] == "validation_error"


# ----------------------------------------------------------------------------- import


def test_import_csv(client, engine, admin):
    data = b"Domain,Notes\nexample.com,x\nnot a domain,y\nexample.com,dup\nfoo.org,z\n"
    r = client.post(f"{ADD}/import", headers=admin, files={"file": ("list.csv", data, "text/csv")},
                    data={"scan": "none"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert (body["created"], body["duplicates_in_input"], body["scans_queued"]) == (2, 1, 0)
    assert body["invalid"][0]["row"] == 3
    with Session(engine) as s:
        log = s.scalar(select(AuditLog).where(AuditLog.action == "domains.import"))
        assert log.after["filename"] == "list.csv" and len(log.after["sha256"]) == 64
        assert log.target_id == "file-import"
        assert s.scalar(select(Domain.source_license)) == "internal"  # private imports are not CC BY


def test_import_rejects_bad_files(client, admin, monkeypatch):
    r = client.post(f"{ADD}/import", headers=admin, files={"file": ("x.exe", b"MZ", "application/octet-stream")})
    assert r.status_code == 415
    monkeypatch.setattr(get_settings(), "max_upload_bytes", 10)
    r = client.post(f"{ADD}/import", headers=admin, files={"file": ("x.txt", b"a" * 11, "text/plain")})
    assert r.status_code == 413


def test_import_10k_rows_fast(client, engine, admin):
    data = "\n".join(f"site{i}.example.org" for i in range(10_000)).encode() + b"\nsite1.example.org\n"
    started = time.monotonic()
    r = client.post(f"{ADD}/import", headers=admin, files={"file": ("big.txt", data, "text/plain")})
    elapsed = time.monotonic() - started
    assert r.status_code == 413  # 10_001 entries > limit
    data = "\n".join(f"site{i}.example.org" for i in range(10_000)).encode()
    started = time.monotonic()
    r = client.post(f"{ADD}/import", headers=admin, files={"file": ("big.txt", data, "text/plain")})
    elapsed = time.monotonic() - started
    assert r.status_code == 200
    assert r.json()["created"] == 10_000 and r.json()["scans_queued"] == 10_000
    assert elapsed < 60, f"import took {elapsed:.1f}s"


# ----------------------------------------------------------------------------- scans


def test_scan_queue_lifecycle(client, admin):
    client.post(ADD, headers=admin, json={"domains": ["a.com", "b.com", "c.com"], "scan": "none"})
    r = client.post("/api/v1/admin/scans", headers=admin, json={"domain": "A.com", "priority": 2})
    assert r.status_code == 201
    job = r.json()
    assert (job["domain"], job["status"], job["reason"], job["priority"]) == ("a.com", "queued", "rescan", 2)

    dup = client.post("/api/v1/admin/scans", headers=admin, json={"domain": "a.com"})
    assert dup.status_code == 409 and dup.json()["error"]["details"]["scan_id"] == job["id"]
    assert client.post("/api/v1/admin/scans", headers=admin, json={"domain": "unknown.com"}).status_code == 404

    cancelled = client.post(f"/api/v1/admin/scans/{job['id']}/cancel", headers=admin)
    assert cancelled.status_code == 200 and cancelled.json()["status"] == "cancelled"
    assert client.post(f"/api/v1/admin/scans/{job['id']}/cancel", headers=admin).status_code == 409
    # once cancelled, a new scan can be queued
    assert client.post("/api/v1/admin/scans", headers=admin, json={"domain": "a.com"}).status_code == 201

    stats = client.get("/api/v1/admin/scans/stats", headers=admin).json()
    assert stats["queued"] == 1 and stats["cancelled"] == 1


def test_scan_pagination(client, admin):
    client.post(ADD, headers=admin, json={"domains": [f"d{i}.com" for i in range(7)]})
    seen, cursor = [], None
    while True:
        page = client.get("/api/v1/admin/scans", headers=admin,
                          params={"limit": 3, **({"cursor": cursor} if cursor else {})}).json()
        seen += [item["id"] for item in page["items"]]
        cursor = page["next_cursor"]
        if not cursor:
            break
    assert len(seen) == len(set(seen)) == 7
    assert client.get("/api/v1/admin/scans", headers=admin, params={"cursor": "garbage"}).status_code == 422
    assert client.get("/api/v1/admin/scans", headers=admin, params={"status": "bogus"}).status_code == 422


def test_writes_are_audited(client, engine, admin):
    client.post(ADD, headers=admin | {"X-Request-ID": "req-123"}, json={"domains": ["a.com"]})
    with Session(engine) as s:
        log = s.scalar(select(AuditLog).where(AuditLog.action == "domains.add"))
        assert log.request_id == "req-123"
        assert log.actor_api_key_id is not None and log.after["created"] == 1


# ----------------------------------------------------------------------------- rate limiting


def test_rate_limit(client, admin, monkeypatch):
    monkeypatch.setattr(get_settings(), "public_rate_limit_per_minute", 2)
    for _ in range(2):
        r = client.get("/api/v1/categories")
        assert r.status_code == 200 and "RateLimit-Remaining" in r.headers
    r = client.get("/api/v1/categories")
    assert r.status_code == 429
    assert r.json()["error"]["code"] == "rate_limited" and "Retry-After" in r.headers
    # an API key has its own, separate budget
    assert client.get("/api/v1/categories", headers=admin).status_code == 200


def test_rate_limit_fails_open_without_redis(client, fake_redis, monkeypatch):
    import redis

    def boom(*a, **k):
        raise redis.ConnectionError("down")

    monkeypatch.setattr(fake_redis, "pipeline", boom)
    assert client.get("/api/v1/categories").status_code == 200
