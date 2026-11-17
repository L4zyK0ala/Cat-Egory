"""Threat lists as candidates (threats/feeds.py) and our own page-proof detector (threats/detector.py)."""
import gzip
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from classification.engine import REVIEW_SOURCE, record_threats
from common.config import Settings
from crawler.storage import MemoryStore
from database.models import (
    DataSource,
    DetectorVerdict,
    Domain,
    DomainSourceRecord,
    ReviewStatus,
    ReviewTask,
    ScanJob,
    ThreatFeedEntry,
    ThreatIntelligence,
)
from integrations import engine as sources
from tests.integration.test_classification import Scripted, make_scan, reviews
from tests.integration.test_integrations import approved
from threats import detector, feeds

LIST = {"name": "Test phish list", "kind": "threat_feed", "format": "domains", "candidates_only": True,
        "feed_url": "https://lists.example.com/phish.txt", "threat_type": "phishing"}
URLS = LIST | {"name": "Test link list", "format": "url-list"}
S = Settings(_env_file=None, threat_feed_candidates_per_hour=8)  # 2 a quarter hour
PHISH = ("<html><body><h1>Garanti BBVA İnternet Şubesi</h1><form><input name='tckn' maxlength='11'>"
         "<input type='password' name='sifre'><input name='sms_kodu'></form></body></html>")


def entries(engine):
    with Session(engine) as s:
        return {e.host: (e.relevance, e.brand, e.is_listed, e.url) for e in s.scalars(select(ThreatFeedEntry))}


# ----------------------------------------------------------------------------- phase 1: candidates


def test_a_candidate_list_fills_its_own_table_and_never_the_catalogue(engine, clean):
    data = b"# comment\ngarantibbva-giris.com\nhaber-ornek.com.tr\nrandom-shop.xyz\n203.0.113.9\n"
    with Session(engine) as s, s.begin():
        approved(s, "test-phish", LIST)
        result = sources.sync(s, "test-phish", data=data, entry=LIST)
    assert (result.valid, result.invalid) == (3, 1)  # the IP address is not a domain
    assert entries(engine) == {"garantibbva-giris.com": (2, "Garanti BBVA", True, None),
                               "haber-ornek.com.tr": (1, None, True, None), "random-shop.xyz": (0, None, True, None)}
    with Session(engine) as s:
        assert s.scalar(select(func.count()).select_from(Domain)) == 0  # nothing in the catalogue
        assert s.scalar(select(func.count()).select_from(ThreatIntelligence)) == 0  # and no verdict of theirs
    with Session(engine) as s, s.begin():  # the list dropped two names
        sources.sync(s, "test-phish", data=b"garantibbva-giris.com\n", entry=LIST)
    assert [h for h, e in entries(engine).items() if e[2]] == ["garantibbva-giris.com"]


def test_a_link_list_keeps_one_link_per_host_for_the_reviewer(engine, clean):
    data = b"https://ptt-odeme.net/kargo?id=1\nhttps://ptt-odeme.net/other\nhttps://x.pages.dev/\n"
    with Session(engine) as s, s.begin():
        approved(s, "test-links", URLS)
        sources.sync(s, "test-links", data=data, entry=URLS)
    got = entries(engine)
    assert got["ptt-odeme.net"] == (2, "PTT", True, "https://ptt-odeme.net/kargo?id=1")
    assert got["x.pages.dev"][3] == "https://x.pages.dev/"


def test_urlhaus_layout_and_the_key_in_the_path(monkeypatch):
    csv = (b'################\n# id,dateadded,url,url_status,last_online,threat,tags,urlhaus_link,reporter\n'
           b'"1","2026-11-17 10:00:00","http://bad.example.net/x.exe","online","","malware_download","","",""\n')
    assert sources.parse(csv, "csv", {"column_index": 2}) == [(3, "http://bad.example.net/x.exe")]
    seen = []
    monkeypatch.setattr(sources, "download", lambda url, **_: seen.append(url) or b"ok")
    from common import config

    monkeypatch.setattr(config, "get_settings", lambda: Settings(_env_file=None))
    with pytest.raises(sources.SourceError) as err:
        sources.download_latest("https://h.example/v2/files/exports/{abusech_key}/recent.csv")
    assert err.value.code == "no_key"
    monkeypatch.setattr(config, "get_settings", lambda: Settings(_env_file=None, abusech_auth_key="k-123"))
    sources.download_latest("https://h.example/v2/files/exports/{abusech_key}/recent.csv")
    assert seen == ["https://h.example/v2/files/exports/k-123/recent.csv"]


