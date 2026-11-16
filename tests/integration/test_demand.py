"""Demand: looked-up domains and list downloads as daily totals, never who asked; demand moves discovery."""
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import fakeredis
import pytest
from pydantic import SecretStr
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker
from starlette.requests import Request

from backend.app import demand
from backend.app.discovery import queue_discovery
from common.config import Settings, get_settings
from database.models import (
    DataSource,
    DemandDaily,
    Domain,
    DomainSourceRecord,
    FeedDownloadDaily,
    ScanJob,
)

NOW = datetime.now(UTC)


@pytest.fixture
def secret(monkeypatch):
    monkeypatch.setattr(get_settings(), "fingerprint_secret", SecretStr("demand-test"))


def today():
    return demand.local_day(datetime.now(UTC), get_settings())


def counts(redis, day=None):
    return {k.decode(): int(v) for k, v in redis.hgetall(f"demand:{day or today()}:n").items()}


def callers(redis, name, day=None):
    return redis.pfcount(f"demand:{day or today()}:c:{name}")


# --------------------------------------------------------------------------- counting in the API

def test_lookups_are_counted_by_registrable_domain_known_or_not(client, fake_redis, clean, secret):
    assert client.get("/api/v1/domain/shop.unknown-example.com").status_code == 404
    client.get("/api/v1/url", params={"url": "https://unknown-example.com/a/b?c=d"})
    client.post("/api/v1/lookup", json={"domains": ["other-example.org", "http://x.other-example.org/", "not a name"]})
    # one bulk request naming a site twice counts it once
    assert counts(fake_redis) == {"unknown-example.com": 2, "other-example.org": 1}
    assert callers(fake_redis, "unknown-example.com") == 1  # one visitor (the test client), asked twice


@pytest.mark.parametrize("path", ["/api/v1/domain/10.0.0.1", "/api/v1/domain/printer.local", "/api/v1/domain/a b"])
def test_garbage_and_private_names_are_not_counted(client, fake_redis, clean, path):
    client.get(path)
    assert counts(fake_redis) == {}


def test_a_secondary_request_of_the_same_page_view_is_not_counted(client, fake_redis, clean):
    client.get("/api/v1/domain/unknown-example.com", headers={"X-Category-Demand": "skip"})
    assert counts(fake_redis) == {}


def test_the_site_key_tells_visitors_apart_other_keys_count_once(client, fake_redis, clean, secret, make_key):
    site, _ = make_key("contributor", scopes=["community:write", "visitors:forward"])
    for ip in ("203.0.113.1", "203.0.113.2", "2001:db8::1", "2001:db8::2"):  # IPv6 in one /64 is one visitor
        client.get("/api/v1/domain/wanted-example.com", headers=site | {"X-Category-Visitor": ip})
    assert callers(fake_redis, "wanted-example.com") == 3

    other, _ = make_key("contributor")  # no visitors:forward: the header is ignored, the key is the caller
    for ip in ("198.51.100.1", "198.51.100.2", "198.51.100.3"):
        client.get("/api/v1/domain/scripted-example.com", headers=other | {"X-Category-Visitor": ip})
    assert callers(fake_redis, "scripted-example.com") == 1


def test_the_number_of_names_a_day_is_bounded(client, fake_redis, clean, monkeypatch):
    monkeypatch.setattr(get_settings(), "demand_max_names_per_day", 2)
    for name in ("a-example.com", "b-example.com", "c-example.com", "a-example.com"):
        client.get(f"/api/v1/domain/{name}")
    assert counts(fake_redis) == {"a-example.com": 2, "b-example.com": 1}  # known names still count


def test_lookups_work_while_redis_is_down(engine, clean):
    from fastapi.testclient import TestClient

    from tests.integration.test_hardening import build

    server = fakeredis.FakeServer()
    server.connected = False
    client = TestClient(build(engine, fakeredis.FakeRedis(server=server)))
    assert client.get("/api/v1/domain/unknown-example.com").status_code == 404


