from sqlalchemy import select
from sqlalchemy.orm import Session

from classification.engine import apply_categories, record_threats
from classification.taxonomy import default_taxonomy
from database.models import AssignmentOrigin, ClassificationStatus, Domain

TAX = default_taxonomy()


def classify(engine, name, primary, secondary=(), status=ClassificationStatus.CLASSIFIED, origin=AssignmentOrigin.AI):
    with Session(engine) as s, s.begin():
        d = s.scalar(select(Domain).where(Domain.normalized_domain == name))
        apply_categories(s, d, primary=primary, secondary=list(secondary), confidence=0.95, origin=origin,
                         taxonomy=TAX, status=status)


def add(client, admin, *names, source="manual"):
    client.post("/api/v1/admin/domains", headers=admin, json={"domains": list(names), "source": source, "scan": "none"})


def test_private_imports_are_not_disclosed(engine, client, admin):
    add(client, admin, "public.example.com")
    client.post("/api/v1/admin/domains/import", headers=admin, data={"scan": "none"},
                files={"file": ("internal.txt", b"partner.example.net\n", "text/plain")})
    classify(engine, "public.example.com", "news")
    classify(engine, "partner.example.net", "news")

    assert client.get("/api/v1/domain/public.example.com").status_code == 200
    assert client.get("/api/v1/domain/partner.example.net").status_code == 404  # anonymous: hidden
    assert client.get("/api/v1/domain/partner.example.net", headers=admin).status_code == 200  # admin: visible
    listed = [i["domain"] for i in client.get("/api/v1/categories/news/domains").json()["items"]]
    assert listed == ["public.example.com"]
    assert client.get("/api/v1/stats").json()["category_distribution"] == [{"category": "news", "domains": 1}]

    add(client, admin, "partner.example.net")  # later reported by a public source -> publishable
    assert client.get("/api/v1/domain/partner.example.net").status_code == 200


def test_url_lookup_and_fallback(engine, client, admin):
    add(client, admin, "example.org")
    classify(engine, "example.org", "reference")
    r = client.get("/api/v1/url", params={"url": "https://WWW.Example.org/a/b?x=1#frag"}).json()
    assert r["match"] == "registrable_domain" and r["result"]["domain"] == "example.org"
    assert r["normalized_url"] == "https://www.example.org/a/b?x=1"
    assert client.get("/api/v1/url", params={"url": "http://example.org/"}).json()["match"] == "host"
    assert client.get("/api/v1/url", params={"url": "ftp://example.org/"}).status_code == 422
    assert client.get("/api/v1/url", params={"url": "http://nothing.example.com/"}).status_code == 404


def test_history_and_pagination(engine, client, admin):
    add(client, admin, "h.example.com")
    for primary in ("blogs", "news", "media"):
        classify(engine, "h.example.com", primary)
    body = client.get("/api/v1/history/h.example.com", params={"limit": 2}).json()
    assert [h["category"] for h in body["history"]] == ["media", "news"]
    assert body["history"][0]["valid_to"] is None and body["history"][1]["valid_to"] is not None
    rest = client.get("/api/v1/history/h.example.com", params={"cursor": body["next_cursor"]}).json()
    assert [h["category"] for h in rest["history"]] == ["blogs"] and rest["next_cursor"] is None


def test_category_listing(engine, client, admin):
    names = [f"s{i}.example.com" for i in range(5)]
    add(client, admin, *names)
    for i, n in enumerate(names):
        classify(engine, n, "sports" if i % 2 == 0 else "news", secondary=["sports"] if i % 2 else [])
    allsports = []
    cursor = None
    while True:
        page = client.get("/api/v1/categories/sports/domains",
                          params={"limit": 2, **({"cursor": cursor} if cursor else {})}).json()
        allsports += page["items"]
        cursor = page["next_cursor"]
        if cursor is None:
            break
    assert len(allsports) == 5
    primary = client.get("/api/v1/categories/sports/domains", params={"primary_only": True}).json()["items"]
    assert len(primary) == 3
    assert client.get("/api/v1/categories/unknown/domains").status_code == 404


def test_risk_category_listing_and_stats(engine, client, admin):
    add(client, admin, "bad.example.com")
    with Session(engine) as s, s.begin():
        d = s.scalar(select(Domain).where(Domain.normalized_domain == "bad.example.com"))
        record_threats(s, d, ["phishing"])
    items = client.get("/api/v1/categories/phishing/domains").json()["items"]
    assert items == [{"domain": "bad.example.com"}]
    stats = client.get("/api/v1/stats").json()
    assert stats["threats"] == {"phishing": 1} and stats["total_domains"] == 1
    assert stats["pending_reviews"] == 0 and stats["ai_valid_answer_rate_30d"] is None
