"""The admin "İnfografik" tab: one month's publishable totals, never a looked-up name."""
from datetime import UTC, date, datetime

import pytest
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from backend.app.api.infographic import month_bounds
from classification.engine import DETECTOR_SOURCE, apply_categories, record_threats
from classification.taxonomy import default_taxonomy
from database.models import AssignmentOrigin, ClassificationStatus, DemandDaily, Domain, FeedDownloadDaily, ThreatIntelligence

OCTOBER = datetime(2026, 10, 15, 12, tzinfo=UTC)
EARLIER = datetime(2026, 8, 1, tzinfo=UTC)


def test_month_bounds():
    assert month_bounds(None, date(2026, 11, 19)) == (date(2026, 10, 1), date(2026, 11, 1))
    assert month_bounds("2026-12", date(2026, 12, 2)) == (date(2026, 12, 1), date(2027, 1, 1))
    assert month_bounds("2025-12", date(2026, 11, 19)) == (date(2025, 12, 1), date(2026, 1, 1))


@pytest.mark.parametrize("month", ["2026-13", "26-10", "2026-1", "2099-01"])
def test_bad_or_future_months_are_refused(client, admin, month):
    assert client.get("/api/v1/admin/infographic", headers=admin, params={"month": month}).status_code == 422


def test_a_month_of_numbers(engine, clean, client, admin):
    sites = {"news.example.com": ("news", OCTOBER, ClassificationStatus.CLASSIFIED),
             "haber.com.tr": ("news", EARLIER, ClassificationStatus.VERIFIED),
             "oyun.com.tr": ("gaming", OCTOBER, ClassificationStatus.CLASSIFIED),
             "ziraatbank-giris.com": ("banking", OCTOBER, ClassificationStatus.CLASSIFIED),
             "later.com.tr": ("news", datetime(2026, 11, 5, tzinfo=UTC), ClassificationStatus.CLASSIFIED),
             "waiting.com.tr": ("news", OCTOBER, ClassificationStatus.NEEDS_REVIEW)}
    client.post("/api/v1/admin/domains", headers=admin, json={"domains": list(sites), "scan": "none"})
    with Session(engine) as s, s.begin():
        for name, (primary, seen, status) in sites.items():
            d = s.scalar(select(Domain).where(Domain.normalized_domain == name))
            apply_categories(s, d, primary=primary, secondary=[], confidence=0.9, origin=AssignmentOrigin.AI,
                             taxonomy=default_taxonomy(), status=status)
            d.first_seen = seen
        bank = s.scalar(select(Domain).where(Domain.normalized_domain == "ziraatbank-giris.com"))
        record_threats(s, bank, ["phishing", "lookalike-domain"], source_slug=DETECTOR_SOURCE)
        s.flush()
        s.execute(update(ThreatIntelligence).values(first_seen=OCTOBER))
        s.add_all([DemandDaily(day=date(2026, 10, 3), domain="news.example.com", known=True, lookups=7, callers=4),
                   DemandDaily(day=date(2026, 10, 4), domain="someones-private-name.net", known=False, lookups=2,
                               callers=1),
                   DemandDaily(day=date(2026, 9, 30), domain="oyun.com.tr", known=True, lookups=50, callers=50),
                   FeedDownloadDaily(day=date(2026, 10, 9), feed="adult-content.txt", downloads=12, callers=3)])

    r = client.get("/api/v1/admin/infographic", headers=admin, params={"month": "2026-10"})
    assert r.status_code == 200, r.text
    j = r.json()
    assert j["month"] == "2026-10" and j["complete"] is True
    assert j["start"].startswith("2026-09-30T21:00")  # Türkiye midnight
    assert j["totals"] == {"published": 4, "new_published": 3, "verified": 1, "tr_published": 2, "categories_used": 3}
    assert j["categories"][0] == {"key": "news", "count": 2}
    assert {c["key"] for c in j["tr_categories"]} == {"news", "gaming"}
    t = j["threats"]
    assert (t["new_domains"], t["own_domains"]) == (1, 1)
    assert {c["key"] for c in t["by_type"]} == {"phishing", "lookalike-domain"}
    assert t["brands"] == [{"key": "Ziraat Bankası", "count": 1}] and t["suffixes"] == [{"key": ".com", "count": 1}]
    d = j["demand"]
    assert (d["lookups"], d["domains"], d["feed_downloads"]) == (9, 2, 12)
    assert d["by_category"] == [{"key": "news", "count": 4}]
    assert d["daily"] == [{"key": "2026-10-03", "count": 7}, {"key": "2026-10-04", "count": 2}]
    assert "someones-private-name" not in r.text and "news.example.com" not in r.text  # totals only


def test_needs_a_reader_key(client, make_key):
    contributor, _ = make_key("contributor")
    assert client.get("/api/v1/admin/infographic", headers=contributor).status_code == 403