def test_picking_takes_turkish_brands_first_and_never_official_hosts(engine, clean):
    data = b"random-shop.xyz\nhaber-ornek.com.tr\ngarantibbva-giris.com\ngiris.akbank.com\nakbank-sube.net\n"
    with Session(engine) as s, s.begin():
        approved(s, "test-phish", LIST)
        sources.sync(s, "test-phish", data=data, entry=LIST)
    with Session(engine) as s, s.begin():
        first = feeds.pick(s, S)
    assert sorted(first) == ["akbank-sube.net", "garantibbva-giris.com"]  # brand-like first; giris.akbank.com is official
    with Session(engine) as s, s.begin():
        second = feeds.pick(s, S)
    assert second == ["haber-ornek.com.tr"]  # then .tr; random-shop.xyz is below the default relevance
    with Session(engine) as s, s.begin():
        assert feeds.pick(s, S) == []
    with Session(engine) as s:
        jobs = {s.get(Domain, j.domain_id).normalized_domain: j.priority for j in s.scalars(select(ScanJob))}
        provenance = s.scalar(select(func.count()).select_from(DomainSourceRecord)
                              .join(DataSource, DataSource.id == DomainSourceRecord.source_id)
                              .where(DataSource.slug == "test-phish"))
    assert jobs == dict.fromkeys(["akbank-sube.net", "garantibbva-giris.com", "haber-ornek.com.tr"], feeds.PRIORITY)
    assert provenance == 3
    with Session(engine) as s, s.begin():
        assert feeds.pick(s, Settings(_env_file=None, threat_feed_candidates_per_hour=0)) == []


def test_a_picked_host_always_goes_to_the_threat_queue(engine, clean):
    with Session(engine) as s, s.begin():
        approved(s, "test-phish", LIST)
        sources.sync(s, "test-phish", data=b"haber-ornek.com.tr\n", entry=LIST)
        feeds.pick(s, S)
    scan = make_scan(engine, "haber-ornek.com.tr", title="Haberler")
    out = run(engine, scan, Scripted(primary_category="news", confidence=0.99))
    assert (out.action, out.reason) == ("review", "feed_listed:test-phish")
    assert ("feed_listed:test-phish", "open") in reviews(engine)


# ----------------------------------------------------------------------------- phase 2: the detector


def page(html=PHISH, host="garantibbva-giris.com", title="Garanti BBVA Giriş", cert_days=None):
    now = datetime(2026, 11, 17, tzinfo=UTC)
    return detector.Page(host=host, title=title, description=None, text="Hoş geldiniz", html=html,
                         cert_valid_from=(now - timedelta(days=cert_days)).timestamp() if cert_days is not None else None,
                         scanned_at=now)


def test_the_rule_needs_a_secret_a_brand_and_a_suspect_host():
    yes, signals = detector.judge(page(), suspicious_name="brand_lookalike", protected=None)
    assert yes and signals["secrets"] == ["password", "sms_code", "tr_id"] and signals["brand"] == "Garanti BBVA"
    assert detector.judge(page(cert_days=3), suspicious_name=None, protected=None)[0]       # a new certificate will do
    assert not detector.judge(page(cert_days=90), suspicious_name=None, protected=None)[0]  # an old one alone will not
    assert not detector.judge(page(html="<p>Garanti BBVA kampanya</p>"), suspicious_name="x", protected=None)[0]
    assert not detector.judge(page(title="Kedi maması", html="<input type=password>"), suspicious_name="x",
                              protected=None)[0]  # no brand claimed
    assert not detector.judge(page(host="sube.garantibbva.com.tr"), suspicious_name="x", protected=None)[0]  # official
    assert not detector.judge(page(), suspicious_name="x", protected="example.com")[0]  # a popular site's page


def run(engine, scan_id, provider, settings=S):
    from backend.app.classify_tasks import classify_scan

    store = MemoryStore()
    store.put("h.gz", gzip.compress(PHISH.encode()), "application/gzip")
    with Session(engine) as s, s.begin():
        return classify_scan(s, scan_id, settings, provider=provider, store=store)


def verdicts(engine):
    with Session(engine) as s:
        return [(s.get(Domain, v.domain_id).normalized_domain, v.positive, v.action) for v in
                s.scalars(select(DetectorVerdict).order_by(DetectorVerdict.id))]


