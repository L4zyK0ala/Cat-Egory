"""Cross-check with the national list (threats/sgb.py): internal records only, polite, never published."""
import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from classification.engine import record_threats
from database.models import Domain, ExternalListing
from threats import sgb

LISTED = [{"id": 11, "value": "evil.example.net", "type": "domain", "category": "PH", "criticality": 4,
           "date": "2026-11-09 07:00:00", "scope": "exact"}]


def setup(engine, client, admin):
    client.post("/api/v1/admin/domains", headers=admin, json={
        "domains": ["evil.example.net", "scam.example.org", "fine.example.com"], "source": "manual", "scan": "none"})
    with Session(engine) as s, s.begin():
        for name, threat in (("evil.example.net", "phishing"), ("scam.example.org", "scam")):
            record_threats(s, s.scalar(select(Domain).where(Domain.normalized_domain == name)), [threat])


def run_check(engine, fetch, **kw):
    with Session(engine) as s, s.begin():
        return sgb.check(s, fetch, limit=kw.pop("limit", 10), pause=0, sleep=lambda _: None, **kw)


def test_check_records_listed_and_missing_and_waits_a_day(engine, client, admin):
    setup(engine, client, admin)
    asked = []

    def fetch(domain):
        asked.append(domain)
        return LISTED if domain == "evil.example.net" else []

    assert run_check(engine, fetch) == {"checked": 2, "listed": 1, "errors": 0}
    assert sorted(asked) == ["evil.example.net", "scam.example.org"]  # fine.example.com has no threat: not asked
    assert run_check(engine, fetch) == {"checked": 0, "listed": 0, "errors": 0}  # checked within 24 h

    report = client.get("/api/v1/admin/threats/sgb-report", headers=admin, params={"status": "all"}).json()
    assert report["counts"] == {"listed": 1, "missing": 1, "unchecked": 0}
    by = {i["domain"]: i for i in report["items"]}
    assert by["scam.example.org"]["status"] == "missing" and by["scam.example.org"]["types"] == ["scam"]
    match = by["evil.example.net"]["sgb"]["matches"][0]
    assert match["category_name"] == "Oltalama"
    assert match["link"] == "https://siberguvenlik.gov.tr/zararli-baglantilar/detay/11"
    missing = client.get("/api/v1/admin/threats/sgb-report", headers=admin).json()["items"]
    assert [i["domain"] for i in missing] == ["scam.example.org"]  # default: the ones to report
    threats = client.get("/api/v1/admin/threats", headers=admin).json()["items"]
    assert {t["domain"]: (t["sgb"] or {}).get("listed") for t in threats} == {
        "evil.example.net": True, "scam.example.org": False}

    # never public: the domain page and stats know nothing about the list
    page = client.get("/api/v1/domain/evil.example.net").text
    assert "sgb" not in page and "siberguvenlik" not in page


def test_an_api_error_stops_the_run(engine, client, admin):
    setup(engine, client, admin)

    def fetch(domain):
        raise httpx.ConnectError("down")

    assert run_check(engine, fetch) == {"checked": 0, "listed": 0, "errors": 1}
    with Session(engine) as s:
        assert s.scalar(select(ExternalListing.id)) is None  # nothing recorded, retried next hour


def test_http_fetch_pages_and_filters(engine):
    calls = []

    def handler(request):
        calls.append(dict(request.url.params))
        page = int(request.url.params["page"])
        models = [{"id": 1, "url": "evil.example.net", "type": "domain"}] if page == 0 else \
            [{"id": 2, "url": "xevil.example.net", "type": "domain"}]
        return httpx.Response(200, json={"models": models, "pageCount": 2, "totalCount": 2})

    with httpx.Client(transport=httpx.MockTransport(handler)) as http:
        got = sgb.http_fetch(http)("evil.example.net")
    assert [m["id"] for m in got] == [1] and [c["page"] for c in calls] == ["0", "1"]
    assert calls[0]["q"] == "evil.example.net"


def test_report_needs_the_review_scope(client, make_key):
    assert client.get("/api/v1/admin/threats/sgb-report",
                      headers=make_key("contributor")[0]).status_code == 403


def test_each_answer_is_committed_at_once_and_reported(engine, client, admin):
    """The CLI path: no transaction is held open while waiting for the API, and progress is shown."""
    setup(engine, client, admin)
    seen = []

    def fetch(domain):
        if seen:  # by the second lookup the first answer must already be visible to others
            with Session(engine) as other:
                assert other.scalar(select(ExternalListing.domain_id)) is not None
        return []

    with Session(engine) as s:
        counts = sgb.check(s, fetch, limit=10, pause=0, sleep=lambda _: None, commit_each=True,
                           progress=lambda name, matches: seen.append((name, matches)))
    assert counts["checked"] == 2 and sorted(n for n, _ in seen) == ["evil.example.net", "scam.example.org"]
