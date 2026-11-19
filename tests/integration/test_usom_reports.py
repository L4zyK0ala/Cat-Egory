"""The USOM report queue: our confirmed threats the national list lacks, sent by e-mail only when a person says so."""
import json
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from classification.engine import DETECTOR_SOURCE, record_threats
from common.config import get_settings
from database.models import Domain, ExternalListing, UsomMail, UsomReport
from threats import listings, sgb, usom

SITES = {  # name: (threat types, source, on the national list?)
    "vakifbank-kredi.net": (["phishing", "lookalike-domain"], "cat-egory-review", False),
    "evil-shop.net": (["scam"], DETECTOR_SOURCE, False),
    "already.net": (["phishing"], "cat-egory-review", True),
    "lookalike-only.net": (["lookalike-domain"], "cat-egory-review", False),
    "fresh.net": (["phishing"], "cat-egory-review", None),  # not checked yet
}


@pytest.fixture
def mail_settings(monkeypatch):
    monkeypatch.setenv("CATEGORY_USOM_MAIL_FROM", "Cat-Egory <ihbar@cat-egory.org>")
    monkeypatch.setenv("CATEGORY_RESEND_API_KEY", "test-key-not-real")
    get_settings.cache_clear()
    yield get_settings()
    get_settings.cache_clear()


@pytest.fixture
def no_mail(monkeypatch):
    """The local .env may set a sender (the server folder): these tests need none."""
    monkeypatch.setenv("CATEGORY_USOM_MAIL_FROM", "")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def sent_tasks(monkeypatch):
    from backend.app.worker import celery_app

    sent = []
    monkeypatch.setattr(celery_app, "send_task", lambda name, **kw: sent.append((name, kw.get("args"))))
    return sent


@pytest.fixture
def sites(engine, clean, client, admin):
    client.post("/api/v1/admin/domains", headers=admin, json={"domains": list(SITES), "scan": "none"})
    with Session(engine) as s, s.begin():
        for name, (types, source, listed) in SITES.items():
            d = s.scalar(select(Domain).where(Domain.normalized_domain == name))
            record_threats(s, d, types, source_slug=source)
            if listed is not None:
                listings.save(s, sgb.SOURCE, d.id, listed=listed, matches=[])
    return SITES


def get(client, admin, view="pending"):
    r = client.get("/api/v1/admin/threats/usom", headers=admin, params={"view": view})
    assert r.status_code == 200, r.text
    return r.json()


def resend(status=200, seen=None):
    def handle(request: httpx.Request):
        if seen is not None:
            seen.append(request)
        body = {"id": "msg_123"} if status < 400 else {"message": "API key is invalid"}
        return httpx.Response(status, json=body)
    return httpx.Client(transport=httpx.MockTransport(handle))


def test_the_queue_holds_only_what_we_confirmed_and_the_list_lacks(sites, client, admin, no_mail):
    q = get(client, admin)
    assert sorted(i["domain"] for i in q["items"]) == ["evil-shop.net", "vakifbank-kredi.net"]
    assert q["counts"] == {"pending": 2, "reported": 0, "listed": 0, "dismissed": 0, "unchecked": 1}
    item = {i["domain"]: i for i in q["items"]}["vakifbank-kredi.net"]
    assert item["defanged"] == "vakifbank-kredi[.]net" and item["brand"] == "VakıfBank"
    assert item["confirmed_by"] == ["review"] and item["types"] == ["lookalike-domain", "phishing"]
    assert {i["domain"]: i for i in q["items"]}["evil-shop.net"]["confirmed_by"] == ["detector"]
    assert q["mail"]["ready"] is False and q["mail"]["recipient"] == "ihbar@siberguvenlik.gov.tr"


def test_without_a_sender_nothing_can_be_sent(sites, client, admin, sent_tasks, no_mail):
    r = client.post("/api/v1/admin/threats/usom/send", headers=admin,
                    json={"domains": ["evil-shop.net"], "subject": "Bildirim", "body": "metin"})
    assert r.status_code == 409 and r.json()["error"]["code"] == "mail_not_configured" and sent_tasks == []


