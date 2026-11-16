"""A scheduled re-scan of a page that has not changed keeps the AI answer instead of asking the model again."""
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app import scan_tasks
from backend.app.classify_tasks import classify_scan, policy_from
from backend.app.scan_tasks import claim_jobs, precheck
from backend.app.services.domains import queue_scans
from classification.engine import record_threats
from classification.unchanged import kept_answer
from common.config import Settings
from crawler.result import content_fingerprint
from database.models import (
    AIPrediction,
    ClassificationStatus,
    Domain,
    JobStatus,
    ScanJob,
    ScanReason,
    ScanResult,
)
from tests.integration.test_classification import S, Scripted, make_scan
from tests.integration.test_scan_pipeline import ZONE, FakeResolver, add, success_result

NAME = "news.example.com"


# --------------------------------------------------------------------------- the fingerprint

def test_the_fingerprint_ignores_numbers_case_and_spacing():
    a = content_fingerprint("Latest News  14 November 2026\nWeather: 12°C")
    assert a == content_fingerprint("latest news 15 november 2026 weather: 9°c")
    assert a != content_fingerprint("Domain for sale 2026")
    assert content_fingerprint("") is None and content_fingerprint("   \n") is None and content_fingerprint(None) is None


# --------------------------------------------------------------------------- the rule

def answered(engine, fingerprint="f1", **answer):
    """A scan the model answered about (by default a confident 'news')."""
    scan_id = make_scan(engine)
    with Session(engine) as s, s.begin():
        s.get(ScanResult, scan_id).content_fingerprint = fingerprint
        classify_scan(s, scan_id, S, provider=Scripted(**answer))
    return scan_id


def rescan(engine, reason=ScanReason.SCHEDULED, fingerprint="f1", title="Daily News", final_url=None):
    scan_id = make_scan(engine, title=title)
    with Session(engine) as s, s.begin():
        scan = s.get(ScanResult, scan_id)
        s.get(ScanJob, scan.scan_job_id).reason = reason
        scan.content_fingerprint = fingerprint
        if final_url:
            scan.final_url = final_url
    return scan_id


def kept(engine, scan_id, every_days=180, now=None):
    with Session(engine) as s:
        scan = s.get(ScanResult, scan_id)
        answer = kept_answer(s, scan, s.get(ScanJob, scan.scan_job_id), s.get(Domain, scan.domain_id),
                             policy=policy_from(S), every_days=every_days, now=now or datetime.now(UTC))
        return answer.id if answer else None


def test_an_unchanged_page_keeps_the_confident_answer(engine, clean):
    answered(engine)
    with Session(engine) as s:
        answer = s.scalar(select(AIPrediction.id))
    assert kept(engine, rescan(engine)) == answer


@pytest.mark.parametrize("change", [
    {"fingerprint": "f2"},                                        # the text changed
    {"fingerprint": None},                                        # a crawler older than fingerprints
    {"title": "Domain for sale"},                                 # the title changed
    {"final_url": "https://parking.example.net/"},                # it now redirects elsewhere
    {"reason": ScanReason.MANUAL},                                # someone asked for this scan
    {"reason": ScanReason.RESCAN},
    {"reason": ScanReason.COMMUNITY},
])
def test_a_changed_page_or_a_requested_scan_asks_the_model(engine, clean, change):
    answered(engine)
    assert kept(engine, rescan(engine, **change)) is None


@pytest.mark.parametrize("answer", [
    {"confidence": 0.80},                    # second-opinion band
    {"confidence": 0.60},                    # waiting for re-analysis
    {"risk_categories": ["phishing"]},       # a risk claim always goes to a person
    {"requires_human_review": True},
])
def test_an_unsure_or_risky_answer_is_asked_again(engine, clean, answer):
    answered(engine)          # published with a confident answer ...
    answered(engine, **answer)  # ... then a newer, weaker one
    with Session(engine) as s:
        assert s.scalar(select(Domain.classification_status)) == ClassificationStatus.CLASSIFIED
    assert kept(engine, rescan(engine)) is None


