from datetime import UTC, datetime, timedelta

import dns.resolver
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.scan_tasks import claim_jobs, lookup_dns, precheck, reap_stale, store_result
from common.config import Settings
from crawler.result import Artifact, CrawlResult
from database.models import ClassificationStatus, Domain, ExtractedContent, JobStatus, ScanJob, ScanResult, Screenshot

S = Settings(_env_file=None, max_in_flight_scans=2)


class Answer(list):
    def __init__(self, items, canonical):
        super().__init__(items)
        self.canonical_name = canonical


class Rec:
    def __init__(self, text):
        self.text = text

    def to_text(self):
        return self.text


class FakeResolver:
    lifetime = 5.0

    def __init__(self, zone):
        self.zone = zone

    def resolve(self, name, rtype):
        if name not in self.zone:
            raise dns.resolver.NXDOMAIN()
        records = self.zone[name].get(rtype)
        if records is None:
            raise dns.resolver.NoAnswer()
        if records == "timeout":
            raise dns.exception.Timeout()
        return Answer([Rec(r) for r in records], self.zone[name].get("canonical", name) + ".")


ZONE = {
    "ok.com": {"A": ["93.184.215.14"], "MX": ["10 mx.ok.com."], "NS": ["ns1.ok.com."]},
    "inside.com": {"A": ["10.1.2.3"]},
    "mixed.com": {"A": ["93.184.215.14", "127.0.0.1"]},
    "slow.com": {"A": "timeout", "AAAA": "timeout"},
    "nomail.com": {},
}


def add(client, admin, *names):
    client.post("/api/v1/admin/domains", headers=admin, json={"domains": list(names)})


def jobs(engine):
    with Session(engine) as s:
        return {s.get(Domain, j.domain_id).normalized_domain: j for j in s.scalars(select(ScanJob))}


def test_lookup_dns():
    info, addrs, err = lookup_dns("ok.com", FakeResolver(ZONE))
    assert err is None and addrs == ["93.184.215.14"] and info["mx"] == ["10 mx.ok.com."]
    assert lookup_dns("missing.com", FakeResolver(ZONE))[2] == "dns_nxdomain"
    assert lookup_dns("nomail.com", FakeResolver(ZONE))[2] == "dns_no_address"
    assert lookup_dns("slow.com", FakeResolver(ZONE))[2] == "dns_timeout"


def test_claim_respects_capacity_and_priority(client, engine, admin):
    add(client, admin, "a.com", "b.com", "c.com")
    with Session(engine) as s, s.begin():
        s.execute(ScanJob.__table__.update().where(ScanJob.domain_id == select(Domain.id).where(
            Domain.normalized_domain == "c.com").scalar_subquery()).values(priority=0))
    with Session(engine) as s, s.begin():
        claimed = claim_jobs(s, S)
    assert len(claimed) == 2
    state = jobs(engine)
    assert state["c.com"].status == JobStatus.RUNNING and state["c.com"].attempts == 1  # priority 0 first
    with Session(engine) as s, s.begin():
        assert claim_jobs(s, S) == []  # at capacity


def run_precheck(engine, name, zone=ZONE):
    with Session(engine) as s, s.begin():
        claim_jobs(s, Settings(_env_file=None, max_in_flight_scans=100))
    job = jobs(engine)[name]
    with Session(engine) as s, s.begin():
        return precheck(s, str(job.id), S, FakeResolver(zone)), str(job.id)


def test_precheck_ok_returns_payload(client, engine, admin):
    add(client, admin, "ok.com")
    payload, job_id = run_precheck(engine, "ok.com")
    assert payload == {"job_id": job_id, "domain": "ok.com", "dns": payload["dns"], "resolved_ips": ["93.184.215.14"]}


def test_precheck_blocks_private_and_mixed_answers(client, engine, admin):
    add(client, admin, "inside.com", "mixed.com")
    for name in ("inside.com", "mixed.com"):
        payload, job_id = run_precheck(engine, name)
        assert payload is None
        job = jobs(engine)[name]
        assert job.status == JobStatus.BLOCKED and job.error_code == "resolves_to_blocked_ip"
    with Session(engine) as s:
        assert s.scalar(select(ScanResult.blocked_reason).limit(1)) == "resolves_to_blocked_ip"


