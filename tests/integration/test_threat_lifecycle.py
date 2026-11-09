"""Confirmed threats follow what the scans see: a dead site expires, a returning one goes back to a person."""
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from classification.engine import THREAT_RETURNED, open_review, record_threats, threat_candidate
from crawler.result import CrawlResult
from database.models import AIPrediction, AuditLog, Domain, ReviewStatus, ReviewTask, ThreatIntelligence, ValidationStatus
from tests.integration.test_scan_pipeline import S, run_precheck
from threats.lifecycle import after_scan, ai_answer_needed, observe
from threats.records import withdraw

NOW = datetime(2026, 11, 30, tzinfo=UTC)


def result(status=200, error=None):
    return CrawlResult(job_id="x", domain="evil.example.net", requested_url="https://evil.example.net/",
                       http_status=status, error_code=error)


@pytest.mark.parametrize(("res", "state"), [
    (result(200), "alive"), (result(302), "alive"), (result(404), "dead"), (result(410), "dead"),
    (result(None, "dns_nxdomain"), "dead"), (result(None, "dns_no_address"), "dead"),
    (result(None, "connection_failed"), "dead"), (result(403), None), (result(503), None),
    (result(None, "timeout"), None), (result(None, "robots_unavailable"), None),
    (result(None, "sandbox_unavailable"), None), (result(None, "internal_error"), None),
])
def test_what_a_scan_says(res, state):
    assert observe(res) == state


def flagged(engine, client, admin, last_seen=NOW - timedelta(days=15)):
    client.post("/api/v1/admin/domains", headers=admin,
                json={"domains": ["evil.example.net"], "source": "manual", "scan": "none"})
    with Session(engine) as s, s.begin():
        d = s.scalar(select(Domain).where(Domain.normalized_domain == "evil.example.net"))
        record_threats(s, d, ["phishing", "lookalike-domain"])
        s.flush()
        s.execute(update(ThreatIntelligence).values(last_seen=last_seen))


def scan(engine, res, now=NOW, days=14):
    with Session(engine) as s, s.begin():
        d = s.scalar(select(Domain).where(Domain.normalized_domain == "evil.example.net"))
        return after_scan(s, d, res, expire_days=days, now=now)


def records(engine):
    with Session(engine) as s:
        return s.scalars(select(ThreatIntelligence)).all()


def open_tasks(engine):
    with Session(engine) as s:
        return [t.reason for t in s.scalars(select(ReviewTask).where(ReviewTask.status == ReviewStatus.OPEN))]


def test_a_live_site_keeps_its_flag_fresh(engine, client, admin):
    flagged(engine, client, admin)
    assert scan(engine, result(200)) is None
    assert all(r.is_active and r.last_seen == NOW for r in records(engine))


def test_a_site_dead_long_enough_expires(engine, client, admin):
    flagged(engine, client, admin, last_seen=NOW - timedelta(days=13))
    assert scan(engine, result(None, "dns_nxdomain")) is None  # not long enough
    assert all(r.is_active for r in records(engine))
    change = scan(engine, result(None, "dns_nxdomain"), now=NOW + timedelta(days=2))
    assert (change.action, change.types) == ("expire", ["lookalike-domain", "phishing"])
    assert not any(r.is_active for r in records(engine))
    assert all(r.raw["expired_at"] for r in records(engine))
    assert open_tasks(engine) == []


def test_silence_is_not_death(engine, client, admin):
    flagged(engine, client, admin, last_seen=NOW - timedelta(days=90))
    for res in (result(None, "timeout"), result(503), result(None, "sandbox_unavailable")):
        assert scan(engine, res) is None
    assert all(r.is_active for r in records(engine))


def test_a_returning_site_goes_to_a_person_and_is_not_flagged_again(engine, client, admin):
    flagged(engine, client, admin)
    scan(engine, result(None, "dns_nxdomain"))
    change = scan(engine, result(200), now=NOW + timedelta(days=3))
    assert change.action == "return"
    assert open_tasks(engine) == [THREAT_RETURNED]
    assert not any(r.is_active for r in records(engine))
    with Session(engine) as s:
        assert s.scalar(select(ReviewTask).where(threat_candidate())) is not None
    assert scan(engine, result(200), now=NOW + timedelta(days=4)) is None  # asked once


def test_a_later_model_answer_does_not_move_it_out_of_the_threat_queue(engine, client, admin):
    flagged(engine, client, admin)
    scan(engine, result(None, "dns_nxdomain"))
    scan(engine, result(200), now=NOW + timedelta(days=3))
    with Session(engine) as s, s.begin():
        d = s.scalar(select(Domain).where(Domain.normalized_domain == "evil.example.net"))
        open_review(s, d, None, "low_confidence")
    assert open_tasks(engine) == [THREAT_RETURNED]