def test_an_old_answer_is_refreshed_and_zero_turns_it_off(engine, clean):
    answered(engine)
    scan_id = rescan(engine)
    assert kept(engine, scan_id, now=datetime.now(UTC) + timedelta(days=179)) is not None
    assert kept(engine, scan_id, now=datetime.now(UTC) + timedelta(days=181)) is None
    assert kept(engine, scan_id, every_days=0) is None


def test_a_threat_or_an_unpublished_domain_asks_the_model(engine, clean):
    answered(engine)
    with Session(engine) as s, s.begin():
        record_threats(s, s.scalar(select(Domain)), ["phishing"])
    assert kept(engine, rescan(engine)) is None

    with Session(engine) as s, s.begin():
        s.scalar(select(Domain)).classification_status = ClassificationStatus.NEEDS_REVIEW
    assert kept(engine, rescan(engine)) is None


def test_a_human_decision_that_differs_from_the_model_asks_again(engine, clean):
    answered(engine)
    with Session(engine) as s, s.begin():
        d = s.scalar(select(Domain))
        d.primary_category_id, d.classification_status = "sports", ClassificationStatus.VERIFIED
    assert kept(engine, rescan(engine)) is None


# --------------------------------------------------------------------------- the persist task

def claimed(engine) -> str:
    with Session(engine) as s, s.begin():
        claim_jobs(s, Settings(_env_file=None, max_in_flight_scans=100))
    with Session(engine) as s:
        job_id = str(s.scalar(select(ScanJob.id).where(ScanJob.status == JobStatus.RUNNING)))
    with Session(engine) as s, s.begin():
        assert precheck(s, job_id, S, FakeResolver(ZONE)) is not None
    return job_id


def queue(engine, reason):
    with Session(engine) as s, s.begin():
        d = s.scalar(select(Domain).where(Domain.normalized_domain == "ok.com"))
        assert queue_scans(s, [d.id], reason=reason, priority=7, requested_by_id=None) == 1


def page(job_id, text="Latest headlines, 14 November 2026"):
    result = success_result(job_id)
    result.content_fingerprint = content_fingerprint(text)
    return result.to_dict()


def test_the_persist_task_skips_the_model_only_for_an_unchanged_page(client, engine, admin, monkeypatch):
    sent = []
    monkeypatch.setattr(scan_tasks.celery_app, "send_task", lambda name, **kw: sent.append((name, kw["args"])))
    add(client, admin, "ok.com")
    job_id = claimed(engine)
    assert scan_tasks.persist(page(job_id), job_id) == "succeeded"
    [(task, (first,))] = sent
    assert task == "category.classify.scan"
    with Session(engine) as s, s.begin():
        classify_scan(s, first, S, provider=Scripted())
        answer = s.scalar(select(AIPrediction.id))

    sent.clear()
    queue(engine, ScanReason.SCHEDULED)
    job_id = claimed(engine)
    assert scan_tasks.persist(page(job_id, "Latest  headlines, 16 november 2026"), job_id) == "succeeded"
    assert sent == []  # same page: the model is not asked
    with Session(engine) as s:
        scan = s.scalar(select(ScanResult).order_by(ScanResult.id.desc()).limit(1))
        assert scan.reused_prediction_id == answer and scan.content_fingerprint
        d = s.scalar(select(Domain))
        assert (d.primary_category_id, d.classification_status) == ("news", ClassificationStatus.CLASSIFIED)
        assert d.next_scan > datetime.now(UTC) + timedelta(days=S.scan_interval_days - 1)

    queue(engine, ScanReason.SCHEDULED)
    job_id = claimed(engine)
    scan_tasks.persist(page(job_id, "This domain is for sale"), job_id)
    assert [t for t, _ in sent] == ["category.classify.scan"]  # the page changed

    sent.clear()
    queue(engine, ScanReason.RESCAN)
    job_id = claimed(engine)
    scan_tasks.persist(page(job_id), job_id)
    assert [t for t, _ in sent] == ["category.classify.scan"]  # an admin asked for this scan


def test_metrics_count_kept_answers(client, engine, admin, monkeypatch):
    from backend.app.metrics import DatabaseCollector

    test_the_persist_task_skips_the_model_only_for_an_unchanged_page(client, engine, admin, monkeypatch)
    families = {f.name: f for f in DatabaseCollector()._query()}
    assert families["category_ai_answers_kept_24h"].samples[0].value == 1
