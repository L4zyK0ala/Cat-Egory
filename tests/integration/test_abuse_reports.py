"""Abuse-report drafts: contacts from RDAP, drafts for our own confirmed threats, sent only by a person."""
from datetime import UTC, datetime

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from classification.engine import record_threats
from database.models import AbuseReport, Domain, JobStatus, ScanJob, ScanReason, ScanResult
from threats import abuse
from threats.records import withdraw

SITE = "https://cat-egory.org"


def vcard(name=None, email=None):
    items = [["version", {}, "text", "4.0"]]
    if name:
        items.append(["fn", {}, "text", name])
    if email:
        items.append(["email", {}, "text", email])
    return ["vcard", items]


DOCS = {
    abuse.BOOTSTRAP["dns"]: {"services": [[["net", "com"], ["http://rdap.example-registry.net/", "https://rdap.example-registry.net/"]]]},
    abuse.BOOTSTRAP["ipv4"]: {"services": [[["93.0.0.0/8"], ["https://rdap.example-rir.net/"]],
                                           [["93.184.0.0/16"], ["https://rdap.more-specific.net/"]]]},
    abuse.BOOTSTRAP["ipv6"]: {"services": []},
    "https://rdap.example-registry.net/domain/evil.example.net": {
        "entities": [{"roles": ["registrar"], "vcardArray": vcard("Example Registrar Inc."),
                      "entities": [{"roles": ["abuse"], "vcardArray": vcard("Abuse", "Abuse@Registrar.Example")}]}]},
    "https://rdap.more-specific.net/ip/93.184.215.14": {
        "name": "EXAMPLE-NET",
        "entities": [{"roles": ["registrant"], "vcardArray": vcard("Example Hosting Ltd"),
                      "entities": [{"roles": ["abuse"], "vcardArray": vcard("NOC", "abuse@hosting.example")}]}]},
}


def fake_fetch(urls=None):
    def fetch(url):
        if urls is not None:
            urls.append(url)
        return DOCS.get(url)
    return fetch


def test_contacts_from_rdap():
    rdap = abuse.Rdap(fake_fetch())
    doc, server = rdap.domain("evil.example.net")
    c = abuse.registrar_contact(doc, server)
    assert (c.kind, c.email, c.organisation, server) == ("registrar", "abuse@registrar.example", "Example Registrar Inc.",
                                                         "https://rdap.example-registry.net/")
    doc, server = rdap.ip("93.184.215.14")
    c = abuse.hosting_contact(doc, server)
    assert (c.email, c.organisation, server) == ("abuse@hosting.example", "Example Hosting Ltd", "https://rdap.more-specific.net/")
    assert rdap.domain("evil.example.tr") == (None, None)  # no RDAP for .tr
    assert abuse.registrar_contact({"entities": [{"roles": ["registrar"], "vcardArray": vcard("R", "not an address")}]},
                                   "x") is None


def test_rdap_is_https_only():
    with pytest.raises(ValueError):
        abuse.http_fetch(httpx.Client())("http://rdap.example.net/domain/x.net")


def test_the_report_text():
    subject, body = abuse.compose(domain="evil.example.net", types=["lookalike-domain", "phishing"],
                                  first_seen=datetime(2026, 11, 8, 9, 30, tzinfo=UTC),
                                  url="https://evil.example.net/login", ip="93.184.215.14", reviewed=False,
                                  evidence="Octryx saw a page imitating Ziraat", site_url=SITE,
                                  contact=abuse.Contact("hosting", "abuse@hosting.example", None, "x"))
    assert subject == "Abuse report: a phishing website at evil.example.net"
    assert "hxxps://evil[.]example[.]net/login" in body and "93.184.215.14" in body
    assert "2026-11-08 09:30 UTC" in body and "imitating Ziraat" in body
    assert f"{SITE}/domain/evil.example.net" in body
    assert "USOM" not in body and "VirusTotal" not in body


# --------------------------------------------------------------------------- with the database


