"""Octryx's findings: proven ones are listed without a person, everything else waits for one."""
from datetime import UTC, datetime

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from classification.engine import OCTRYX_SOURCE
from database.models import DataSource, Domain, DomainSourceRecord, ThreatIntelligence
from threats.lifecycle import ai_answer_needed
from threats.octryx import ingest
from threats.records import withdraw


@pytest.fixture(autouse=True)
def _isolated(clean):
    return


def item(domain, own=True, brand="Akbank"):
    return {"id": 7, "domain": domain, "brand": brand, "own_evidence": own}


def run(engine, items, max_auto=20):
    with Session(engine) as s, s.begin():
        return ingest(s, items, max_auto=max_auto)


def records(engine, name):
    with Session(engine) as s:
        return sorted((str(t.threat_type), slug, t.is_active) for t, slug in s.execute(
            select(ThreatIntelligence, DataSource.slug).join(DataSource, DataSource.id == ThreatIntelligence.source_id)
            .join(Domain, Domain.id == ThreatIntelligence.domain_id).where(Domain.normalized_domain == name)))


def sources(engine, name):
    with Session(engine) as s:
        return {(slug, cat) for slug, cat in s.execute(
            select(DataSource.slug, DomainSourceRecord.source_category)
            .join(DomainSourceRecord, DomainSourceRecord.source_id == DataSource.id)
            .join(Domain, Domain.id == DomainSourceRecord.domain_id).where(Domain.normalized_domain == name))}


def test_a_proven_finding_is_listed_without_a_person(engine, client, admin):
    result = run(engine, [item("akbank-giris.example.net")])
    assert [f["domain"] for f in result.flagged] == ["akbank-giris.example.net"]
    assert records(engine, "akbank-giris.example.net") == [("lookalike-domain", OCTRYX_SOURCE, True),
                                                           ("phishing", OCTRYX_SOURCE, True)]
    report = client.get("/api/v1/admin/threats/sgb-report", headers=admin, params={"status": "all"}).json()
    assert [(i["domain"], i["automatic"]) for i in report["items"]] == [("akbank-giris.example.net", True)]
    page = client.get("/api/v1/domain/akbank-giris.example.net").json()
    assert {r["type"] for r in page["risk"]["sources"]} == {"phishing", "lookalike-domain"}


def test_another_lists_verdict_goes_to_a_person(engine):
    result = run(engine, [item("only-listed.example.net", own=False)])
    assert (result.flagged, result.candidates) == ([], 1)
    assert records(engine, "only-listed.example.net") == []
    assert sources(engine, "only-listed.example.net") == {("ct-brand-watch", "look-alike: Akbank (Octryx)")}


def test_protected_names_go_to_a_person(engine):
    assert run(engine, [item("akbank.gov.tr")]).candidates == 1
    assert records(engine, "akbank.gov.tr") == []


def test_the_cap_limits_a_faulty_run(engine):
    result = run(engine, [item(f"a{i}.example.net") for i in range(3)], max_auto=2)
    assert (len(result.flagged), result.candidates) == (2, 1)


def test_a_second_run_changes_nothing(engine):
    run(engine, [item("akbank-giris.example.net")])
    result = run(engine, [item("akbank-giris.example.net")])
    assert (result.flagged, result.known) == ([], 1)


def test_a_withdrawn_flag_stays_withdrawn(engine):
    run(engine, [item("akbank-giris.example.net")])
    with Session(engine) as s, s.begin():
        d = s.scalar(select(Domain).where(Domain.normalized_domain == "akbank-giris.example.net"))
        assert withdraw(s, domain=d) == 2
    assert run(engine, [item("akbank-giris.example.net")]).flagged == []
    assert all(not active for _, _, active in records(engine, "akbank-giris.example.net"))


def test_the_model_is_not_asked_about_it(engine):
    run(engine, [item("akbank-giris.example.net")])
    with Session(engine) as s:
        d = s.scalar(select(Domain).where(Domain.normalized_domain == "akbank-giris.example.net"))
        assert not ai_answer_needed(s, d.id, every_days=30, now=datetime.now(UTC))


def test_bad_rows_are_skipped(engine):
    assert run(engine, [{"domain": "not a domain", "brand": "x"}, {"brand": "x"}]).invalid == 2
