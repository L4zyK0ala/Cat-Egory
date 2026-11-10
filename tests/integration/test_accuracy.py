"""The public accuracy report: measured against people, sample apart from hard cases."""
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app import audit
from classification.accuracy import report, wilson
from classification.engine import record_threats
from database.models import AIPrediction, Domain, ReviewStatus, ReviewTask, ThreatIntelligence, ValidationStatus
from threats.records import withdraw


def test_wilson_interval():
    assert wilson(0, 0) is None
    low, high = wilson(9, 10)
    assert 0.55 < low < 0.6 and 0.98 < high <= 1.0


def review(s, domain, reason, predicted, decided):
    p = AIPrediction(domain_id=domain.id, provider="x", model="m", prompt_version="1", taxonomy_version="1",
                     validation_status=ValidationStatus.VALID, primary_category_id=predicted, confidence=0.95)
    s.add(p)
    s.flush()
    s.add(ReviewTask(domain_id=domain.id, ai_prediction_id=p.id, reason=reason, status=ReviewStatus.DONE,
                     decision_primary_id=decided, decided_at=datetime.now(UTC)))


def test_report_separates_the_sample_from_hard_cases(engine, client, admin):
    client.post("/api/v1/admin/domains", headers=admin, json={"domains": ["a.example.com"], "scan": "none"})
    with Session(engine) as s, s.begin():
        d = s.scalar(select(Domain).where(Domain.normalized_domain == "a.example.com"))
        for i in range(6):  # 5 of 6 kept: news; one moved to sports
            review(s, d, "verification_sample", "news", "news" if i else "sports")
        review(s, d, "verification_sample", "gaming", None)  # rejected
        review(s, d, "low_confidence", "news", "sports")
        review(s, d, "low_confidence", "news", "news")
    body = client.get("/api/v1/accuracy").json()
    published = body["published_ai"]
    assert (published["n"], published["correct"], published["rejected"]) == (7, 5, 1)
    assert [c["category"] for c in published["categories"]] == ["news"]  # gaming has too few to show
    assert (body["hard_cases"]["n"], body["hard_cases"]["correct"]) == (2, 1)


def test_threat_figures_count_withdrawals_and_their_time(engine, client, admin):
    client.post("/api/v1/admin/domains", headers=admin, json={"domains": ["bad.example.com", "gone.example.com"],
                                                             "scan": "none"})
    with Session(engine) as s, s.begin():
        bad = s.scalar(select(Domain).where(Domain.normalized_domain == "bad.example.com"))
        gone = s.scalar(select(Domain).where(Domain.normalized_domain == "gone.example.com"))
        record_threats(s, bad, ["phishing"])
        record_threats(s, gone, ["scam"])
        s.flush()
        s.execute(ThreatIntelligence.__table__.update().values(first_seen=datetime.now(UTC) - timedelta(hours=10)))
        withdraw(s, domain=bad)
        audit.record(s, action="threat.withdraw", target_type="domain", target_id="bad.example.com")
    with Session(engine) as s:
        threats = report(s)["threats"]
    assert (threats["total"], threats["active"], threats["withdrawn"]) == (2, 1, 1)
    assert 9.5 < threats["median_hours_to_withdraw"] < 10.5


def test_an_empty_database_reports_nothing_measured(engine, clean):
    with Session(engine) as s:
        body = report(s)
    assert body["published_ai"]["accuracy"] is None and body["threats"]["withdrawn_share"] is None
