from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from backend.app.scan_tasks import store_result
from crawler.result import CrawlResult
from database.models import Domain, DomainCategory
from tests.integration.test_classification import Scripted, make_scan, run
from tests.integration.test_scan_pipeline import ZONE, S, add, jobs, run_precheck

GOV_ZONE = ZONE | {"ptt.gov.tr": {"A": ["212.174.186.2"]}, "kapali.gov.tr": {"A": ["10.0.0.5"]},
                   "classified.gov.tr": {"A": ["212.174.186.3"]}}


def _fail(engine, name, code="robots_unavailable", attempts=3):
    """Run a domain's scan to a final failure (robots_unavailable is retried twice first)."""
    for _ in range(attempts):
        payload, job_id = run_precheck(engine, name, GOV_ZONE)
        if payload is None:
            return
        result = CrawlResult(job_id=job_id, domain=name, requested_url=f"https://{name}/", error_code=code,
                             resolved_ips=payload["resolved_ips"])
        with Session(engine) as s, s.begin():
            if store_result(s, result, job_id, S) != "retry":
                return


def _current(engine, name):
    with Session(engine) as s:
        return sorted((dc.category_id, str(dc.origin)) for dc in s.scalars(
            select(DomainCategory).join(Domain, Domain.id == DomainCategory.domain_id).where(
                DomainCategory.valid_to.is_(None), Domain.normalized_domain == name)))


def test_unreadable_gov_site_is_classified_by_rule(client, engine, admin):
    add(client, admin, "ptt.gov.tr")
    _fail(engine, "ptt.gov.tr")
    assert _current(engine, "ptt.gov.tr") == [("government", "rule")]
    body = client.get("/api/v1/domain/ptt.gov.tr").json()
    assert body["classification_status"] == "classified" and body["data_source"] == "cat-egory-registry-rules"
    assert body["categories"][0]["origin"] == "rule" and body["categories"][0]["confidence"] is None

    # a later successful scan + AI answer replaces the rule (the AI can say more, e.g. a sub-purpose)
    run(engine, make_scan(engine, "ptt.gov.tr"), Scripted(primary_category="government",
                                                          secondary_categories=["e-commerce"]))
    assert _current(engine, "ptt.gov.tr") == [("e-commerce", "ai"), ("government", "ai")]


def test_rule_needs_a_live_domain_and_never_overrides(client, engine, admin):
    add(client, admin, "missing.gov.tr", "kapali.gov.tr", "classified.gov.tr", "ok.com")
    _fail(engine, "missing.gov.tr")      # NXDOMAIN: maybe not registered at all
    _fail(engine, "kapali.gov.tr")       # private address: refused, suspicious
    _fail(engine, "ok.com")              # no rule for .com
    assert _current(engine, "missing.gov.tr") == _current(engine, "kapali.gov.tr") == _current(engine, "ok.com") == []

    run(engine, make_scan(engine, "classified.gov.tr"), Scripted(primary_category="education"))
    _fail(engine, "classified.gov.tr")   # an existing classification wins over the rule
    assert _current(engine, "classified.gov.tr") == [("education", "ai"), ("sports", "ai")]


def test_cli_backfill(client, engine, admin, monkeypatch):
    from backend import cli

    add(client, admin, "ptt.gov.tr")
    _fail(engine, "ptt.gov.tr")
    with Session(engine) as s, s.begin():  # as if scanned before the rules existed
        s.execute(DomainCategory.__table__.delete())
        s.execute(Domain.__table__.update().values(classification_status="unreachable", primary_category_id=None))
    with sessionmaker(bind=engine)() as s, s.begin():
        assert cli.apply_registry_rules(s, dry_run=True) == ["ptt.gov.tr"]
    with sessionmaker(bind=engine)() as s, s.begin():
        assert cli.apply_registry_rules(s) == ["ptt.gov.tr"]
    assert _current(engine, "ptt.gov.tr") == [("government", "rule")]
    assert str(jobs(engine)["ptt.gov.tr"].status) == "failed"
