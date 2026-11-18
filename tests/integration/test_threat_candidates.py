"""Threat lists as candidates (threats/feeds.py) and our own page-proof detector (threats/detector.py)."""
import gzip
import uuid
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
    JobStatus,
    ReviewStatus,
    ReviewTask,
    ScanJob,
    ScanResult,
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


def page(html=PHISH, host="garantibbva-giris.com", title="Garanti BBVA Giriş", cert_days=None, text="Hoş geldiniz",
         language=None):
    now = datetime(2026, 11, 17, tzinfo=UTC)
    return detector.Page(host=host, title=title, description=None, text=text, html=html,
                         cert_valid_from=(now - timedelta(days=cert_days)).timestamp() if cert_days is not None else None,
                         scanned_at=now, language=language)


def test_established_sites_are_spared_and_phishing_abroad_counts():
    # v1 called Vodafone UK's own sign-in pages phishing: a ranked site's own pages go to a person
    uk = page(host="login-qc5.dx-idm.vodafone.co.uk", title="Vodafone sign in", text="Welcome back, sign in below")
    yes, signals = detector.judge(uk, suspicious_name="brand_lookalike", protected=None, popular="vodafone.co.uk")
    assert not yes and signals["popular"] == "vodafone.co.uk"
    # Cat-egory lists threats worldwide: an English page imitating a brand is phishing (a person flagged this one)
    partner = page(host="vodafone.partner.simo.co", title="Vodafone Partner Login", text="Welcome, partner")
    yes, signals = detector.judge(partner, suspicious_name="feed_listed:phishing-database", protected=None)
    assert yes and signals["kind"] == "phishing" and signals["turkish"] is False  # language recorded, not required
    greek = page(host="ebank.ziraatbank.com.gr", title="e-Banking", text="Welcome to e-Banking")
    assert detector.judge(greek, suspicious_name="feed_listed:phishing-database", protected=None)[0]
    assert detector.turkish(page(host="sube-giris.com.tr", title="Login", text="Welcome"))
    assert detector.turkish(page(host="x.com", title="Login", text="Welcome", language="tr-TR"))


TEL = "<form><input name='ad_soyad'><input type='tel' name='telefon'><button>Başvur</button></form>"


def test_v3_finds_fake_investment_platforms():
    scam = page(host="isbankasitech.pro", title="İş Bankası | Yapay Zeka Alım Satım | Resmî Web Sitesi",
                text="Yatırımınızı yapay zeka yönetsin, kazancınızı katlayın", html=TEL)
    yes, signals = detector.judge(scam, suspicious_name="brand_lookalike", protected=None)
    assert yes and signals["kind"] == "investment_scam" and signals["phone"] and signals["secrets"] == []
    assert {"yatirim", "yapay zeka", "alim satim", "resmi web sitesi"} <= set(signals["invest_terms"])
    assert detector.THREATS[signals["kind"]] == "scam"
    no_form = page(host="isbankasitech.pro", title=scam.title, text=scam.text, html="<p>Yakında</p>")
    assert not detector.judge(no_form, suspicious_name="brand_lookalike", protected=None)[0]
    # a call-back form without investment talk is no investment scam
    callback = page(host="halkbank-destek.com", title="Halkbank Müşteri Hizmetleri", text="Sizi arayalım", html=TEL)
    assert not detector.judge(callback, suspicious_name="brand_lookalike", protected=None)[0]
    # nor is the bank's own site
    own = page(host="www.isbank.com.tr", title=scam.title, text=scam.text, html=TEL)
    assert not detector.judge(own, suspicious_name="x", protected=None)[0]