def domain_with_scan(engine, client, admin, name, threats, ip="93.184.215.14"):
    client.post("/api/v1/admin/domains", headers=admin, json={"domains": [name], "source": "manual", "scan": "none"})
    with Session(engine) as s, s.begin():
        d = s.scalar(select(Domain).where(Domain.normalized_domain == name))
        if threats:
            record_threats(s, d, threats)
        job = ScanJob(domain_id=d.id, reason=ScanReason.SCHEDULED, status=JobStatus.SUCCEEDED)
        s.add(job)
        s.flush()
        s.add(ScanResult(scan_job_id=job.id, domain_id=d.id, requested_url=f"https://{name}/",
                         final_url=f"https://{name}/login", http_status=200, resolved_ips=[ip] if ip else []))


def run(engine, fetch=None):
    with Session(engine) as s:
        return abuse.draft_due(s, abuse.Rdap(fetch or fake_fetch()), limit=10, repeat_days=30, site_url=SITE, pause=0)


def reports(engine):
    with Session(engine) as s:
        return {(r.kind, r.recipient, r.status) for r in s.scalars(select(AbuseReport))}


def test_drafts_for_our_confirmed_threats(engine, client, admin):
    domain_with_scan(engine, client, admin, "evil.example.net", ["phishing"])
    domain_with_scan(engine, client, admin, "parked-lookalike.example.net", ["lookalike-domain"])  # name only
    domain_with_scan(engine, client, admin, "clean.example.net", [])
    assert run(engine) == {"domains": 1, "drafts": 2, "no_contact": 0, "errors": 0}
    assert reports(engine) == {("registrar", "abuse@registrar.example", "draft"),
                               ("hosting", "abuse@hosting.example", "draft")}
    assert run(engine)["domains"] == 0  # not again within 30 days


def test_no_contact_and_failures(engine, client, admin):
    domain_with_scan(engine, client, admin, "evil.example.tr", ["scam"], ip="10.0.0.5")  # no RDAP, private IP
    assert run(engine)["no_contact"] == 1
    assert reports(engine) == {("none", None, "no_contact")}

    domain_with_scan(engine, client, admin, "evil.example.net", ["phishing"])

    def broken(url):
        raise httpx.ConnectError("down")
    assert run(engine, broken)["errors"] == 1
    assert len(reports(engine)) == 1  # tried again next run, not marked as having no contact


def test_a_person_sends_or_dismisses(engine, client, admin):
    domain_with_scan(engine, client, admin, "evil.example.net", ["phishing"])
    run(engine)
    page = client.get("/api/v1/admin/abuse-reports", headers=admin).json()
    assert page["counts"]["draft"] == 2 and all(i["threat_active"] for i in page["items"])
    assert all(i["web_form"] is None for i in page["items"])
    first, second = page["items"]
    sent = client.post(f"/api/v1/admin/abuse-reports/{first['id']}/sent", headers=admin)
    assert sent.status_code == 200 and sent.json()["status"] == "sent" and sent.json()["decided_at"]
    assert client.post(f"/api/v1/admin/abuse-reports/{first['id']}/sent", headers=admin).status_code == 409
    assert client.post(f"/api/v1/admin/abuse-reports/{second['id']}/dismiss", headers=admin).json()["status"] == "dismissed"
    assert client.post("/api/v1/admin/abuse-reports/999999/sent", headers=admin).status_code == 404

    with Session(engine) as s, s.begin():
        withdraw(s, domain=s.scalar(select(Domain).where(Domain.normalized_domain == "evil.example.net")))
    done = client.get("/api/v1/admin/abuse-reports?status=sent", headers=admin).json()["items"]
    assert [i["threat_active"] for i in done] == [False]  # the site is no longer flagged


def test_reports_are_staff_only(client):
    assert client.get("/api/v1/admin/abuse-reports").status_code == 401


def test_providers_that_prefer_a_web_form(engine, client, admin):
    docs = dict(DOCS)
    docs["https://rdap.more-specific.net/ip/93.184.215.14"] = {
        "entities": [{"roles": ["abuse"], "vcardArray": vcard("Abuse", "abuse@cloudflare.com")}]}
    domain_with_scan(engine, client, admin, "evil.example.net", ["phishing"])
    run(engine, lambda url: docs.get(url))
    forms = {i["recipient"]: i["web_form"] for i in client.get("/api/v1/admin/abuse-reports", headers=admin).json()["items"]}
    assert forms == {"abuse@cloudflare.com": "https://abuse.cloudflare.com/", "abuse@registrar.example": None}