def test_counting_can_be_switched_off(client, fake_redis, clean, monkeypatch):
    monkeypatch.setattr(get_settings(), "demand_counting", False)
    client.get("/api/v1/domain/unknown-example.com")
    assert counts(fake_redis) == {}


def request(headers=None):
    return Request({"type": "http", "method": "GET", "path": "/", "headers": [
        (k.lower().encode(), v.encode()) for k, v in (headers or {}).items()]})


def test_feed_downloads_count_requests_and_distinct_callers(fake_redis, secret):
    for fp in ("a", "b", "a"):
        demand.record_feed(fake_redis, request(), SimpleNamespace(authenticated=False, fingerprint=fp), "gambling.txt")
    assert int(fake_redis.hget(f"demand:{today()}:feeds", "gambling.txt")) == 3
    assert fake_redis.pfcount(f"demand:{today()}:fc:gambling.txt") == 2


# --------------------------------------------------------------------------- nightly roll-up

def visit(redis, name, *visitors, when=NOW):
    for v in visitors:
        demand.record_lookups(redis, request(), SimpleNamespace(authenticated=False, fingerprint=v), [name], now=when)


def test_finished_days_move_to_postgres_and_leave_redis(engine, clean, fake_redis):
    yesterday = NOW - timedelta(days=1)
    with Session(engine) as s, s.begin():
        s.add(Domain(domain="known-example.com", normalized_domain="known-example.com",
                     registrable_domain="known-example.com", public_suffix="com"))
    visit(fake_redis, "known-example.com", "a", "b", when=yesterday)
    visit(fake_redis, "new-example.com", "a", "a", "a", when=yesterday)
    demand.record_feed(fake_redis, request(), SimpleNamespace(authenticated=False, fingerprint="a"), "all.csv",
                       now=yesterday)
    visit(fake_redis, "today-example.com", "a")  # today is not finished: stays in Redis

    settings = get_settings()
    stats = demand.run(sessionmaker(engine), fake_redis, settings)
    assert stats["days"] == 1 and stats["names"] == 2 and stats["feeds"] == 1
    with Session(engine) as s:
        rows = {r.domain: (r.known, r.lookups, r.callers) for r in s.scalars(select(DemandDaily))}
        assert rows == {"known-example.com": (True, 2, 2), "new-example.com": (False, 3, 1)}
        assert [(f.feed, f.downloads, f.callers) for f in s.scalars(select(FeedDownloadDaily))] == [("all.csv", 1, 1)]
    day = demand.local_day(yesterday, settings)
    assert not fake_redis.exists(f"demand:{day}:n", f"demand:{day}:c:new-example.com", f"demand:{day}:feeds")
    assert counts(fake_redis) == {"today-example.com": 1}
    assert demand.run(sessionmaker(engine), fake_redis, settings)["days"] == 0  # nothing left to do


def test_old_daily_rows_are_deleted(engine, clean, fake_redis):
    settings = get_settings()
    with Session(engine) as s, s.begin():
        for age in (10, settings.demand_keep_days + 1):
            s.add(DemandDaily(day=today() - timedelta(days=age), domain=f"d{age}-example.com", known=False,
                              lookups=1, callers=1))
    demand.run(sessionmaker(engine), fake_redis, settings)
    with Session(engine) as s:
        assert list(s.scalars(select(DemandDaily.domain))) == ["d10-example.com"]


# --------------------------------------------------------------------------- demand moves discovery

def demanded(engine, monkeypatch, cap=100):
    monkeypatch.setattr(get_settings(), "demand_daily_cap", cap)
    day = today() - timedelta(days=1)
    with Session(engine) as s, s.begin():
        for name, people in (("popular-example.com", 5), ("enough-example.com", 3), ("lonely-example.com", 1)):
            s.add(DemandDaily(day=day, domain=name, known=False, lookups=people * 2, callers=people))
        added = demand.add_demanded(s, get_settings(), day)
    return added


def test_unknown_names_several_visitors_wanted_are_added(engine, clean, monkeypatch):
    assert demanded(engine, monkeypatch) == 2
    with Session(engine) as s:
        names = set(s.scalars(select(Domain.normalized_domain).join(DomainSourceRecord).join(DataSource)
                              .where(DataSource.slug == "demand")))
        assert names == {"popular-example.com", "enough-example.com"}  # one visitor is never enough
        assert s.scalar(select(ScanJob.id)) is None  # discovery queues them, under its budget