def test_v3_knows_worldwide_brands():
    login = page(host="paypal-secure-login.com", title="Log in to your PayPal account", text="Email or mobile number",
                 html="<form><input name=email><input type=password name=pass></form>")
    yes, signals = detector.judge(login, suspicious_name="feed_listed:openphish", protected=None)
    assert yes and signals["brand"] == "PayPal"
    assert not detector.judge(page(host="www.paypal.com", title=login.title, html=login.html),
                              suspicious_name="x", protected=None)[0]  # its official site
    host_only = page(host="web.whatsapp.com.afrcloud2.c01.kr", title="Sign in", text="")
    assert detector.claimed_brand(host_only).brand == "WhatsApp"
    # a site offering another company's login button is not that company (trippd.com, replay 2026-11-18)
    social = page(host="trippd.com", title="Trippd - plan your trip", text="Continue with Google or Facebook ile giriş",
                  html="<input type=password>")
    yes, signals = detector.judge(social, suspicious_name="brand_lookalike", protected=None)
    assert not yes and signals["brand"] is None
    embedded = page(host="trippd.com", title="trippd.com - Find Fun", html="<input type=password>",
                    text="trippd.com login Map Satellite Keyboard shortcuts Map data ©2026 Google, INEGI Terms")
    assert detector.claimed_brand(embedded) is None  # an embedded map is not a claim
    copied = page(host="paypal-secure.top", title="Log in", text="Email ... © 2026 PayPal. All rights reserved.")
    assert detector.claimed_brand(copied).brand == "PayPal"  # a copied footer is
    office = page(host="office365-verify.top", title="Sign in", text="Sign in with your Microsoft account",
                  html="<input type=password>")
    assert detector.judge(office, suspicious_name="feed_listed:openphish", protected=None)[1]["brand"] == "Microsoft"
    assert feeds.relevance("paypal-secure-login.com") == (0, "PayPal")
    assert feeds.relevance("random-blog.net") == (0, None)
    assert feeds.relevance("garantibbva-giris.com")[0] == 2 and feeds.relevance("haber.com.tr") == (1, None)


def test_v2_reads_the_brand_from_the_address_and_ignores_lists_of_brands():
    # the brand only in the logo: the title says "Giriş", the address says e-Devlet
    edevlet = page(host="portal-e-devlet.com", title="Giriş", text="Lütfen T.C. kimlik numaranızı girin")
    yes, signals = detector.judge(edevlet, suspicious_name="feed_listed:phishing-database", protected=None)
    assert yes and (signals["brand"], signals["brand_via"]) == ("e-Devlet", "host")
    # a short token in a host name is no claim (cPanel of a customer "hgs" at a hosting company)
    assert detector.claimed_brand(page(host="cpanel.stz.hgs.mybluehost.me", title="cPanel Giriş", text="")) is None
    # a gambling site listing banks as payment methods claims none of them
    casino = page(host="sekabet5544.com", title="Sekabet", text="Yatırım yöntemleri: Papara, TEB, Ziraat Bankası")
    yes, signals = detector.judge(casino, suspicious_name=None, protected=None, popular=None)
    assert not yes and signals["brand"] is None and signals["listed_brands"] == ["Ziraat Bankası", "TEB", "Papara"]
    assert detector.claimed_brand(page(title="Giriş", text="Akbank İnternet Şubesi", host="sube-giris.net")).brand \
        == "Akbank"  # one brand in the text is a claim
    # a site going by its own name that merely contains a brand string: "Fraisbank" is not İş Bankası
    frais = page(host="fraisbank.fr", title="Fraisbank", text="Bienvenue chez Fraisbank")
    assert detector.claimed_brand(frais) is None
    assert detector.claimed_brand(page(host="acikdenizbankasyvvyi.plesk.page", title="Giriş", text="")).brand \
        == "DenizBank"  # the address word is not the page's own name
    # a brand string must start a word: "fraisbank" is not İş Bankası, "garantibbvamobil" is Garanti BBVA
    assert detector.brands_named("Fraisbank, la banque") == []
    assert [b.name for b in detector.brands_named("garantibbvamobil giriş")] == ["Garanti BBVA"]


def test_v2_knows_the_popular_sites(engine, clean, monkeypatch):
    monkeypatch.setattr(detector, "POPULAR_SOURCES", ("test-top",))
    entry = {"name": "Test top list", "kind": "dataset", "format": "domains", "feed_url": "https://x.example.org/t.txt",
             "registrable_only": True}
    with Session(engine) as s, s.begin():
        approved(s, "test-top", entry)
        sources.sync(s, "test-top", data=b"vodafone.co.uk\n", entry=entry)
    with Session(engine) as s:
        assert detector.popular(s, "login-qc5.dx-idm.vodafone.co.uk") == "vodafone.co.uk"
        assert detector.popular(s, "vodafone-uk-giris.com") is None


def test_the_rule_needs_a_secret_a_brand_and_a_suspect_host():
    yes, signals = detector.judge(page(), suspicious_name="brand_lookalike", protected=None)
    assert yes and signals["secrets"] == ["password", "sms_code", "tr_id"] and signals["brand"] == "Garanti BBVA"
    assert detector.judge(page(cert_days=3), suspicious_name=None, protected=None)[0]       # a new certificate will do
    assert not detector.judge(page(cert_days=90), suspicious_name=None, protected=None)[0]  # an old one alone will not
    assert not detector.judge(page(html="<p>Garanti BBVA kampanya</p>"), suspicious_name="x", protected=None)[0]
    assert not detector.judge(page(host="kedi-giris.com", title="Kedi maması", html="<input type=password>"),
                              suspicious_name="x", protected=None)[0]  # no brand claimed
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


