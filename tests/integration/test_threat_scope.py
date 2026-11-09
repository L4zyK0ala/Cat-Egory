"""A threat on one page must not flag the whole site (threats.records): domain page, URL lookup, risk listing,
stats and feeds all keep domain-scoped and URL-scoped records apart."""
import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from classification.engine import record_threats
from database.models import DataSource, Domain, ThreatIntelligence, ThreatType, Url
from feeds.builder import threat_members
from threats.records import UnknownDomain, record_url_threats, url_row, withdraw

PAGE = "https://Shop.Example.com/wp-content/x/login.php?id=7#top"


def setup(engine, client, admin):
    client.post("/api/v1/admin/domains", headers=admin,
                json={"domains": ["shop.example.com", "evil.example.net"], "source": "manual", "scan": "none"})
    with Session(engine) as s, s.begin():
        evil = s.scalar(select(Domain).where(Domain.normalized_domain == "evil.example.net"))
        record_threats(s, evil, ["phishing"])
        community = s.scalar(select(DataSource).where(DataSource.slug == "community"))  # second public source
        s.add(ThreatIntelligence(domain_id=evil.id, threat_type=ThreatType.PHISHING, source_id=community.id))
        url, added = record_url_threats(s, PAGE, ["phishing"])
        assert added == 1 and url.normalized_url == "https://shop.example.com/wp-content/x/login.php?id=7"


def test_page_threat_does_not_flag_the_site(engine, client, admin):
    setup(engine, client, admin)
    shop = client.get("/api/v1/domain/shop.example.com").json()
    assert shop["risk"]["phishing"] is False and shop["risk"]["reported_urls"] == 1
    evil = client.get("/api/v1/domain/evil.example.net").json()
    assert evil["risk"]["phishing"] is True and evil["risk"]["reported_urls"] == 0

    exact = client.get("/api/v1/url", params={"url": PAGE}).json()
    assert exact["url_risk"]["phishing"] is True and exact["result"]["risk"]["phishing"] is False
    other = client.get("/api/v1/url", params={"url": "https://shop.example.com/contact"}).json()
    assert other["url_risk"]["phishing"] is False

    # listed once although two sources report it; the hacked shop is not listed at all
    assert client.get("/api/v1/categories/phishing/domains").json()["items"] == [{"domain": "evil.example.net"}]
    stats = client.get("/api/v1/stats").json()
    assert stats["threats"] == {"phishing": 1} and stats["threat_urls"] == {"phishing": 1}
    with Session(engine) as s:
        assert threat_members(s) == {"phishing": ["evil.example.net"]}


def test_url_threats_need_a_known_host(engine, client, admin):
    with Session(engine) as s, s.begin(), pytest.raises(UnknownDomain):
        record_url_threats(s, "https://not-added.example.org/login", ["phishing"])


def test_withdraw_keeps_scopes_apart(engine, client, admin):
    setup(engine, client, admin)
    with Session(engine) as s, s.begin():
        shop = s.scalar(select(Domain).where(Domain.normalized_domain == "shop.example.com"))
        assert withdraw(s, domain=shop) == 0  # no domain-scoped record on the shop
        assert withdraw(s, domain=shop, url=url_row(s, PAGE, create=False)) == 1
        evil = s.scalar(select(Domain).where(Domain.normalized_domain == "evil.example.net"))
        assert withdraw(s, domain=evil, threat=ThreatType.PHISHING) == 1  # ours only; the community one stays
        assert s.scalar(select(Url.id).where(Url.domain_id == shop.id)) is not None  # history kept
    assert client.get("/api/v1/domain/shop.example.com").json()["risk"]["reported_urls"] == 0
    assert client.get("/api/v1/domain/evil.example.net").json()["risk"]["phishing"] is True


def test_admin_threat_api(client, admin, make_key):
    client.post("/api/v1/admin/domains", headers=admin,
                json={"domains": ["shop.example.com", "evil.example.net"], "source": "manual", "scan": "none"})
    api = "/api/v1/admin/threats"
    note = "login form copies the bank's page"

    r = client.post(api, headers=admin, json={"url": PAGE, "types": ["phishing"], "evidence": note})
    assert r.status_code == 201 and r.json()["scope"] == "url" and r.json()["changed"] == 1
    r = client.post(api, headers=admin, json={"domain": "evil.example.net", "types": ["phishing", "scam"],
                                              "evidence": note})
    assert r.json() == {"target": "evil.example.net", "scope": "domain", "changed": 2}

    # validation: one target, known types, a real evidence note, a known host
    assert client.post(api, headers=admin, json={"domain": "evil.example.net", "url": PAGE, "types": ["phishing"],
                                                 "evidence": note}).status_code == 422
    assert client.post(api, headers=admin, json={"domain": "evil.example.net", "types": ["news"],
                                                 "evidence": note}).status_code == 422
    assert client.post(api, headers=admin, json={"domain": "evil.example.net", "types": ["phishing"],
                                                 "evidence": "bad"}).status_code == 422
    assert client.post(api, headers=admin, json={"url": "https://unknown.example.org/x", "types": ["phishing"],
                                                 "evidence": note}).status_code == 404
    contributor = make_key("contributor")[0]
    assert client.post(api, headers=contributor, json={"domain": "evil.example.net", "types": ["phishing"],
                                                       "evidence": note}).status_code == 403

    items = client.get(api, headers=admin, params={"scope": "url"}).json()["items"]
    assert [(i["url"], i["evidence"]) for i in items] == [
        ("https://shop.example.com/wp-content/x/login.php?id=7", note)]
    public_page = client.get("/api/v1/domain/evil.example.net").json()
    assert public_page["risk"]["scam"] is True and note not in str(public_page)  # evidence stays internal

    r = client.post(f"{api}/withdraw", headers=admin, json={"domain": "evil.example.net", "type": "scam",
                                                           "reason": "owner showed it is a real shop"})
    assert r.json()["changed"] == 1
    r = client.post(f"{api}/withdraw", headers=admin, json={"url": PAGE, "reason": "page removed, site cleaned"})
    assert r.json()["changed"] == 1
    assert client.get("/api/v1/domain/evil.example.net").json()["risk"]["scam"] is False
    assert client.get("/api/v1/domain/shop.example.com").json()["risk"]["reported_urls"] == 0
    assert len(client.get(api, headers=admin, params={"active": False}).json()["items"]) == 2
