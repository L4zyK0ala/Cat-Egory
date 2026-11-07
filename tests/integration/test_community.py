import pytest
from pydantic import SecretStr
from sqlalchemy import select
from sqlalchemy.orm import Session

from classification.engine import apply_categories
from classification.taxonomy import default_taxonomy
from common.config import get_settings
from database.models import AssignmentOrigin, AuditLog, ClassificationStatus, Domain, ScanJob

TAX = default_taxonomy()
REPORT = "/api/v1/community/report"


@pytest.fixture
def site(engine, client, admin):
    client.post("/api/v1/admin/domains", headers=admin, json={"domains": ["club.example.com"], "scan": "none"})
    with Session(engine) as s, s.begin():
        d = s.scalar(select(Domain).where(Domain.normalized_domain == "club.example.com"))
        apply_categories(s, d, primary="gambling", secondary=["sports", "entertainment"], confidence=0.92,
                         origin=AssignmentOrigin.AI, taxonomy=TAX, status=ClassificationStatus.CLASSIFIED)
    return "club.example.com"


@pytest.fixture
def fingerprints(monkeypatch):
    monkeypatch.setattr(get_settings(), "fingerprint_secret", SecretStr("test-secret"))


def state(engine, name="club.example.com"):
    with Session(engine) as s:
        d = s.scalar(select(Domain).where(Domain.normalized_domain == name))
        return d.primary_category_id, d.secondary_categories, str(d.classification_status)


def report(client, headers=None, **kw):
    body = {"domain": "club.example.com", "type": "false_positive", "category": "sports",
            "reason": "This is a casino, there is no sports content."} | kw
    return client.post(REPORT, json=body, headers=headers or {})


def test_anonymous_report_and_validation(engine, client, site, fingerprints):
    r = report(client, evidence_urls=["https://club.example.com/about"])
    assert r.status_code == 202 and r.json()["status"] == "pending"
    assert report(client).status_code == 409  # same reporter, open report
    assert report(client, domain="unknown.example.com").status_code == 404
    assert report(client, category="made-up").status_code == 422
    assert report(client, category=None).status_code == 422
    assert report(client, reason="short").status_code == 422
    assert report(client, evidence_urls=["file:///etc/passwd"]).json()["error"]["code"] == "invalid_url"
    assert report(client, type="category_correction", proposed_categories=[]).status_code == 422
    assert state(engine)[2] == "classified"  # one report changes nothing public
    with Session(engine) as s:
        log = s.scalar(select(AuditLog).where(AuditLog.action == "community.report"))
        assert log.actor_fingerprint and log.actor_user_id is None


def test_dispute_threshold_and_restore(engine, client, site, make_key, admin):
    keys = [make_key("contributor")[0] for _ in range(3)]
    for i, headers in enumerate(keys):
        assert report(client, headers).status_code == 202
        assert state(engine)[2] == ("disputed" if i == 2 else "classified")
    assert client.get("/api/v1/domain/club.example.com").json()["classification_status"] == "disputed"
    for item in client.get("/api/v1/admin/submissions", headers=admin).json():
        assert item["reporters_for_domain"] == 3
        client.post(f"/api/v1/admin/submissions/{item['id']}/moderate", headers=admin, json={"action": "reject"})
    assert state(engine)[2] == "classified"


def test_accept_false_positive_and_negative(engine, client, site, make_key, admin):
    c1, c2 = make_key("contributor")[0], make_key("contributor")[0]
    report(client, c1)  # sports is wrong
    report(client, c2, type="false_negative", category="online-communities")
    queue = client.get("/api/v1/admin/submissions", headers=admin).json()
    by_type = {q["type"]: q for q in queue}
    assert by_type["false_positive"]["category"] == "sports"
    client.post(f"/api/v1/admin/submissions/{by_type['false_positive']['id']}/moderate", headers=admin,
                json={"action": "accept", "note": "checked: casino only"})
    assert state(engine) == ("gambling", ["entertainment"], "verified")
    client.post(f"/api/v1/admin/submissions/{by_type['false_negative']['id']}/moderate", headers=admin,
                json={"action": "accept"})
    assert state(engine) == ("gambling", ["entertainment", "online-communities"], "verified")
    origins = {c["origin"] for c in client.get("/api/v1/domain/club.example.com").json()["categories"]}
    assert origins == {"community"}