def test_precheck_nxdomain_marks_unreachable(client, engine, admin):
    add(client, admin, "missing.com")
    payload, _ = run_precheck(engine, "missing.com")
    assert payload is None
    assert jobs(engine)["missing.com"].status == JobStatus.FAILED
    with Session(engine) as s:
        d = s.scalar(select(Domain).where(Domain.normalized_domain == "missing.com"))
        assert d.classification_status == ClassificationStatus.UNREACHABLE and d.scan_count == 1


def test_precheck_timeout_is_retried(client, engine, admin):
    add(client, admin, "slow.com")
    payload, _ = run_precheck(engine, "slow.com")
    job = jobs(engine)["slow.com"]
    assert payload is None and job.status == JobStatus.QUEUED and job.error_code == "dns_timeout"


def success_result(job_id, domain="ok.com"):
    return CrawlResult(
        job_id=job_id, domain=domain, requested_url="https://ok.com/", final_url="https://ok.com/home",
        http_status=200, redirect_chain=[{"url": "https://ok.com/", "status": 301}], page_title="OK",
        resolved_ips=["93.184.215.14"], text_excerpt="hello world", word_count=2, link_count=3,
        screenshot=Artifact("scans/x/screenshot.png", "a" * 64, 1000, "image/png", 1280, 800),
        html=Artifact("scans/x/page.html.gz", "b" * 64, 500, "text/html"),
        text=Artifact("scans/x/text.txt.gz", "c" * 64, 11, "text/plain"), sandboxed=False)


def test_store_success(client, engine, admin):
    add(client, admin, "ok.com")
    _, job_id = run_precheck(engine, "ok.com")
    with Session(engine) as s, s.begin():
        assert store_result(s, success_result(job_id), job_id, S) == "succeeded"
    with Session(engine) as s:
        scan = s.scalar(select(ScanResult))
        assert scan.final_url == "https://ok.com/home" and str(scan.resolved_ips[0]) == "93.184.215.14"
        shot = s.scalar(select(Screenshot))
        assert shot.visibility == "private" and shot.retention_until > datetime.now(UTC) + timedelta(days=170)
        assert s.scalar(select(ExtractedContent.text_excerpt)) == "hello world"
        d = s.scalar(select(Domain).where(Domain.normalized_domain == "ok.com"))
        assert d.scan_count == 1 and d.last_scanned and d.next_scan > d.last_scanned
    with Session(engine) as s, s.begin():  # a duplicate delivery is ignored
        assert store_result(s, success_result(job_id), job_id, S) == "ignored"


def test_store_retry_then_fail(client, engine, admin):
    add(client, admin, "ok.com")
    for attempt in range(1, 4):
        _, job_id = run_precheck(engine, "ok.com")
        failure = CrawlResult(job_id=job_id, domain="ok.com", requested_url="https://ok.com/",
                              error_code="connection_failed")
        with Session(engine) as s, s.begin():
            outcome = store_result(s, failure, job_id, S)
        assert outcome == ("retry" if attempt < 3 else "failed")
    with Session(engine) as s:
        assert s.scalar(select(Domain.classification_status).where(Domain.normalized_domain == "ok.com")) \
            == ClassificationStatus.UNREACHABLE


def test_reap_stale_jobs(client, engine, admin):
    add(client, admin, "ok.com", "a.com")
    with Session(engine) as s, s.begin():
        claim_jobs(s, Settings(_env_file=None, max_in_flight_scans=10))
        s.execute(ScanJob.__table__.update().values(started_at=datetime.now(UTC) - timedelta(hours=1)))
        s.execute(ScanJob.__table__.update().where(ScanJob.domain_id == select(Domain.id).where(
            Domain.normalized_domain == "a.com").scalar_subquery()).values(attempts=3))
    with Session(engine) as s, s.begin():
        assert reap_stale(s, S) == {"requeued": 1, "failed": 1}
    state = jobs(engine)
    assert state["ok.com"].status == JobStatus.QUEUED and state["a.com"].status == JobStatus.FAILED


def test_cancelled_job_result_is_ignored(client, engine, admin):
    add(client, admin, "ok.com")
    _, job_id = run_precheck(engine, "ok.com")
    with Session(engine) as s, s.begin():
        s.execute(ScanJob.__table__.update().values(status="cancelled"))
    with Session(engine) as s, s.begin():
        assert store_result(s, success_result(job_id), job_id, S) == "ignored"


# ----------------------------------------------------------------------------- re-scan scheduling


def _days(engine, name="ok.com"):
    with Session(engine) as s:
        d = s.scalar(select(Domain).where(Domain.normalized_domain == name))
        return round((d.next_scan - d.last_scanned).total_seconds() / 86400)


