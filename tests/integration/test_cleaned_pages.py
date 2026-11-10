"""Cleaned-page detection: takedown notices count as gone, a clearly different page goes to a person."""
from datetime import timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from classification.engine import THREAT_PAGE_CHANGED
from crawler.result import CrawlResult
from database.models import (
    Domain,
    ExtractedContent,
    JobStatus,
    ReviewStatus,
    ReviewTask,
    ScanJob,
    ScanReason,
    ScanResult,
    ThreatIntelligence,
)
from tests.integration.test_threat_lifecycle import NOW, flagged, open_tasks, records
from threats.lifecycle import after_scan, ai_answer_needed, observe, page_changed, taken_down

KIT = ("Sign in to continue. Enter your card number, expiry date and the security code to verify your identity. "
       "Ziraat internet subesi musteri numarasi parola giris dogrulama kodu sms onay hesabinizi koruyun")
SHOP = ("Handmade ceramics from Izmir. Bowls, plates, mugs and vases glazed by hand in small batches. Free shipping "
        "over 500 TL. About us, contact, returns policy, wholesale enquiries and gift cards")


def page(title="Giris", text=KIT, url="https://evil.example.net/", status=200, words=None):
    return CrawlResult(job_id="x", domain="evil.example.net", requested_url="https://evil.example.net/",
                       final_url=url, http_status=status, page_title=title, text_excerpt=text,
                       word_count=len(text.split()) if words is None else words)


@pytest.mark.parametrize("res", [
    page("Account Suspended", "Account Suspended. This Account has been suspended. Contact your hosting provider "
                              "for more information.", url="https://evil.example.net/cgi-sys/suspendedpage.cgi"),
    page("Site", "This Account has been suspended. Contact your hosting provider for more information.", status=503),
    page("HESAP", "BU HESAP ASKIYA ALINMIŞTIR. Lütfen hosting sağlayıcınız ile iletişime geçin."),
    page("evil.example.net", "This domain is for sale! Make an offer.", url="https://evil.example.net/"),
    page("evil.example.net", "", url="https://www.sedo.com/search/details/?domain=evil.example.net", words=0),
    page("Welcome to nginx!", "If you see this page, the nginx web server is successfully installed and working."),
    page("Suspected phishing site | Cloudflare", "Warning: Suspected Phishing Site Ahead!"),
    page("evil.example.net", "This domain has expired. If you are the owner, renew it now."),
])
def test_takedown_notices(res):
    assert taken_down(res) and observe(res) == "taken_down"


@pytest.mark.parametrize("res", [
    page(),  # the kit itself
    page("Account Suspended", "Your account has been suspended due to unusual activity. Verify your identity to "
                              "restore access. Card number, expiry date, security code."),  # a lure, not a host notice
    page("Hesabınız askıya alındı", "Hesabınız askıya alındı. Kimliğinizi doğrulamak için giriş yapın."),
    page("Bank", "x " * 500 + " this domain is for sale", words=501),  # too long to be a notice
    page("Bank", "", status=403),
])
def test_lures_and_ordinary_pages_are_not_takedowns(res):
    assert not taken_down(res)


def test_page_changed():
    assert not page_changed("Giris", KIT, "https://evil.example.net/", page())
    assert not page_changed("Giris", KIT + " kampanya", "https://evil.example.net/", page())  # small edit
    assert page_changed("Giris", KIT, "https://evil.example.net/", page("Ceramics", SHOP))
    assert page_changed("Giris", KIT, "https://evil.example.net/", page(url="https://www.google.com/"))
    assert not page_changed("Giris", "", "https://evil.example.net/", page("Giris", "", words=0))
    assert page_changed("Giris", "", "https://evil.example.net/", page("Ceramics", SHOP))


# --------------------------------------------------------------------------- with the database


def stored(engine, res):
    """Store a scan of evil.example.net as the pipeline would; returns its id."""
    with Session(engine) as s, s.begin():
        d = s.scalar(select(Domain).where(Domain.normalized_domain == "evil.example.net"))
        job = ScanJob(domain_id=d.id, reason=ScanReason.SCHEDULED, status=JobStatus.SUCCEEDED)
        s.add(job)
        s.flush()
        row = ScanResult(scan_job_id=job.id, domain_id=d.id, requested_url=res.requested_url,
                         final_url=res.final_url, http_status=res.http_status, page_title=res.page_title)
        s.add(row)
        s.flush()
        s.add(ExtractedContent(scan_result_id=row.id, text_excerpt=res.text_excerpt, word_count=res.word_count))
        return row.id