def test_the_admin_tab_and_the_review_card(engine, clean, client, admin, monkeypatch, request):
    from common.config import get_settings

    monkeypatch.setenv("CATEGORY_THREAT_DETECTOR_AUTO", "false")  # whatever a local .env says
    get_settings.cache_clear()
    request.addfinalizer(get_settings.cache_clear)
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


# ----------------------------------------------------------------------------- worldwide candidates, one-page scans


PAYPAL = ("<html><head><title>Log in to your PayPal account</title></head><body><form>"
          "<input name=email><input type=password name=pass></form></body></html>")


def _scan_of(engine, job_id, *, url, html_key, title):
    """What the crawler would hand back for a job, stored as the pipeline does."""
    from backend.app.scan_tasks import store_result
    from crawler.result import Artifact, CrawlResult

    result = CrawlResult(job_id=job_id, domain="", requested_url=url, final_url=url, http_status=200,
                         page_title=title, resolved_ips=["93.184.215.14"], text_excerpt=title,
                         html=Artifact(html_key, "b" * 64, 100, "text/html"), sandboxed=True)
    with Session(engine) as s, s.begin():
        assert store_result(s, result, job_id, S) == "succeeded"
        return s.scalar(select(ScanResult.id).where(ScanResult.scan_job_id == uuid.UUID(job_id)))


def test_worldwide_candidates_and_one_page_scans(engine, clean, monkeypatch):
    from backend.app.scan_tasks import claim_jobs, precheck
    from database.models import Url
    from tests.integration.test_scan_pipeline import FakeResolver
    from threats import pages

    links = URLS | {"name": "Test phish links"}
    monkeypatch.setattr(feeds, "phishing_slugs", lambda: ["test-links"])
    with Session(engine) as s, s.begin():
        approved(s, "test-links", links)
        sources.sync(s, "test-links", entry=links, data=(
            b"https://paypal-secure-login.com/signin/index.php?email=victim@example.org#x\n"
            b"https://random-blog.net/\n"
            b"https://garden-club.org/wp-content/plugins/x/login.php\n"))
    worldwide = Settings(_env_file=None, threat_feed_candidates_per_hour=0, threat_feed_global_per_hour=40)
    with Session(engine) as s, s.begin():
        assert sorted(feeds.pick(s, worldwide)) == ["garden-club.org", "paypal-secure-login.com", "random-blog.net"]
    with Session(engine) as s:
        queued = {s.get(Domain, j.domain_id).normalized_domain: (s.get(Url, j.url_id).url if j.url_id else None)
                  for j in s.scalars(select(ScanJob))}
    assert queued == {  # the listed page, without the victim's address; a bare home page is a normal scan
        "paypal-secure-login.com": "https://paypal-secure-login.com/signin/index.php",
        "garden-club.org": "https://garden-club.org/wp-content/plugins/x/login.php",
        "random-blog.net": None}

    with Session(engine) as s, s.begin():
        claim_jobs(s, Settings(_env_file=None, max_in_flight_scans=10))
    with Session(engine) as s:
        job_ids = {s.get(Domain, j.domain_id).normalized_domain: str(j.id) for j in s.scalars(select(ScanJob))}
    zone = {name: {"A": ["93.184.215.14"]} for name in job_ids}
    with Session(engine) as s, s.begin():
        payload = precheck(s, job_ids["paypal-secure-login.com"], S, FakeResolver(zone))
    assert payload["url"] == "https://paypal-secure-login.com/signin/index.php"

    store = MemoryStore()
    store.put("paypal.gz", gzip.compress(PAYPAL.encode()), "application/gzip")
    store.put("blog.gz", gzip.compress(b"<html><body><h1>My garden</h1></body></html>"), "application/gzip")
    phish = _scan_of(engine, job_ids["paypal-secure-login.com"], url=payload["url"], html_key="paypal.gz",
                     title="Log in to your PayPal account")
    blog = _scan_of(engine, job_ids["random-blog.net"], url="https://random-blog.net/", html_key="blog.gz",
                    title="My garden")
    with Session(engine) as s:
        d = s.scalar(select(Domain).where(Domain.normalized_domain == "paypal-secure-login.com"))
        assert d.last_scanned is None and d.scan_count == 0 and d.next_scan is None  # a page is not the site
        assert s.get(ScanResult, phish).url_id is not None
        assert pages.detector_only(s, s.get(ScanResult, phish)) and pages.detector_only(s, s.get(ScanResult, blog))

    with Session(engine) as s, s.begin():
        assert pages.page_check(s, phish, S, store) == "review"      # asks for a password: a person looks
        assert pages.page_check(s, blog, S, store) == "measured"     # worldwide, asks for nothing: no review
    with Session(engine) as s:
        open_reviews = [s.get(Domain, t.domain_id).normalized_domain for t in s.scalars(select(ReviewTask))]
        v = s.scalar(select(DetectorVerdict).where(DetectorVerdict.scan_result_id == phish))
    assert open_reviews == ["paypal-secure-login.com"]
    assert v.positive and v.url_id is not None and v.signals["brand"] == "PayPal"


