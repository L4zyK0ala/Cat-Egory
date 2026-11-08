from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from classification.taxonomy import default_taxonomy
from database.models import AssignmentOrigin, DataSource, Domain, DomainCategory, ThreatIntelligence, ThreatType


def test_domain_lookup(client, engine, admin):
    client.post("/api/v1/admin/domains", headers=admin, json={"domains": ["bücher.de"]})
    for spelling in ["bücher.de", "XN--BCHER-KVA.DE", "B%C3%BCcher.de"]:
        r = client.get(f"/api/v1/domain/{spelling}")
        assert r.status_code == 200, spelling
    body = r.json()
    assert body["domain"] == "xn--bcher-kva.de" and body["display_domain"] == "bücher.de"
    assert body["classification_status"] == "unclassified" and body["categories"] == []
    assert body["risk"]["phishing"] is False and body["data_source"] is None
    assert body["taxonomy_version"] == default_taxonomy().version


def test_domain_lookup_errors(client, clean):
    r = client.get("/api/v1/domain/unknown-site.com")
    assert r.status_code == 404 and r.json()["error"]["code"] == "not_found"
    r = client.get("/api/v1/domain/127.0.0.1")
    assert r.status_code == 422 and r.json()["error"]["details"]["code"] == "ip_address"


def test_domain_categories_risk_and_etag(client, engine, admin):
    client.post("/api/v1/admin/domains", headers=admin, json={"domains": ["news.example.com"]})
    with Session(engine) as s, s.begin():
        d = s.scalar(select(Domain).where(Domain.normalized_domain == "news.example.com"))
        ai = s.scalar(select(DataSource).where(DataSource.slug == "cat-egory-ai"))
        s.add_all([
            DomainCategory(domain_id=d.id, category_id="news", is_primary=True, confidence=0.93,
                           origin=AssignmentOrigin.AI, source_id=ai.id, taxonomy_version="1.0.0"),
            DomainCategory(domain_id=d.id, category_id="sports", confidence=0.71, origin=AssignmentOrigin.AI,
                           source_id=ai.id, taxonomy_version="1.0.0"),
            DomainCategory(domain_id=d.id, category_id="blogs", is_primary=False, origin=AssignmentOrigin.AI,
                           taxonomy_version="1.0.0", valid_from=datetime(2025, 1, 1, tzinfo=UTC),
                           valid_to=datetime(2025, 6, 1, tzinfo=UTC)),  # history, not current
            ThreatIntelligence(domain_id=d.id, threat_type=ThreatType.PHISHING, source_id=ai.id),
        ])

    r = client.get("/api/v1/domain/news.example.com")
    body = r.json()
    assert [c["id"] for c in body["categories"]] == ["news", "sports"]
    assert body["categories"][0]["primary"] and body["categories"][0]["confidence"] == 0.93
    assert body["data_source"] == "cat-egory-ai"
    assert body["risk"]["phishing"] is True and body["risk"]["sources"][0]["source"] == "cat-egory-ai"

    etag = r.headers["ETag"]
    assert r.headers["Cache-Control"].startswith("public")
    assert client.get("/api/v1/domain/news.example.com", headers={"If-None-Match": etag}).status_code == 304

    with Session(engine) as s, s.begin():
        d = s.scalar(select(Domain).where(Domain.normalized_domain == "news.example.com"))
        d.last_scanned = datetime.now(UTC)
    assert client.get("/api/v1/domain/news.example.com", headers={"If-None-Match": etag}).status_code == 200