def test_the_daily_cap_takes_the_most_wanted_first(engine, clean, monkeypatch):
    assert demanded(engine, monkeypatch, cap=1) == 1
    with Session(engine) as s:
        assert list(s.scalars(select(Domain.normalized_domain))) == ["popular-example.com"]


def test_discovery_scans_demand_before_the_lists(engine, clean, monkeypatch):
    from tests.integration.test_discovery import queued, setup_list

    entries = setup_list(engine)
    demanded(engine, monkeypatch)
    settings = Settings(_env_file=None, discovery_daily_budget=10, discovery_batch_size=3, discovery_max_queued=10)
    with Session(engine) as s, s.begin():
        assert queue_discovery(s, settings, entries=entries) == 3
    names = {q[0] for q in queued(engine)}
    assert {"popular-example.com", "enough-example.com"} <= names and len(names) == 3  # then the list's first


def test_demand_alone_is_enough_for_discovery(engine, clean, monkeypatch):
    demanded(engine, monkeypatch)
    settings = Settings(_env_file=None, discovery_daily_budget=10, discovery_batch_size=5, discovery_max_queued=10)
    with Session(engine) as s, s.begin():
        assert queue_discovery(s, settings, entries={}) == 2


# --------------------------------------------------------------------------- the admin tab

def test_the_trends_tab_lists_known_unknown_and_lists(client, admin, engine, clean, make_key):
    with Session(engine) as s, s.begin():
        s.add(Domain(domain="known-example.com", normalized_domain="known-example.com",
                     registrable_domain="known-example.com", public_suffix="com", primary_category_id="news"))
        for age in (1, 2):
            day = today() - timedelta(days=age)
            s.add(DemandDaily(day=day, domain="known-example.com", known=True, lookups=10, callers=4))
            s.add(DemandDaily(day=day, domain="wanted-example.com", known=False, lookups=3, callers=2))
            s.add(FeedDownloadDaily(day=day, feed="security.rpz", downloads=96, callers=2))
        s.add(DemandDaily(day=today() - timedelta(days=60), domain="old-example.com", known=False, lookups=9, callers=9))
    body = client.get("/api/v1/admin/trends?days=30", headers=admin).json()
    assert [(d["domain"], d["callers"], d["lookups"], d["days"], d["primary"]) for d in body["known"]] == [
        ("known-example.com", 8, 20, 2, "news")]
    assert [(d["domain"], d["callers"], d["known"]) for d in body["unknown"]] == [("wanted-example.com", 4, False)]
    assert body["feeds"] == [{"feed": "security.rpz", "downloads": 192, "callers": 4}]
    assert [d["lookups"] for d in body["daily"]] == [13, 13] and body["min_callers"] == 3

    contributor, _ = make_key("contributor")
    assert client.get("/api/v1/admin/trends", headers=contributor).status_code == 403
    assert client.get("/api/v1/admin/trends").status_code == 401


def test_the_public_api_has_no_trends(engine, fake_redis, clean, monkeypatch):
    from fastapi.testclient import TestClient

    from tests.integration.test_hardening import build

    monkeypatch.setattr(get_settings(), "admin_routes", False)
    assert TestClient(build(engine, fake_redis)).get("/api/v1/admin/trends").status_code == 404


def test_only_staff_and_the_site_key_may_state_visitors():
    from backend.app.security import ROLE_SCOPES
    from database.models import Role

    assert "visitors:forward" in ROLE_SCOPES[Role.CONTRIBUTOR] and "visitors:forward" in ROLE_SCOPES[Role.ADMIN]


def test_no_key_states_visitors_unless_it_was_asked_for(engine, clean, make_key, client):
    for role in ("admin", "contributor"):
        headers, _ = make_key(role)  # default scopes
        assert "visitors:forward" not in client.get("/api/v1/admin/me", headers=headers).json()["scopes"]