def test_withdrawn_flags_never_come_back_for_review(engine, client, admin):
    flagged(engine, client, admin)
    with Session(engine) as s, s.begin():
        withdraw(s, domain=s.scalar(select(Domain).where(Domain.normalized_domain == "evil.example.net")))
    assert scan(engine, result(200)) is None
    assert open_tasks(engine) == []


def test_flagging_again_clears_the_expiry_mark(engine, client, admin):
    flagged(engine, client, admin)
    scan(engine, result(None, "dns_nxdomain"))
    with Session(engine) as s, s.begin():
        record_threats(s, s.scalar(select(Domain).where(Domain.normalized_domain == "evil.example.net")), ["phishing"])
    row = next(r for r in records(engine) if str(r.threat_type) == "phishing")
    assert row.is_active and "expired_at" not in (row.raw or {})


def test_the_pipeline_applies_it_and_audits(client, engine, admin):
    from backend.app.scan_tasks import store_result
    from tests.integration.test_scan_pipeline import add

    add(client, admin, "ok.com")
    with Session(engine) as s, s.begin():
        record_threats(s, s.scalar(select(Domain).where(Domain.normalized_domain == "ok.com")), ["phishing"])
        s.flush()
        s.execute(update(ThreatIntelligence).values(last_seen=datetime.now(UTC) - timedelta(days=30)))
    _, job_id = run_precheck(engine, "ok.com")
    dead = CrawlResult(job_id=job_id, domain="ok.com", requested_url="https://ok.com/", error_code="dns_no_address")
    with Session(engine) as s, s.begin():
        store_result(s, dead, job_id, S)
    assert not records(engine)[0].is_active
    with Session(engine) as s:
        assert s.scalar(select(AuditLog.action).where(AuditLog.action == "threat.expire")) == "threat.expire"
        d = s.scalar(select(Domain).where(Domain.normalized_domain == "ok.com"))
        assert d.next_scan > datetime.now(UTC) + timedelta(days=2)  # no active threat left: the normal interval


def predicted(engine, name, age_days, primary="parked-domains"):
    with Session(engine) as s, s.begin():
        d = s.scalar(select(Domain).where(Domain.normalized_domain == name))
        s.add(AIPrediction(domain_id=d.id, provider="x", model="m", prompt_version="1", taxonomy_version="1",
                           validation_status=ValidationStatus.VALID if primary else ValidationStatus.INVALID_CATEGORY,
                           primary_category_id=primary, created_at=NOW - timedelta(days=age_days)))


def needed(engine, name="evil.example.net"):
    with Session(engine) as s:
        d = s.scalar(select(Domain).where(Domain.normalized_domain == name))
        return ai_answer_needed(s, d.id, every_days=30, now=NOW)


def test_a_flagged_domain_with_a_recent_answer_skips_the_model(engine, client, admin):
    flagged(engine, client, admin)
    assert needed(engine)  # no answer yet
    predicted(engine, "evil.example.net", 40)
    assert needed(engine)  # too old
    predicted(engine, "evil.example.net", 2, primary=None)
    assert needed(engine)  # the recent answer was not a valid one
    predicted(engine, "evil.example.net", 2)
    assert not needed(engine)


def test_unflagged_domains_are_never_skipped(engine, client, admin):
    flagged(engine, client, admin)
    predicted(engine, "evil.example.net", 2)
    with Session(engine) as s, s.begin():
        s.execute(update(ThreatIntelligence).values(is_active=False))
    assert needed(engine)


def test_the_persist_task_does_not_call_the_model_then(client, engine, admin, monkeypatch):
    from backend.app import scan_tasks
    from tests.integration.test_scan_pipeline import add, success_result

    sent = []
    monkeypatch.setattr(scan_tasks.celery_app, "send_task", lambda name, **kw: sent.append(name))
    add(client, admin, "ok.com")
    _, job_id = run_precheck(engine, "ok.com")
    scan_tasks.persist(success_result(job_id).to_dict(), job_id)
    assert sent == ["category.classify.scan"]  # an ordinary domain

    sent.clear()
    with Session(engine) as s, s.begin():
        record_threats(s, s.scalar(select(Domain).where(Domain.normalized_domain == "ok.com")), ["phishing"])
        d = s.scalar(select(Domain).where(Domain.normalized_domain == "ok.com"))
        s.add(AIPrediction(domain_id=d.id, provider="x", model="m", prompt_version="1", taxonomy_version="1",
                           validation_status=ValidationStatus.VALID, primary_category_id="parked-domains"))
    client.post("/api/v1/admin/domains", headers=admin, json={"domains": ["ok.com"], "source": "manual", "scan": "all"})
    _, job_id = run_precheck(engine, "ok.com")
    assert scan_tasks.persist(success_result(job_id).to_dict(), job_id) == "succeeded"
    assert sent == []