def test_send_report_and_listing(engine, sites, client, admin, mail_settings, sent_tasks):
    body = {"domains": ["vakifbank-kredi.net", "evil-shop.net"], "subject": "Zararlı Site  Bildirimi",
            "body": "Merhaba,\n- vakifbank-kredi[.]net\n- evil-shop[.]net"}
    r = client.post("/api/v1/admin/threats/usom/send", headers=admin, json=body)
    assert r.status_code == 202, r.text
    mail = r.json()
    assert mail["status"] == "queued" and mail["recipient"] == "ihbar@siberguvenlik.gov.tr"
    assert sent_tasks == [("category.threats.usom_mail", [mail["id"]])]
    assert all(i["sending"] for i in get(client, admin)["items"])
    again = client.post("/api/v1/admin/threats/usom/send", headers=admin, json=body | {"domains": ["evil-shop.net"]})
    assert again.status_code == 422 and "already being sent" in again.json()["error"]["message"]

    seen: list[httpx.Request] = []
    with Session(engine) as s, resend(seen=seen) as http:
        assert usom.deliver(s, mail["id"], http, mail_settings) == "sent"
        assert usom.deliver(s, mail["id"], http, mail_settings) == "sent"  # a second delivery sends nothing
    assert len(seen) == 1
    sent = json.loads(seen[0].content)
    assert seen[0].headers["Authorization"] == "Bearer test-key-not-real"
    assert sent == {"from": "Cat-Egory <ihbar@cat-egory.org>", "to": ["ihbar@siberguvenlik.gov.tr"],
                    "subject": "Zararlı Site Bildirimi", "text": body["body"] + "\n"}
    assert client.get(f"/api/v1/admin/threats/usom/mails/{mail['id']}", headers=admin).json()["status"] == "sent"
    q = get(client, admin, "reported")
    assert q["counts"]["pending"] == 0 and q["counts"]["reported"] == 2
    assert {i["mail_id"] for i in q["items"]} == {mail["id"]}

    # the daily cross-check finds one on the list two days later
    with Session(engine) as s, s.begin():
        d = s.scalar(select(Domain.id).where(Domain.normalized_domain == "evil-shop.net"))
        s.execute(update(UsomReport).where(UsomReport.domain_id == d)
                  .values(status_at=datetime.now(UTC) - timedelta(days=2)))
        listings.save(s, sgb.SOURCE, d, listed=True, matches=[])
        assert usom.mark_listed(s) == 1
        assert usom.mark_listed(s) == 0
    q = get(client, admin, "listed")
    assert [i["domain"] for i in q["items"]] == ["evil-shop.net"] and q["items"][0]["days_to_list"] == 2.0
    assert q["median_days_to_list"] == 2.0 and q["counts"]["reported"] == 1


def test_a_refused_mail_reports_nothing_and_never_shows_the_key(engine, sites, client, admin, mail_settings,
                                                               sent_tasks):
    r = client.post("/api/v1/admin/threats/usom/send", headers=admin,
                    json={"domains": ["evil-shop.net"], "subject": "Bildirim", "body": "metin"})
    with Session(engine) as s, resend(status=401) as http:
        assert usom.deliver(s, r.json()["id"], http, mail_settings) == "failed"
    out = client.get(f"/api/v1/admin/threats/usom/mails/{r.json()['id']}", headers=admin).json()
    assert out["status"] == "failed" and "HTTP 401" in out["error"] and "test-key" not in out["error"]
    q = get(client, admin)
    assert "evil-shop.net" in [i["domain"] for i in q["items"]] and not any(i["sending"] for i in q["items"])


@pytest.mark.parametrize("domains, reason", [
    (["lookalike-only.net"], "no active confirmed threat"),
    (["nobody-knows.net"], "unknown domains"),
])
def test_only_our_reportable_threats_can_be_sent(sites, client, admin, mail_settings, sent_tasks, domains, reason):
    r = client.post("/api/v1/admin/threats/usom/send", headers=admin,
                    json={"domains": domains, "subject": "Bildirim", "body": "metin"})
    assert r.status_code == 422 and reason in r.json()["error"]["message"] and sent_tasks == []


def test_set_aside_and_back(engine, sites, client, admin, mail_settings, sent_tasks):
    url = "/api/v1/admin/threats/usom/status"
    assert client.post(url, headers=admin, json={"domains": ["evil-shop.net"], "action": "dismissed"}).json() == {"changed": 1}
    assert [i["domain"] for i in get(client, admin, "dismissed")["items"]] == ["evil-shop.net"]
    r = client.post("/api/v1/admin/threats/usom/send", headers=admin,
                    json={"domains": ["evil-shop.net"], "subject": "Bildirim", "body": "metin"})
    assert r.status_code == 422 and "set aside" in r.json()["error"]["message"]
    client.post(url, headers=admin, json={"domains": ["evil-shop.net"], "action": "pending"})
    assert "evil-shop.net" in [i["domain"] for i in get(client, admin)["items"]]
    with Session(engine) as s:
        assert s.scalar(select(UsomReport.id)) is None and s.scalar(select(UsomMail.id)) is None


def test_needs_a_reviewer(client, make_key):
    contributor, _ = make_key("contributor")
    assert client.get("/api/v1/admin/threats/usom", headers=contributor).status_code == 403
    assert ExternalListing  # imported for the fixtures' rows


def test_one_mail_holds_up_to_100_domains_and_a_long_text(sites, client, admin, mail_settings, sent_tasks):
    url = "/api/v1/admin/threats/usom/send"
    many = client.post(url, headers=admin, json={"domains": [f"d{i}.net" for i in range(101)], "subject": "B",
                                                 "body": "metin"})
    assert many.status_code == 422
    assert many.json()["error"]["details"][0]["loc"] == ["body", "domains"]  # the panel names the field
    long = client.post(url, headers=admin, json={"domains": ["evil-shop.net"], "subject": "B", "body": "x" * 30000})
    assert long.status_code == 202, long.text
