"""VirusTotal lookups (threats/virustotal.py): for reviewers only, never published, never a threat flag."""
import httpx
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from classification.engine import record_threats
from database.models import Domain, ExternalListing, ThreatIntelligence
from threats import sgb, virustotal

FLAGGED = {"malicious": 3, "suspicious": 1, "harmless": 50, "undetected": 30, "total": 84, "last_analysis": None,
           "engines": [{"name": "Vendor", "category": "malicious", "result": "phishing"}]}
CLEAN = {**FLAGGED, "malicious": 0, "suspicious": 0, "engines": []}


def setup(engine, client, admin):
    names = ["evil.example.net", "scam.example.org", "new.example.org"]
    client.post("/api/v1/admin/domains", headers=admin, json={
        "domains": [*names, "fine.example.com"], "source": "manual", "scan": "none"})
    with Session(engine) as s, s.begin():
        for name in names:
            record_threats(s, s.scalar(select(Domain).where(Domain.normalized_domain == name)), ["phishing"])


def run_check(engine, fetch, module=virustotal):
    with Session(engine) as s, s.begin():
        return module.check(s, fetch, limit=10, pause=0, sleep=lambda _: None)


ANSWERS = {"evil.example.net": [FLAGGED], "scam.example.org": [CLEAN], "new.example.org": []}


def test_answers_reach_reviewers_only(engine, client, admin):
    setup(engine, client, admin)
    asked = []

    def fetch(domain):
        asked.append(domain)
        return ANSWERS[domain]

    with Session(engine) as s:
        flags = s.scalar(select(func.count()).select_from(ThreatIntelligence))
    assert run_check(engine, fetch) == {"checked": 3, "listed": 1, "errors": 0}
    assert "fine.example.com" not in asked  # no threat, no candidate: not sent anywhere
    assert run_check(engine, fetch)["checked"] == 0  # checked within 72 hours

    report = client.get("/api/v1/admin/threats/sgb-report", headers=admin, params={"status": "all"}).json()
    by = {i["domain"]: i["vt"] for i in report["items"]}
    assert (by["evil.example.net"]["flagged"], by["evil.example.net"]["malicious"]) == (True, 3)
    assert by["evil.example.net"]["link"] == "https://www.virustotal.com/gui/domain/evil.example.net"
    assert by["evil.example.net"]["engines"][0]["name"] == "Vendor"
    assert (by["scam.example.org"]["known"], by["scam.example.org"]["flagged"]) == (True, False)
    assert (by["new.example.org"]["known"], by["new.example.org"]["flagged"]) == (False, False)
    threats = client.get("/api/v1/admin/threats", headers=admin).json()["items"]
    assert {t["domain"]: t["vt"]["flagged"] for t in threats}["evil.example.net"] is True

    page = client.get("/api/v1/domain/evil.example.net").text
    assert "virustotal" not in page.lower()
    with Session(engine) as s:  # an answer never creates a flag
        assert s.scalar(select(func.count()).select_from(ThreatIntelligence)) == flags


def test_sources_are_tracked_apart(engine, client, admin):
    setup(engine, client, admin)
    assert run_check(engine, lambda d: [], module=sgb)["checked"] == 3
    assert run_check(engine, lambda d: ANSWERS[d])["checked"] == 3  # the SGB check does not use up VirusTotal's turn
    report = client.get("/api/v1/admin/threats/sgb-report", headers=admin, params={"status": "all"}).json()
    by = {i["domain"]: i for i in report["items"]}
    assert by["evil.example.net"]["status"] == "missing" and by["evil.example.net"]["vt"]["flagged"] is True


def test_a_used_up_quota_stops_the_run(engine, client, admin):
    setup(engine, client, admin)
    request = httpx.Request("GET", "https://www.virustotal.com/")
    calls = []

    def fetch(domain):
        calls.append(domain)
        raise httpx.HTTPStatusError("quota", request=request, response=httpx.Response(429, request=request))

    assert run_check(engine, fetch) == {"checked": 0, "listed": 0, "errors": 1}
    assert len(calls) == 1
    with Session(engine) as s:
        assert s.scalar(select(ExternalListing.id)) is None


def test_no_key_means_off(monkeypatch):
    from backend.app import threat_tasks
    from common.config import get_settings

    monkeypatch.delenv("CATEGORY_VIRUSTOTAL_API_KEY", raising=False)
    get_settings.cache_clear()
    assert threat_tasks.virustotal_key() == ""
    assert threat_tasks.run_virustotal_check()["disabled"] == 1
