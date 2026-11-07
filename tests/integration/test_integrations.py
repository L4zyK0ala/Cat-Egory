import httpx
import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from database.models import DataSource, Domain, DomainCategory, DomainSourceRecord, ThreatIntelligence
from integrations.engine import SourceError, approve, catalogue, download, register, sync

HOSTS = b"# test list\n0.0.0.0 bet1.example.com\n0.0.0.0 bet2.example.com\n0.0.0.0 localhost\nnot a domain!\n"
ENTRY = {"name": "Test list", "kind": "blocklist", "format": "hosts", "category": "gambling",
         "feed_url": "https://lists.example.com/hosts"}
THREAT = {"name": "Test threats", "kind": "threat_feed", "format": "url-list", "threat_type": "phishing",
          "feed_url": "https://lists.example.com/phish.txt"}


def approved(session, slug, entry, redistribution=True):
    register(session, slug, entry)
    approve(session, slug, license_slug=f"test-{slug}", license_name="Test licence", license_url=None,
            redistribution_allowed=redistribution, commercial_use_allowed=True, attribution_required=True,
            attribution="Test", reviewer_id=None)


def test_catalogue_is_all_unverified():
    entries = catalogue()
    assert {"urlhaus", "openphish", "stevenblack-hosts"} <= set(entries)
    assert all(e["license_status"] == "unverified" for e in entries.values())


def test_sync_requires_approval(engine, clean):
    with Session(engine) as s, s.begin():
        register(s, "test-list", ENTRY)
        with pytest.raises(SourceError) as exc:
            sync(s, "test-list", data=HOSTS, entry=ENTRY)
        assert exc.value.code == "not_approved"


def test_blocklist_sync_and_delisting(engine, clean):
    with Session(engine) as s, s.begin():
        approved(s, "test-list", ENTRY)
        r = sync(s, "test-list", data=HOSTS, entry=ENTRY)
    assert (r.entries, r.valid, r.invalid, r.new_domains) == (3, 2, 1, 2)
    with Session(engine) as s:
        rec = s.scalar(select(DomainSourceRecord).limit(1))
        assert rec.source_category == "gambling" and rec.is_listed
        assert s.scalar(select(func.count()).select_from(DomainCategory)) == 0  # labels are not our classification
        src = s.scalar(select(DataSource).where(DataSource.slug == "test-list"))
        assert src.record_count == 2 and src.last_success_at and float(src.quality_score) == pytest.approx(0.667)
    with Session(engine) as s, s.begin():
        r = sync(s, "test-list", data=b"0.0.0.0 bet1.example.com\n", entry=ENTRY)
    assert r.delisted == 1 and r.new_domains == 0
    with Session(engine) as s:
        listed = dict(s.execute(select(Domain.normalized_domain, DomainSourceRecord.is_listed)
                                .join(DomainSourceRecord, DomainSourceRecord.domain_id == Domain.id)).all())
    assert listed == {"bet1.example.com": True, "bet2.example.com": False}


@pytest.mark.parametrize("redistribution", [True, False])
def test_threat_feed_respects_licence(engine, clean, client, admin, redistribution):
    client.post("/api/v1/admin/domains", headers=admin, json={"domains": ["login-bank.example.com"], "scan": "none"})
    with Session(engine) as s, s.begin():
        approved(s, "test-phish", THREAT, redistribution=redistribution)
        r = sync(s, "test-phish", data=b"https://login-bank.example.com/verify?x=1\nhttp://10.0.0.1/a\n", entry=THREAT)
    assert r.valid == 1 and r.threats_active == 1
    risk = client.get("/api/v1/domain/login-bank.example.com").json()["risk"]
    assert risk["phishing"] is redistribution
    with Session(engine) as s, s.begin():
        sync(s, "test-phish", data=b"", entry=THREAT)  # entry left the feed
    with Session(engine) as s:
        assert s.scalar(select(ThreatIntelligence.is_active)) is False


def test_download_policy():
    with pytest.raises(SourceError) as exc:
        download("http://127.0.0.1/list.txt")
    assert exc.value.code == "blocked_url"
    with pytest.raises(SourceError) as exc:
        download("https://lists.example.com/x", resolver=lambda h, p: ["10.1.1.1"])
    assert exc.value.code == "blocked_url"

    def handler(request):
        return httpx.Response(302, headers={"Location": "http://169.254.169.254/"}) if "redirect" in request.url.path \
            else httpx.Response(200, content=b"0.0.0.0 a.example.com\n")

    client = httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=False)
    public = lambda h, p: ["93.184.215.14"]  # noqa: E731
    assert download("https://lists.example.com/ok", client=client, resolver=public).startswith(b"0.0.0.0")
    with pytest.raises(SourceError) as exc:
        download("https://lists.example.com/redirect", client=client, resolver=public)
    assert exc.value.code == "http_status"  # redirects are not followed


def test_failed_download_is_recorded(engine, clean):
    with Session(engine) as s, s.begin():
        approved(s, "test-list", ENTRY)
        s.execute(DataSource.__table__.update().where(DataSource.slug == "test-list")
                  .values(feed_url="http://127.0.0.1/hosts"))
        r = sync(s, "test-list", entry=ENTRY)
    assert r.errors and "blocked_url" in r.errors[0]
    with Session(engine) as s:
        assert "blocked_url" in s.scalar(select(DataSource.last_error).where(DataSource.slug == "test-list"))
