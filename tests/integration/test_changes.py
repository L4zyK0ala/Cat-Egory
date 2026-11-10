"""Change feed (/api/v1/changes) and feed diffs (/api/v1/feeds/{name}/diff)."""
from datetime import UTC, datetime, timedelta

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from classification.engine import apply_categories
from database.models import AssignmentOrigin, ClassificationStatus, Domain, FeedRelease, ThreatIntelligence
from tests.integration.test_feeds import TAX, feed_client, run_build, setup_data, store  # noqa: F401 - fixtures
from threats.records import withdraw


def _domain(s, name):
    return s.scalar(select(Domain).where(Domain.normalized_domain == name))


def _reclassify(engine, name, primary):
    with Session(engine) as s, s.begin():
        apply_categories(s, _domain(s, name), primary=primary, secondary=[], confidence=0.9,
                         origin=AssignmentOrigin.AI, taxonomy=TAX, status=ClassificationStatus.CLASSIFIED)


def _events(client, **params):
    r = client.get("/api/v1/changes", params=params)
    assert r.status_code == 200, r.text
    return r.json()


def test_changes_since_a_time(engine, client, admin):
    setup_data(engine, client, admin)
    first = _events(client)
    seen = {(c["domain"], c["kind"], c["old"], c["new"]) for c in first["items"]}
    assert seen == {("casino.example.com", "category", None, "gambling"), ("news.example.com", "category", None, "news"),
                    ("bad.example.com", "threat_added", None, "phishing")}  # the private import never shows
    assert not first["more"] and first["until"] == first["items"][-1]["at"]

    _reclassify(engine, "news.example.com", "gambling")
    _reclassify(engine, "casino.example.com", "gambling")  # same main category: not a change
    with Session(engine) as s, s.begin():
        assert withdraw(s, domain=_domain(s, "bad.example.com")) == 1
    later = _events(client, since=first["until"])["items"]
    assert [(c["domain"], c["kind"], c["old"], c["new"], c["reason"]) for c in later] == [
        ("news.example.com", "category", "news", "gambling", None),
        ("bad.example.com", "threat_removed", None, "phishing", "withdrawn")]
    assert _events(client, since=later[-1]["at"])["items"] == []


def test_an_expired_threat_says_so(engine, client, admin):
    setup_data(engine, client, admin)
    with Session(engine) as s, s.begin():
        s.execute(update(ThreatIntelligence).values(is_active=False, deactivated_at=datetime.now(UTC),
                                                    raw={"expired_at": datetime.now(UTC).isoformat()}))
    removed = [c for c in _events(client)["items"] if c["kind"] == "threat_removed"]
    assert [(c["domain"], c["reason"]) for c in removed] == [("bad.example.com", "expired")]


def test_pages_never_split_one_instant(engine, client, admin):
    setup_data(engine, client, admin)
    every = _events(client)["items"]
    page = _events(client, limit=2)
    assert page["more"] and page["items"] and len(page["items"]) <= 2
    rest = _events(client, since=page["until"])["items"]
    assert [c["domain"] for c in page["items"] + rest] == [c["domain"] for c in every]


def test_changes_limits(client):
    old = (datetime.now(UTC) - timedelta(days=31)).isoformat()
    assert client.get("/api/v1/changes", params={"since": old}).json()["error"]["code"] == "since_too_old"
    assert client.get("/api/v1/changes", params={"limit": 5000}).status_code == 422
    assert client.get("/api/v1/changes", params={"since": "yesterday"}).status_code == 422


def test_feed_diff(engine, client, admin, store, feed_client):  # noqa: F811
    setup_data(engine, client, admin)
    run_build(engine, store, datetime(2026, 11, 7, 12, tzinfo=UTC))
    _reclassify(engine, "news.example.com", "gambling")
    run_build(engine, store, datetime(2026, 11, 8, 12, tzinfo=UTC))

    for ext in ("txt", "hosts", "rpz", "adblock"):
        r = feed_client.get(f"/api/v1/feeds/gambling.{ext}/diff", params={"from": "2026.11.07.1"})
        assert r.status_code == 200 and r.headers["X-Feed-Version"] == "2026.11.08.1"
        assert [ln for ln in r.text.splitlines() if not ln.startswith("#")] == ["+news.example.com"], ext
    gone = feed_client.get("/api/v1/feeds/news.txt/diff", params={"from": "2026.11.07.1"}).text
    assert "# Removed: 1" in gone and gone.splitlines()[-1] == "-news.example.com"
    same = feed_client.get("/api/v1/feeds/gambling.txt/diff", params={"from": "2026.11.08.1"}).text
    assert "# Added: 0" in same and not [ln for ln in same.splitlines() if not ln.startswith("#")]

    with Session(engine) as s:
        assert s.scalar(select(FeedRelease.version).where(FeedRelease.feed == "all.csv").limit(1))
    for name, version in (("all.csv", "2026.11.07.1"), ("gambling.txt", "1999.01.01.1"), ("nope.txt", "2026.11.07.1"),
                          ("../x.txt", "2026.11.07.1")):
        assert feed_client.get(f"/api/v1/feeds/{name}/diff", params={"from": version}).status_code == 404