def test_shadow_verdicts_are_measured_against_people(engine, clean):
    import fakeredis
    from sqlalchemy.orm import sessionmaker

    from integrations.brand_watch import default_watchlist
    from integrations.ct_watch import handle
    from tests.integration.test_ct_watch import S as CT

    handle(["garantibbva-giris.com", "akbank-sube-giris.net"], default_watchlist(), fakeredis.FakeRedis(), CT,
           sessionmaker(bind=engine))
    for name in ("garantibbva-giris.com", "akbank-sube-giris.net"):
        run(engine, make_scan(engine, name, title="Garanti BBVA Giriş"), Scripted(primary_category="banking"))
    assert verdicts(engine) == [("garantibbva-giris.com", True, None), ("akbank-sube-giris.net", True, None)]
    with Session(engine) as s:
        assert s.scalar(select(func.count()).select_from(ThreatIntelligence)) == 0  # shadow: flags nothing
        assert detector.measure(s)["pending"] == 2
    with Session(engine) as s, s.begin():  # a person flags one and closes the other as harmless
        for task in s.scalars(select(ReviewTask)):
            name = s.get(Domain, task.domain_id).normalized_domain
            if name == "garantibbva-giris.com":
                record_threats(s, s.get(Domain, task.domain_id), ["phishing"], source_slug=REVIEW_SOURCE)
            task.status, task.decided_at = ReviewStatus.DONE, datetime.now(UTC) + timedelta(minutes=1)
    with Session(engine) as s:
        m = detector.measure(s)
    assert (m["tp"], m["fp"], m["pending"], m["precision"]) == (1, 1, 0, 0.5)


def test_it_flags_by_itself_only_when_allowed_and_proven(engine, clean):
    import fakeredis
    from sqlalchemy.orm import sessionmaker

    from integrations.brand_watch import default_watchlist
    from integrations.ct_watch import handle
    from tests.integration.test_ct_watch import S as CT

    auto = Settings(_env_file=None, threat_detector_auto=True, threat_detector_min_decisions=20,
                    threat_detector_sample_every=1000)
    handle(["garantibbva-giris.com"], default_watchlist(), fakeredis.FakeRedis(), CT, sessionmaker(bind=engine))
    run(engine, make_scan(engine, "garantibbva-giris.com", title="Garanti BBVA Giriş"),
        Scripted(primary_category="banking"), settings=auto)
    assert verdicts(engine)[-1] == ("garantibbva-giris.com", True, None)  # allowed, but not proven: nothing flagged

    with Session(engine) as s, s.begin():  # 20 earlier positives a person confirmed
        manual = s.scalar(select(DataSource).where(DataSource.slug == "manual"))
        for i in range(20):
            d = Domain(domain=f"old{i}.example.net", normalized_domain=f"old{i}.example.net",
                       registrable_domain=f"old{i}.example.net", public_suffix="net", source_id=manual.id)
            s.add(d)
            s.flush()
            s.add(DetectorVerdict(domain_id=d.id, rule=detector.RULE, positive=True, signals={}))
            record_threats(s, d, ["phishing"], source_slug=REVIEW_SOURCE)
    handle(["akbank-sube-giris.net"], default_watchlist(), fakeredis.FakeRedis(), CT, sessionmaker(bind=engine))
    run(engine, make_scan(engine, "akbank-sube-giris.net", title="Akbank Giriş"), Scripted(primary_category="banking"),
        settings=auto)
    assert verdicts(engine)[-1] == ("akbank-sube-giris.net", True, "flagged")
    with Session(engine) as s:
        rows = s.execute(select(ThreatIntelligence.threat_type, DataSource.slug)
                         .join(DataSource, DataSource.id == ThreatIntelligence.source_id)
                         .join(Domain, Domain.id == ThreatIntelligence.domain_id)
                         .where(Domain.normalized_domain == "akbank-sube-giris.net")).all()
        task = s.scalar(select(ReviewTask).join(Domain, Domain.id == ReviewTask.domain_id)
                        .where(Domain.normalized_domain == "akbank-sube-giris.net"))
    assert sorted((str(t), slug) for t, slug in rows) == [("lookalike-domain", "cat-egory-detector"),
                                                          ("phishing", "cat-egory-detector")]
    assert task.status == ReviewStatus.SKIPPED and task.decision_note.startswith("detector:")


def test_the_admin_tab_and_the_review_card(engine, clean, client, admin):
    with Session(engine) as s, s.begin():
        approved(s, "test-phish", LIST)
        sources.sync(s, "test-phish", data=b"garantibbva-giris.com\n", entry=LIST)
        feeds.pick(s, S)
    run(engine, make_scan(engine, "garantibbva-giris.com", title="Garanti BBVA Giriş"), Scripted(primary_category="banking"))
    r = client.get("/api/v1/admin/detector", headers=admin)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["measure"]["positive"] == 1 and body["gate"]["auto"] is False
    assert body["recent"][0]["domain"] == "garantibbva-giris.com"
    card = client.get("/api/v1/admin/reviews?queue=threats", headers=admin).json()["items"][0]
    assert card["reason"] == "feed_listed:test-phish"
    assert card["feeds"] == [{"source": "test-phish", "brand": "Garanti BBVA", "url": None}]
    assert card["detector"]["positive"] is True