def rescan(engine, res, now=NOW):
    scan_id = stored(engine, res)
    with Session(engine) as s, s.begin():
        d = s.scalar(select(Domain).where(Domain.normalized_domain == "evil.example.net"))
        return after_scan(s, d, res, expire_days=14, now=now, scan_id=scan_id, change_cooldown_days=7)


def test_a_suspended_site_expires_like_a_dead_one(engine, client, admin):
    flagged(engine, client, admin, last_seen=NOW - timedelta(days=15))
    notice = page("Site", "This Account has been suspended. Contact your hosting provider.")
    change = rescan(engine, notice)
    assert (change.action, change.reason) == ("expire", "taken_down")
    assert all(not r.is_active and r.raw["expired_reason"] == "taken_down" for r in records(engine))


def test_a_suspension_does_not_keep_the_flag_fresh(engine, client, admin):
    flagged(engine, client, admin, last_seen=NOW - timedelta(days=3))
    assert rescan(engine, page("Site", "This domain is for sale!")) is None
    assert all(r.is_active and r.last_seen == NOW - timedelta(days=3) for r in records(engine))


def test_a_changed_page_goes_to_a_person_once(engine, client, admin):
    flagged(engine, client, admin, last_seen=NOW - timedelta(days=1))
    assert rescan(engine, page(), now=NOW) is None  # first scan: nothing to compare with
    assert rescan(engine, page(), now=NOW + timedelta(days=1)) is None  # same kit

    change = rescan(engine, page("Ceramics", SHOP), now=NOW + timedelta(days=2))
    assert change.action == "changed" and change.types == ["lookalike-domain", "phishing"]
    assert open_tasks(engine) == [THREAT_PAGE_CHANGED]
    assert all(r.is_active for r in records(engine))  # a person decides
    with Session(engine) as s:
        d = s.scalar(select(Domain).where(Domain.normalized_domain == "evil.example.net"))
        assert ai_answer_needed(s, d.id, every_days=30, now=NOW + timedelta(days=2))  # show the model's view

    assert rescan(engine, page(), now=NOW + timedelta(days=3)) is None  # cloaking flips back: cooldown
    assert rescan(engine, page("Ceramics", SHOP), now=NOW + timedelta(days=10)) is not None  # after a week, ask again


def _task(engine):
    with Session(engine) as s:
        return s.scalar(select(ReviewTask.id).where(ReviewTask.status == ReviewStatus.OPEN))


def test_a_reviewer_withdraws_or_keeps_the_flag(engine, client, admin):
    flagged(engine, client, admin, last_seen=NOW - timedelta(days=1))
    rescan(engine, page(), now=NOW)
    rescan(engine, page("Ceramics", SHOP), now=NOW + timedelta(days=1))
    task = _task(engine)
    bad = client.post(f"/api/v1/admin/reviews/{task}/decision", headers=admin,
                      json={"action": "withdraw", "risk": ["phishing"]})
    assert bad.status_code == 422
    r = client.post(f"/api/v1/admin/reviews/{task}/decision", headers=admin, json={"action": "withdraw"})
    assert r.status_code == 200, r.text
    assert not any(r.is_active for r in records(engine))
    with Session(engine) as s:
        assert s.scalar(select(ThreatIntelligence.deactivated_at).limit(1)) is not None
    assert client.get("/api/v1/domain/evil.example.net").json()["risk"]["sources"] == []

    # nothing left to withdraw on a later task
    with Session(engine) as s, s.begin():
        s.add(ReviewTask(domain_id=s.scalar(select(Domain.id).where(Domain.normalized_domain == "evil.example.net")),
                         reason=THREAT_PAGE_CHANGED))
    r = client.post(f"/api/v1/admin/reviews/{_task(engine)}/decision", headers=admin, json={"action": "withdraw"})
    assert r.status_code == 409 and r.json()["error"]["code"] == "nothing_to_withdraw"