def scanned(engine, client, admin, name="ok.com", **kw):
    add(client, admin, name) if not jobs(engine).get(name) else client.post(
        "/api/v1/admin/scans", headers=admin, json={"domain": name})
    _, job_id = run_precheck(engine, name)
    result = success_result(job_id, name)
    for k, v in kw.items():
        setattr(result, k, v)
    with Session(engine) as s, s.begin():
        store_result(s, result, job_id, S)


def test_rescan_intervals(client, engine, admin):
    scanned(engine, client, admin)
    assert _days(engine) == S.scan_interval_days
    scanned(engine, client, admin)  # same title and host
    assert _days(engine) == S.scan_interval_days
    scanned(engine, client, admin, page_title="Domain for sale")  # site changed
    assert _days(engine) == S.scan_interval_changed_days

    from classification.engine import record_threats
    with Session(engine) as s, s.begin():
        record_threats(s, s.scalar(select(Domain).where(Domain.normalized_domain == "ok.com")), ["phishing"])
    scanned(engine, client, admin, page_title="Domain for sale")
    assert _days(engine) == S.scan_interval_risk_days


def test_rescan_interval_for_failures_and_verified(client, engine, admin):
    add(client, admin, "missing.com")
    run_precheck(engine, "missing.com")  # NXDOMAIN -> failed
    assert _days(engine, "missing.com") == S.scan_interval_unreachable_days
    add(client, admin, "inside.com")
    run_precheck(engine, "inside.com")  # private address -> blocked
    assert _days(engine, "inside.com") == S.scan_interval_blocked_days
    scanned(engine, client, admin)
    with Session(engine) as s, s.begin():
        s.execute(Domain.__table__.update().where(Domain.normalized_domain == "ok.com").values(
            classification_status="verified"))
    scanned(engine, client, admin)
    assert _days(engine) == S.scan_interval_verified_days


def test_schedule_due(client, engine, admin):
    from backend.app.scan_tasks import schedule_due

    scanned(engine, client, admin, "ok.com")
    scanned(engine, client, admin, "a.com")
    later = datetime.now(UTC) + timedelta(days=S.scan_interval_days + 1)
    with Session(engine) as s, s.begin():
        assert schedule_due(s, S, now=datetime.now(UTC)) == 0  # nothing due yet
    with Session(engine) as s, s.begin():
        assert schedule_due(s, S, now=later) == 2
    with Session(engine) as s, s.begin():
        assert schedule_due(s, S, now=later) == 0  # already queued: no duplicates
    reasons = {str(j.reason) for j in jobs(engine).values()}
    assert reasons == {"scheduled"}
    with Session(engine) as s, s.begin():
        s.execute(Domain.__table__.update().where(Domain.normalized_domain == "a.com").values(is_active=False))
        s.execute(ScanJob.__table__.update().values(status="cancelled"))
    with Session(engine) as s, s.begin():
        assert schedule_due(s, S, now=later) == 1  # inactive domains are never re-scanned


def test_our_failures_are_retried_soon_and_can_be_requeued(client, engine, admin, capsys):
    """A broken sandbox after a reboot is not the site's fault: retry within hours, not next week."""
    from backend import cli

    add(client, admin, "ok.com", "missing.com")
    run_precheck(engine, "missing.com")  # NXDOMAIN: the site's problem
    _, job_id = run_precheck(engine, "ok.com")
    failure = CrawlResult(job_id=job_id, domain="ok.com", requested_url="https://ok.com/",
                          error_code="sandbox_unavailable", error_detail="AppArmor profile not loaded")
    with Session(engine) as s, s.begin():
        assert store_result(s, failure, job_id, S) == "failed"
    with Session(engine) as s:
        d = s.scalar(select(Domain).where(Domain.normalized_domain == "ok.com"))
        assert d.classification_status == ClassificationStatus.UNCLASSIFIED  # not "unreachable"
        assert (d.next_scan - d.last_scanned) == timedelta(hours=S.scan_retry_infra_hours)

    assert cli.main(["retry-infra-failures"]) == 0
    assert "dry run: would queue 1 domains for a new scan: ok.com" in capsys.readouterr().out
    assert cli.main(["retry-infra-failures", "--yes"]) == 0
    assert jobs(engine)["ok.com"].status == JobStatus.QUEUED
    assert jobs(engine)["missing.com"].status == JobStatus.FAILED  # left alone
