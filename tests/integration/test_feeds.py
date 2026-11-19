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


def names(text):
    """The domain names a txt or RPZ feed lists (first field of each record line), compared exactly."""
    return {line.split()[0] for line in text.splitlines() if line.strip() and not line.startswith(("#", ";", "$"))}


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
    # lists use the main category only; the secondary one stays in all.csv
    assert [ln for ln in body(store, engine, "entertainment.hosts").splitlines() if not ln.startswith("#")] == []
    assert "0.0.0.0 casino.example.com" in body(store, engine, "gambling.hosts")

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
    assert r.headers["X-Feed-Version"] and {"casino.example.com"} <= set(names(r.text))
    assert feed_client.get("/api/v1/feeds/gambling.txt", headers={"If-None-Match": r.headers["ETag"]}).status_code == 304
    assert {"bad.example.com"} <= set(names(feed_client.get("/api/v1/feeds/rpz").text))
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


def test_a_flagged_domain_leaves_the_content_lists(engine, client, admin, store):
    """devkiantart.com was confirmed as a look-alike yet stayed in Social Networking: lists are used as allow lists."""
    from threats.records import record_url_threats

    setup_data(engine, client, admin)
    client.post("/api/v1/admin/domains", headers=admin, json={"domains": ["forum.example.org"], "scan": "none"})
    with Session(engine) as s, s.begin():
        get = lambda n: s.scalar(select(Domain).where(Domain.normalized_domain == n))  # noqa: E731
        for name in ("bad.example.com", "forum.example.org"):
            apply_categories(s, get(name), primary="social-networking", secondary=[], confidence=0.95,
                             origin=AssignmentOrigin.HUMAN, taxonomy=TAX, status=ClassificationStatus.VERIFIED)
        record_url_threats(s, "https://forum.example.org/hacked/login", ["phishing"])  # one page, not the site
    run_build(engine, store)
    assert body(store, engine, "social-networking.txt").count("bad.example.com") == 0
    assert {"forum.example.org"} <= set(names(body(store, engine, "social-networking.txt")))
    assert "bad.example.com" not in body(store, engine, "all.csv")
    assert {"bad.example.com"} <= set(names(body(store, engine, "security.rpz")))
    listed = client.get("/api/v1/categories/social-networking/domains").json()["items"]
    assert [d["domain"] for d in listed] == ["forum.example.org"]
    distribution = {d["category"]: d["domains"] for d in client.get("/api/v1/stats").json()["category_distribution"]}
    assert distribution["social-networking"] == 1
    page = client.get("/api/v1/domain/bad.example.com").json()  # the domain page still shows everything
    assert page["risk"]["sources"]


def test_router_profiles_and_the_adblock_format(engine, client, admin, store):
    setup_data(engine, client, admin)
    run_build(engine, store)
    lines = lambda name, mark: [ln for ln in body(store, engine, name).splitlines() if not ln.startswith(mark)]  # noqa: E731
    assert lines("profile-security.txt", "#") == ["bad.example.com"]
    assert lines("profile-family.txt", "#") == ["bad.example.com", "casino.example.com"]  # threats + gambling
    assert "news.example.com" not in body(store, engine, "profile-family.txt")
    assert lines("profile-family.adblock", "!") == ["||bad.example.com^", "||casino.example.com^"]
    assert lines("gambling.adblock", "!") == ["||casino.example.com^"]
    zone = dns.zone.from_text(body(store, engine, "profile-family.rpz"), origin="rpz.cat-egory.")
    assert {str(n) for n in zone.nodes} >= {"casino.example.com", "*.casino.example.com", "bad.example.com"}


def test_threat_data_is_public_domain(engine, client, admin, store):
    from feeds.builder import _content_hash
    from threats.records import withdraw

    setup_data(engine, client, admin)
    client.post("/api/v1/admin/domains", headers=admin, json={"domains": ["gone.example.com", "mistake.example.com"],
                                                               "scan": "none"})
    with Session(engine) as s, s.begin():
        get = lambda n: s.scalar(select(Domain).where(Domain.normalized_domain == n))  # noqa: E731
        record_threats(s, get("gone.example.com"), ["scam"])
        record_threats(s, get("mistake.example.com"), ["phishing"])
        s.flush()
        withdraw(s, domain=get("mistake.example.com"))  # a false positive is not threat data
        from database.models import ThreatIntelligence
        row = s.scalar(select(ThreatIntelligence).where(ThreatIntelligence.domain_id == get("gone.example.com").id))
        row.is_active, row.deactivated_at = False, datetime(2026, 11, 9, tzinfo=UTC)
        row.raw = {"expired_at": "2026-11-09T00:00:00+00:00", "expired_reason": "taken_down"}
    releases = {r.feed: r for r in _releases(engine, store)}

    assert releases["phishing.txt"].license == "CC0-1.0" and releases["security.rpz"].license == "CC0-1.0"
    assert releases["profile-security.adblock"].license == "CC0-1.0"
    assert releases["profile-family.txt"].license == "CC-BY-4.0" and releases["gambling.txt"].license == "CC-BY-4.0"
    phishing = body(store, engine, "phishing.txt")
    assert "# License: CC0-1.0 (https://creativecommons.org/publicdomain/zero/1.0/)" in phishing
    assert "Attribution: not required" in phishing
    assert "; License: CC0-1.0" in body(store, engine, "security.rpz")
    assert "# Attribution: Cat-egory contributors" in body(store, engine, "gambling.txt")

    rows = list(csv.DictReader(ln for ln in body(store, engine, "threats.csv").splitlines() if not ln.startswith("#")))
    assert [(r["domain"], r["threat_type"], r["status"], r["ended_reason"], r["confirmed_by"]) for r in rows] == [
        ("bad.example.com", "phishing", "active", "", "person"),
        ("gone.example.com", "scam", "expired", "taken_down", "person")]
    assert len(rows[0]["last_seen"]) == 10 and rows[1]["ended_at"].startswith("2026-11-09")
    doc = json.loads(body(store, engine, "threats.json"))
    assert doc["meta"]["license"] == "CC0-1.0" and doc["meta"]["entries"] == 2
    assert "mistake.example.com" not in json.dumps(doc)

    old = phishing.replace("CC0-1.0 (https://creativecommons.org/publicdomain/zero/1.0/)",
                           "CC-BY-4.0 (https://creativecommons.org/licenses/by/4.0/)")
    assert _content_hash(old.encode()) != _content_hash(phishing.encode())  # a licence change is a new release
    assert _content_hash(phishing.replace("2026", "2027", 1).encode()) == _content_hash(phishing.encode())


def _releases(engine, store):
    with Session(engine) as s, s.begin():
        return [type("R", (), {"feed": r.feed, "license": r.license}) for r in build(s, store, TAX)]
