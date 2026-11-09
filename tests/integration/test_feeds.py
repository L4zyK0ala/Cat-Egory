import csv
import io
import json
from datetime import UTC, datetime

import dns.zone
import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from backend.app.api.feeds import get_feed_store
from classification.engine import apply_categories, record_threats
from classification.taxonomy import default_taxonomy
from crawler.storage import MemoryStore
from database.models import AssignmentOrigin, ClassificationStatus, Domain, FeedRelease
from feeds.builder import build

TAX = default_taxonomy()


@pytest.fixture
def store():
    return MemoryStore()


@pytest.fixture
def feed_client(client, store):
    client.app.dependency_overrides[get_feed_store] = lambda: store
    return client


def setup_data(engine, client, admin):
    client.post("/api/v1/admin/domains", headers=admin, json={"domains": ["casino.example.com", "news.example.com",
                                                                          "bad.example.com"], "scan": "none"})
    client.post("/api/v1/admin/domains/import", headers=admin, data={"scan": "none"},
                files={"file": ("private.txt", b"secret-casino.example.net\n", "text/plain")})
    with Session(engine) as s, s.begin():
        get = lambda n: s.scalar(select(Domain).where(Domain.normalized_domain == n))  # noqa: E731
        for name, primary, secondary in (("casino.example.com", "gambling", ["entertainment"]),
                                         ("news.example.com", "news", []),
                                         ("secret-casino.example.net", "gambling", [])):
            apply_categories(s, get(name), primary=primary, secondary=secondary, confidence=0.93,
                             origin=AssignmentOrigin.AI, taxonomy=TAX, status=ClassificationStatus.CLASSIFIED)
        record_threats(s, get("bad.example.com"), ["phishing"])


def run_build(engine, store, when=None):
    with Session(engine) as s, s.begin():
        return [(r.feed, r.version, r.record_count) for r in build(s, store, TAX, now=when)]


def body(store, engine, name):
    with Session(engine) as s:
        key = s.scalar(select(FeedRelease.object_key).where(FeedRelease.feed == name).order_by(FeedRelease.id.desc()))
    return store.get(key).decode()


def test_build_formats(engine, client, admin, store):
    setup_data(engine, client, admin)
    releases = run_build(engine, store, datetime(2026, 11, 7, 12, tzinfo=UTC))
    names = {r[0] for r in releases}
    assert {"gambling.txt", "gambling.hosts", "gambling.rpz", "phishing.txt", "security.rpz", "all.csv",
            "all.json"} <= names
    assert all(v == "2026.11.07.1" for _, v, _ in releases)

    gambling = body(store, engine, "gambling.txt")
    assert "# License: CC-BY-4.0" in gambling and "# Version: 2026.11.07.1" in gambling
    assert [ln for ln in gambling.splitlines() if not ln.startswith("#")] == ["casino.example.com"]
    assert "secret-casino" not in gambling  # private import never published
    assert body(store, engine, "entertainment.hosts").splitlines()[-1] == "0.0.0.0 casino.example.com"

    rows = list(csv.reader(io.StringIO("\n".join(ln for ln in body(store, engine, "all.csv").splitlines()
                                                 if not ln.startswith("#")))))
    assert rows[0][:3] == ["domain", "primary_category", "secondary_categories"]
    assert rows[1][:3] == ["casino.example.com", "gambling", "entertainment"] and len(rows) == 3

    doc = json.loads(body(store, engine, "all.json"))
    assert doc["meta"]["license"] == "CC-BY-4.0" and doc["meta"]["entries"] == 2

    for name in ("security.rpz", "gambling.rpz"):
        zone = dns.zone.from_text(body(store, engine, name), origin="rpz.cat-egory.", relativize=True,
                                  check_origin=True)
        assert zone.get_rdataset("@", "SOA").ttl == 300
    zone = dns.zone.from_text(body(store, engine, "security.rpz"), origin="rpz.cat-egory.")
    assert {str(n) for n in zone.nodes} >= {"bad.example.com", "*.bad.example.com"}


def test_unchanged_content_creates_no_release(engine, client, admin, store):
    setup_data(engine, client, admin)
    first = run_build(engine, store)
    assert run_build(engine, store) == []  # same domains -> nothing new, even though timestamps differ
    with Session(engine) as s, s.begin():
        d = s.scalar(select(Domain).where(Domain.normalized_domain == "news.example.com"))
        apply_categories(s, d, primary="gambling", secondary=[], confidence=0.9, origin=AssignmentOrigin.AI,
                         taxonomy=TAX, status=ClassificationStatus.CLASSIFIED)
    changed = {name for name, _, _ in run_build(engine, store)}
    assert {"gambling.txt", "news.txt", "all.csv", "all.json"} <= changed and "phishing.txt" not in changed
    assert len(first) > len(changed)
    with Session(engine) as s:
        assert s.scalar(select(func.count(func.distinct(FeedRelease.version)))) == 2


def test_feed_api(engine, client, admin, store, feed_client):
    assert feed_client.get("/api/v1/feeds/gambling.txt").status_code == 404  # not built yet
    setup_data(engine, client, admin)
    run_build(engine, store)
    r = feed_client.get("/api/v1/feeds/gambling.txt")
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/plain")
    assert r.headers["X-Feed-Version"] and "casino.example.com" in r.text
    assert feed_client.get("/api/v1/feeds/gambling.txt", headers={"If-None-Match": r.headers["ETag"]}).status_code == 304
    assert "bad.example.com" in feed_client.get("/api/v1/feeds/rpz").text
    assert feed_client.get("/api/v1/feeds/all-domains.csv").headers["content-type"].startswith("text/csv")
    for bad in ("../etc/passwd", "gambling.exe", "UPPER.txt", "nope.txt"):
        assert feed_client.get(f"/api/v1/feeds/{bad}").status_code == 404
    index = feed_client.get("/api/v1/feeds").json()
    assert {"name", "url", "version", "records", "sha256", "license"} <= set(index[0])


def test_release_numbers_keep_counting_past_nine(engine):
    from feeds.builder import next_version

    when = datetime(2031, 3, 9, 12, tzinfo=UTC)
    with Session(engine) as s, s.begin():
        for n in (1, 2, 9, 10):
            s.add(FeedRelease(feed="all.csv", version=f"2031.03.09.{n}", license="CC-BY-4.0", taxonomy_version="1",
                              record_count=0, sha256="a" * 64, object_key=f"feeds/{n}", generated_at=when))
        s.add(FeedRelease(feed="all.csv", version="2031.03.08.99", license="CC-BY-4.0", taxonomy_version="1",
                          record_count=0, sha256="a" * 64, object_key="feeds/old", generated_at=when))
    with Session(engine) as s:
        assert next_version(s, when) == "2031.03.09.11"
        assert next_version(s, datetime(2031, 3, 10, tzinfo=UTC)) == "2031.03.10.1"
