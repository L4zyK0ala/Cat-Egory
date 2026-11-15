"""Threat fast lane: suspected threats get extra crawl slots and their own queues and worker."""
from pathlib import Path

import yaml
from sqlalchemy import func, select, text, update
from sqlalchemy.orm import Session

from backend.app import scan_tasks
from backend.app.scan_tasks import claim_jobs
from common.config import Settings
from database.models import Domain, JobStatus, ScanJob
from integrations import ct_watch
from tests.integration.test_scan_pipeline import S, add, jobs, success_result
from threats import octryx

ROOT = Path(__file__).parents[2]


def _urgent(engine, name, priority=1):
    with Session(engine) as s, s.begin():
        s.execute(update(ScanJob).where(ScanJob.domain_id == select(Domain.id).where(
            Domain.normalized_domain == name).scalar_subquery()).values(priority=priority))


def test_threat_sources_use_the_fast_lane():
    assert scan_tasks.is_fast(ct_watch.PRIORITY, Settings(_env_file=None))
    assert scan_tasks.is_fast(octryx.PRIORITY, Settings(_env_file=None))
    assert not scan_tasks.is_fast(5, Settings(_env_file=None))  # manual scans by default


def test_a_suspected_threat_does_not_wait_for_a_free_slot(client, engine, admin):
    add(client, admin, "a.com", "b.com", "c.com", "evil.com", "evil2.com")
    with Session(engine) as s, s.begin():
        assert [fast for _, _, fast in claim_jobs(s, S)] == [False, False]  # the limit (2) is full
    _urgent(engine, "evil.com")
    _urgent(engine, "evil2.com")
    with Session(engine) as s, s.begin():
        claimed = claim_jobs(s, S)
    assert [fast for _, _, fast in claimed] == [True]  # one extra slot, and only for the fast lane
    state = jobs(engine)
    assert state["evil.com"].status == JobStatus.RUNNING and state["c.com"].status == JobStatus.QUEUED
    with Session(engine) as s, s.begin():
        assert claim_jobs(s, S) == []


def test_routine_jobs_never_take_the_extra_slot(client, engine, admin):
    add(client, admin, "a.com", "b.com", "c.com")
    with Session(engine) as s, s.begin():
        assert len(claim_jobs(s, S)) == 2
        assert claim_jobs(s, S) == []
    assert jobs(engine)["c.com"].status == JobStatus.QUEUED


def test_fast_lane_queues(client, engine, admin, monkeypatch):
    sent = []
    monkeypatch.setattr(scan_tasks.celery_app, "send_task", lambda name, **kw: sent.append((name, kw.get("queue"))))
    monkeypatch.setattr(scan_tasks.prepare, "apply_async",
                        lambda **kw: sent.append(("prepare", kw.get("queue"), kw.get("kwargs"))))
    add(client, admin, "ok.com", "evil.com")
    _urgent(engine, "evil.com")
    scan_tasks.dispatch()
    assert sent == [("prepare", "fast", {"fast": True}), ("prepare", None, None)]

    sent.clear()
    job_id = str(jobs(engine)["evil.com"].id)
    monkeypatch.setattr(scan_tasks, "precheck", lambda *a, **kw: {"url": "https://evil.com/"})
    scan_tasks.prepare(job_id, fast=True)
    assert sent == [("category.crawl.run", "crawl-fast")]

    sent.clear()
    scan_tasks.persist(success_result(job_id).to_dict(), job_id, fast=True)
    assert sent == [("category.classify.scan", "fast")]


def test_deployment_runs_the_fast_lane():
    services = yaml.safe_load((ROOT / "deployment/docker-compose.yml").read_text())["services"]
    fast = services["worker-fast"]["command"]
    assert fast[fast.index("-Q") + 1] == "fast"
    assert "CATEGORY_EVREN_API_KEY" in services["worker-fast"]["environment"]  # it classifies too
    assert "fast" not in services["worker"]["command"][services["worker"]["command"].index("-Q") + 1].split(",")
    crawler = (ROOT / "deployment/docker/Dockerfile.crawler").read_text()
    assert '"-Q", "crawl-fast,crawl"' in crawler  # fast first


def test_a_stuck_fast_lane_shows_in_metrics(client, engine, admin):
    from backend.app.metrics import DatabaseCollector

    def oldest():
        families = {f.name: f for f in DatabaseCollector().collect()}
        return families["category_fast_lane_oldest_seconds"].samples[0].value

    add(client, admin, "ok.com", "evil.com")
    assert oldest() == 0  # routine scans do not count
    _urgent(engine, "evil.com")
    with Session(engine) as s, s.begin():
        s.execute(update(ScanJob).where(ScanJob.priority == 1).values(queued_at=func.now() - text("interval '30 minutes'")))
    assert 1700 < oldest() < 1900
    rules = (ROOT / "deployment/prometheus/alerts.yml").read_text()
    assert "category_fast_lane_oldest_seconds > 1200" in rules



def test_scanning_has_a_worker_of_its_own():
    """2026-11-15: 448 queued AI calls held both slots of the main worker; the 10-second dispatch expired unrun."""
    services = yaml.safe_load((ROOT / "deployment/docker-compose.yml").read_text())["services"]
    queues = {name: svc["command"][svc["command"].index("-Q") + 1].split(",")
              for name, svc in services.items() if "celery" in str(svc.get("command", "")) and "-Q" in svc["command"]}
    assert queues["worker-scan"] == ["scan"]
    assert "scan" not in queues["worker"] and "classify" in queues["worker"]
    assert "CATEGORY_EVREN_API_KEY" not in str(services["worker-scan"].get("environment", {}))
    assert "worker-scan" in (ROOT / "deployment/scripts/restore.sh").read_text()
