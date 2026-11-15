"""Sites whose robots.txt shuts out every bot: a model's suggestion from the name only, always for a person."""
import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from ai_providers.prompting import build_prompt
from ai_providers.schemas import ClassificationInput
from backend.app import scan_tasks
from backend.app.classify_tasks import suggest_by_name
from backend.cli import robots_blocked_domains, suggest_robots_blocked
from classification.engine import ROBOTS_BLOCKED
from classification.taxonomy import default_taxonomy
from crawler.result import CrawlResult
from database.models import AIPrediction, ClassificationStatus, Domain, JobStatus, ScanJob, ScanReason
from tests.integration.test_classification import S, Scripted, current, reviews
from tests.integration.test_scan_pipeline import add, run_precheck


def blocked_domain(engine, client, admin, name="social.example.com"):
    add(client, admin, name)
    with Session(engine) as s, s.begin():
        d = s.scalar(select(Domain).where(Domain.normalized_domain == name))
        s.execute(ScanJob.__table__.update().where(ScanJob.domain_id == d.id)
                  .values(status=JobStatus.BLOCKED, error_code="robots_disallowed"))
        return d.id


def ask(engine, domain_id, provider):
    with Session(engine) as s, s.begin():
        return suggest_by_name(s, domain_id, S, provider=provider)


def test_the_prompt_says_no_page_was_read():
    data = ClassificationInput(url="https://social.example.com/", domain="social.example.com",
                               not_fetched="robots_disallowed")
    _, user, signals = build_prompt(data, default_taxonomy())
    assert "robots.txt does not allow automated visitors" in user and "general knowledge" in user
    assert user.rstrip().endswith("Domain under analysis: social.example.com") and signals == []


def test_a_known_name_goes_to_a_person_and_is_never_applied(engine, client, admin):
    domain_id = blocked_domain(engine, client, admin)
    provider = Scripted(primary_category="social-networking", secondary_categories=[], confidence=0.97,
                        risk_categories=["phishing"], evidence=[{"type": "metadata", "observation": "known site"}])
    assert ask(engine, domain_id, provider) == "review"
    assert reviews(engine) == [(ROBOTS_BLOCKED, "open")]
    assert current(engine, "social.example.com") == []  # not published without a person
    with Session(engine) as s:
        assert s.get(Domain, domain_id).classification_status == ClassificationStatus.NEEDS_REVIEW
        pred = s.scalar(select(AIPrediction))
        assert pred.scan_result_id is None and pred.risk_flags == []  # no page, no threat claim
    assert ask(engine, domain_id, provider) == "skipped" and provider.calls == 1  # asked once

    task = client.get("/api/v1/admin/reviews", headers=admin).json()["items"][0]
    assert task["reason"] == ROBOTS_BLOCKED
    r = client.post(f"/api/v1/admin/reviews/{task['id']}/decision", headers=admin, json={"action": "approve"})
    assert r.status_code == 200
    assert current(engine, "social.example.com") == [("social-networking", True, "human")]


def test_an_unknown_name_bothers_nobody(engine, client, admin):
    domain_id = blocked_domain(engine, client, admin, "obscure.example.com")
    assert ask(engine, domain_id, Scripted(confidence=0.3)) == "unknown"
    assert reviews(engine) == []


def test_a_blocked_scan_asks_by_itself(engine, client, admin, monkeypatch):
    sent = []
    monkeypatch.setattr(scan_tasks.celery_app, "send_task", lambda name, **kw: sent.append((name, kw.get("queue"))))
    add(client, admin, "ok.com")
    _, job_id = run_precheck(engine, "ok.com")
    blocked = CrawlResult(job_id=job_id, domain="ok.com", requested_url="https://ok.com/", robots_allowed=False,
                          blocked_reason="robots_disallowed")
    assert scan_tasks.persist(blocked.to_dict(), job_id) == "blocked"
    assert sent == [("category.classify.name_only", "classify")]


def test_backfill_command(engine, client, admin, monkeypatch):
    from backend.app.worker import celery_app

    sent = []
    monkeypatch.setattr(celery_app, "send_task", lambda name, **kw: sent.append((name, kw["args"])))
    first = blocked_domain(engine, client, admin, "a.example.com")
    blocked_domain(engine, client, admin, "b.example.com")
    with Session(engine) as s, s.begin():  # b was scanned again since, and that worked
        b = s.scalar(select(Domain).where(Domain.normalized_domain == "b.example.com"))
        s.add(ScanJob(id=uuid.uuid4(), domain_id=b.id, reason=ScanReason.RESCAN, status=JobStatus.SUCCEEDED))
    with Session(engine) as s, s.begin():
        assert [n for _, n in robots_blocked_domains(s)] == ["a.example.com"]
        assert suggest_robots_blocked(s, dry_run=True) == ["a.example.com"] and sent == []
        assert suggest_robots_blocked(s) == ["a.example.com"]
    assert sent == [("category.classify.name_only", [first])]