def test_a_proven_detector_flags_only_the_listed_page(engine, clean, monkeypatch):
    from database.models import Url
    from threats import pages

    links = URLS | {"name": "Test phish links"}
    monkeypatch.setattr(feeds, "phishing_slugs", lambda: ["test-links"])
    with Session(engine) as s, s.begin():  # 20 earlier positives a person confirmed: the rule has proven itself
        manual = s.scalar(select(DataSource).where(DataSource.slug == "manual"))
        for i in range(20):
            d = Domain(domain=f"old{i}.example.net", normalized_domain=f"old{i}.example.net",
                       registrable_domain=f"old{i}.example.net", public_suffix="net", source_id=manual.id)
            s.add(d)
            s.flush()
            s.add(DetectorVerdict(domain_id=d.id, rule=detector.RULE, positive=True, signals={}))
            record_threats(s, d, ["phishing"], source_slug=REVIEW_SOURCE)
    with Session(engine) as s, s.begin():
        approved(s, "test-links", links)
        sources.sync(s, "test-links", entry=links, data=b"https://paypal-secure-login.com/signin/\n")
        feeds.pick(s, Settings(_env_file=None, threat_feed_candidates_per_hour=0, threat_feed_global_per_hour=4))
        job = s.scalar(select(ScanJob))
        job.status, job_id = JobStatus.RUNNING, str(job.id)
    store = MemoryStore()
    store.put("paypal.gz", gzip.compress(PAYPAL.encode()), "application/gzip")
    scan = _scan_of(engine, job_id, url="https://paypal-secure-login.com/signin/", html_key="paypal.gz",
                    title="Log in to your PayPal account")
    auto = Settings(_env_file=None, threat_detector_auto=True, threat_detector_min_decisions=20,
                    threat_detector_sample_every=1000)
    with Session(engine) as s, s.begin():
        assert pages.page_check(s, scan, auto, store) == "flagged"
    with Session(engine) as s:
        rows = s.execute(select(ThreatIntelligence.threat_type, Url.url).join(Url, Url.id == ThreatIntelligence.url_id)
                         .join(DataSource, DataSource.id == ThreatIntelligence.source_id)
                         .where(DataSource.slug == detector.SOURCE)).all()
        whole = s.scalar(select(func.count()).select_from(ThreatIntelligence).join(
            Domain, Domain.id == ThreatIntelligence.domain_id).where(
            Domain.normalized_domain == "paypal-secure-login.com", ThreatIntelligence.url_id.is_(None)))
        task = s.scalar(select(ReviewTask))
    assert [(str(t), u) for t, u in rows] == [("phishing", "https://paypal-secure-login.com/signin/")]
    assert whole == 0  # the page, not the host (it may be a hacked, honest site)
    assert task.status == ReviewStatus.SKIPPED and task.decision_note.startswith("detector:")


def test_a_long_link_list_keeps_only_watched_names(engine, clean):
    links = URLS | {"name": "Test long links", "watched_only": True}
    with Session(engine) as s, s.begin():
        approved(s, "test-long", links)
        sources.sync(s, "test-long", entry=links, data=(b"https://random-blog.net/a\nhttps://paypal-login.top/x\n"
                                                        b"https://akbank-giris.net/y\nhttps://haber.com.tr/z\n"))
    assert {h: (r, b) for h, (r, b, _, _) in entries(engine).items()} == {
        "paypal-login.top": (0, "PayPal"), "akbank-giris.net": (2, "Akbank"), "haber.com.tr": (1, None)}
