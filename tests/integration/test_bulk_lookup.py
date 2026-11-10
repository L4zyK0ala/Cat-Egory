"""Many names at once: same visibility rules as a single lookup, counted against a developer key's quota."""
from sqlalchemy import select
from sqlalchemy.orm import Session

from classification.engine import apply_categories, record_threats
from classification.taxonomy import default_taxonomy
from common.config import get_settings
from database.models import AssignmentOrigin, ClassificationStatus, Domain


def setup(engine, client, admin):
    client.post("/api/v1/admin/domains", headers=admin, json={"domains": ["news.example.com", "bad.example.com", "example.org"],
                                                             "scan": "none"})
    client.post("/api/v1/admin/domains/import", headers=admin, data={"scan": "none"},
                files={"file": ("private.txt", b"secret.example.net\n", "text/plain")})
    with Session(engine) as s, s.begin():
        get = lambda n: s.scalar(select(Domain).where(Domain.normalized_domain == n))  # noqa: E731
        for name in ("news.example.com", "secret.example.net"):
            apply_categories(s, get(name), primary="news", secondary=["sports"], confidence=0.95,
                             origin=AssignmentOrigin.AI, taxonomy=default_taxonomy(), status=ClassificationStatus.CLASSIFIED)
        record_threats(s, get("bad.example.com"), ["phishing"])


def lookup(client, names, headers=None):
    return client.post("/api/v1/lookup", json={"domains": names}, headers=headers or {})


def test_every_name_gets_an_answer_in_order(engine, client, admin):
    setup(engine, client, admin)
    items = lookup(client, ["News.Example.com", "https://www.example.org/a?b=1", "bad.example.com",
                            "unknown.example.net", "not a domain", "secret.example.net"]).json()["items"]
    assert [(i["status"], i["matched"]) for i in items] == [
        ("found", "news.example.com"), ("registrable_domain", "example.org"), ("found", "bad.example.com"),
        ("not_found", None), ("invalid", None), ("not_found", None)]  # a private import stays hidden
    assert (items[0]["primary"], items[0]["secondary"]) == ("news", ["sports"])
    assert items[2]["threats"] == ["phishing"]


def test_staff_see_private_imports_developer_keys_do_not(engine, client, admin):
    setup(engine, client, admin)
    assert lookup(client, ["secret.example.net"], admin).json()["items"][0]["status"] == "found"
    key = client.post("/api/v1/developer/keys", json={"accept_terms": True}).json()["key"]
    dev = {"Authorization": f"Bearer {key}"}
    assert lookup(client, ["secret.example.net"], dev).json()["items"][0]["status"] == "not_found"
    assert client.get("/api/v1/domain/secret.example.net", headers=dev).status_code == 404


def test_size_limits_and_quota(engine, client, monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "bulk_lookup_max_anonymous", 2)
    r = lookup(client, ["a.example.com", "b.example.com", "c.example.com"])
    assert r.status_code == 422 and r.json()["error"]["code"] == "too_many_domains"
    monkeypatch.setattr(settings, "developer_key_daily_quota", 5)
    key = client.post("/api/v1/developer/keys", json={"accept_terms": True}).json()["key"]
    dev = {"Authorization": f"Bearer {key}"}
    assert lookup(client, [f"x{i}.example.com" for i in range(3)], dev).status_code == 200  # uses 3 of 5
    assert lookup(client, [f"y{i}.example.com" for i in range(3)], dev).status_code == 429  # 6 > 5