def test_primary_removal_needs_replacement(engine, client, site, make_key, admin):
    report(client, make_key("contributor")[0], category="gambling")
    sub = client.get("/api/v1/admin/submissions", headers=admin).json()[0]
    r = client.post(f"/api/v1/admin/submissions/{sub['id']}/moderate", headers=admin, json={"action": "accept"})
    assert r.status_code == 422 and r.json()["error"]["code"] == "primary_required"
    r = client.post(f"/api/v1/admin/submissions/{sub['id']}/moderate", headers=admin,
                    json={"action": "accept", "primary": "sports", "secondary": []})
    assert r.status_code == 200 and state(engine) == ("sports", [], "verified")
    again = client.post(f"/api/v1/admin/submissions/{sub['id']}/moderate", headers=admin, json={"action": "reject"})
    assert again.status_code == 409


def test_correction(engine, client, site, make_key, admin):
    report(client, make_key("contributor")[0], type="category_correction", category=None,
           proposed_categories=["sports", "news"])
    sub = client.get("/api/v1/admin/submissions", headers=admin).json()[0]
    client.post(f"/api/v1/admin/submissions/{sub['id']}/moderate", headers=admin, json={"action": "accept"})
    assert state(engine) == ("sports", ["news"], "verified")


def test_suggest_new_domain(engine, client, admin, fingerprints):
    r = client.post("/api/v1/community/submit", json={"domain": "https://New-Site.example.org/x",
                                                      "proposed_categories": ["blogs"], "comment": "a cooking blog"})
    assert r.status_code == 202 and r.json()["domain"] == "new-site.example.org"
    dup = client.post("/api/v1/community/submit", json={"domain": "new-site.example.org"})
    assert dup.json()["error"]["code"] == "already_suggested"
    public = client.get("/api/v1/community/submissions").json()
    assert public[0]["domain"] == "new-site.example.org" and "comment" not in public[0]
    sub = client.get("/api/v1/admin/submissions", headers=admin).json()[0]
    assert sub["comment"] == "a cooking blog"
    client.post(f"/api/v1/admin/submissions/{sub['id']}/moderate", headers=admin, json={"action": "accept"})
    with Session(engine) as s:
        d = s.scalar(select(Domain).where(Domain.normalized_domain == "new-site.example.org"))
        assert d is not None and s.scalar(select(ScanJob.reason).where(ScanJob.domain_id == d.id)) == "community"
    assert client.get("/api/v1/domain/new-site.example.org").status_code == 200  # community source is public
    known = client.post("/api/v1/community/submit", json={"domain": "new-site.example.org"})
    assert known.json()["error"]["code"] == "already_known"


def test_votes_and_permissions(engine, client, site, make_key, admin):
    author, _ = make_key("contributor")
    voter, _ = make_key("contributor")
    sub = report(client, author).json()
    url = f"/api/v1/community/submissions/{sub['id']}/vote"
    assert client.post(url, json={"vote": 1}).status_code == 401
    assert client.post(url, headers=author, json={"vote": 1}).status_code == 409
    assert client.post(url, headers=voter, json={"vote": 1}).json()["score"] == 1
    assert client.post(url, headers=voter, json={"vote": -1}).json()["score"] == -1  # changed, not added
    assert client.post(url, headers=voter, json={"vote": 5}).status_code == 422
    assert client.get("/api/v1/admin/submissions", headers=voter).status_code == 403
    readonly, _ = make_key("admin", scopes=["scans:read"])
    assert report(client, readonly).status_code == 403
