"""Demand: which domains visitors look up and which lists they download.

Counted as **daily totals with no link to who asked**: a counter per registrable domain and per feed, and for each
a HyperLogLog sketch of distinct callers in Redis (a sketch of hashed values that can count them approximately but
cannot list them; the values are the existing keyed IP fingerprint, or the API key id). A nightly task moves each
finished day into Postgres (``demand_daily``, ``feed_downloads_daily``) and deletes the Redis keys.

* Only valid registrable public domains are counted (``common/domains.py``); IPs, private names and garbage are not.
* A day counts at most ``demand_max_names_per_day`` distinct names, so random look-ups cannot fill Redis.
* Site pages are rendered on the server with one service key; that key may state the visitor's address
  (``X-Category-Visitor``, scope ``visitors:forward``) so site visitors are told apart. It is hashed like any IP and
  used for nothing else. Without the scope, every request with a key counts as that key.
* ``X-Category-Demand: skip`` marks a secondary request of the same visit (the site's fallbacks), not counted twice.

Unknown domains looked up by at least ``demand_min_callers`` distinct callers on a day are added with the ``demand``
data source (at most ``demand_daily_cap`` a day); discovery scans them before the popularity lists. One firewall or
one script is one caller, so it can never push a name in.
"""
from __future__ import annotations

import ipaddress
import logging
from collections.abc import Iterable
from datetime import UTC, date, datetime, timedelta

import redis
from fastapi import Request
from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from common.config import Settings, get_settings
from common.domains import DomainError, normalize_domain, normalize_url
from database.models import DemandDaily, Domain, FeedDownloadDaily

from .security import fingerprint

log = logging.getLogger("category.demand")

DEMAND_SOURCE = "demand"
FORWARD_SCOPE = "visitors:forward"
VISITOR_HEADER = "x-category-visitor"
SKIP_HEADER = "x-category-demand"
TTL = 4 * 86_400  # a day not rolled up is dropped after this (the task normally runs every night)
CHUNK = 500


def local_day(now: datetime, settings: Settings) -> date:
    return (now + timedelta(hours=settings.app_utc_offset_hours)).date()


def _keys(day: date | str) -> tuple[str, str]:
    return f"demand:{day}:n", f"demand:{day}:feeds"


def caller(request: Request, principal, settings: Settings) -> str | None:
    """Who is asking, as an opaque value for the distinct-caller sketch; None = not known."""
    if principal.authenticated:
        if FORWARD_SCOPE in principal.scopes:
            raw = request.headers.get(VISITOR_HEADER, "").strip()
            try:
                ip = ipaddress.ip_address(raw)
            except ValueError:
                ip = None
            if ip is not None:
                value = str(ipaddress.ip_network(f"{ip}/64", strict=False).network_address) if ip.version == 6 else str(ip)
                secret = settings.fingerprint_secret.get_secret_value() if settings.fingerprint_secret else None
                return fingerprint(secret, value)
        return f"key:{principal.api_key_id}"
    return principal.fingerprint


def registrable(raw: str) -> str | None:
    value = raw.strip()[:300]
    try:
        return (normalize_url(value).domain if "/" in value or ":" in value else normalize_domain(value)).registrable
    except DomainError:
        return None


def _skip(request: Request, settings: Settings) -> bool:
    return not settings.demand_counting or request.headers.get(SKIP_HEADER, "").strip().lower() == "skip"


def record_lookups(client: redis.Redis, request: Request, principal, names: Iterable[str],
                   now: datetime | None = None) -> int:
    """Count look-ups of these names (domains or URLs). Never raises: counting must not break a look-up."""
    settings = get_settings()
    if _skip(request, settings):
        return 0
    try:
        domains = sorted({d for d in (registrable(n) for n in names) if d})
        if not domains:
            return 0
        day = local_day(now or datetime.now(UTC), settings)
        key, _ = _keys(day)
        who = caller(request, principal, settings)
        pipe = client.pipeline(transaction=False)
        pipe.hmget(key, domains)
        pipe.hlen(key)
        seen, size = pipe.execute()
        room = settings.demand_max_names_per_day - int(size)
        pipe = client.pipeline(transaction=False)
        counted = 0
        for domain, before in zip(domains, seen, strict=True):
            if before is None:
                if room <= 0:
                    continue  # today's list of names is full: known names still count, new ones do not
                room -= 1
            pipe.hincrby(key, domain, 1)
            if who:
                pipe.pfadd(f"demand:{day}:c:{domain}", who)
                pipe.expire(f"demand:{day}:c:{domain}", TTL)
            counted += 1
        pipe.expire(key, TTL)
        pipe.execute()
        return counted
    except redis.RedisError as exc:
        log.warning("demand not counted", extra={"error": type(exc).__name__})
        return 0


def record_feed(client: redis.Redis, request: Request, principal, feed: str, now: datetime | None = None) -> None:
    settings = get_settings()
    if _skip(request, settings):
        return
    try:
        day = local_day(now or datetime.now(UTC), settings)
        _, key = _keys(day)
        who = caller(request, principal, settings)
        pipe = client.pipeline(transaction=False)
        pipe.hincrby(key, feed, 1)
        pipe.expire(key, TTL)
        if who:
            pipe.pfadd(f"demand:{day}:fc:{feed}", who)
            pipe.expire(f"demand:{day}:fc:{feed}", TTL)
        pipe.execute()
    except redis.RedisError as exc:
        log.warning("feed download not counted", extra={"error": type(exc).__name__})


# --------------------------------------------------------------------------- nightly roll-up


def _text(value) -> str:
    return value.decode() if isinstance(value, bytes) else str(value)


def finished_days(client: redis.Redis, settings: Settings, now: datetime) -> list[date]:
    today = local_day(now, settings)
    days = set()
    for pattern in ("demand:*:n", "demand:*:feeds"):
        for key in client.scan_iter(match=pattern, count=1000):
            try:
                day = date.fromisoformat(_text(key).split(":")[1])
            except ValueError:
                continue
            if day < today:
                days.add(day)
    return sorted(days)


def rollup(session: Session, client: redis.Redis, day: date) -> tuple[dict[str, int], list[str]]:
    """Write one finished day's totals to Postgres. Returns the stats and the Redis keys to delete once committed.
    Re-running a day sets the same values (idempotent)."""
    names_key, feeds_key = _keys(day)
    counts = {_text(k): int(v) for k, v in client.hgetall(names_key).items()}
    done = [names_key, feeds_key]
    names = sorted(counts)
    for start in range(0, len(names), CHUNK):
        chunk = names[start:start + CHUNK]
        pipe = client.pipeline(transaction=False)
        for name in chunk:
            pipe.pfcount(f"demand:{day}:c:{name}")
        callers = pipe.execute()
        known = set(session.scalars(select(Domain.normalized_domain).where(Domain.normalized_domain.in_(chunk))))
        stmt = insert(DemandDaily).values([{"day": day, "domain": n, "known": n in known, "lookups": counts[n],
                                            "callers": int(c)} for n, c in zip(chunk, callers, strict=True)])
        session.execute(stmt.on_conflict_do_update(index_elements=["day", "domain"], set_={
            "known": stmt.excluded.known, "lookups": stmt.excluded.lookups, "callers": stmt.excluded.callers}))
        done += [f"demand:{day}:c:{n}" for n in chunk]
    feeds = {_text(k): int(v) for k, v in client.hgetall(feeds_key).items()}
    if feeds:
        pipe = client.pipeline(transaction=False)
        for feed in feeds:
            pipe.pfcount(f"demand:{day}:fc:{feed}")
        callers = pipe.execute()
        stmt = insert(FeedDownloadDaily).values([{"day": day, "feed": f, "downloads": n, "callers": int(c)}
                                                 for (f, n), c in zip(feeds.items(), callers, strict=True)])
        session.execute(stmt.on_conflict_do_update(index_elements=["day", "feed"], set_={
            "downloads": stmt.excluded.downloads, "callers": stmt.excluded.callers}))
        done += [f"demand:{day}:fc:{f}" for f in feeds]
    return {"names": len(names), "feeds": len(feeds)}, done


def add_demanded(session: Session, settings: Settings, day: date) -> int:
    """Unknown domains that enough distinct visitors looked up on ``day`` join discovery (``demand`` source)."""
    from .services.domains import add_domains, get_source

    if settings.demand_daily_cap <= 0:
        return 0
    source = get_source(session, DEMAND_SOURCE)
    if source is None:
        return 0
    names = list(session.scalars(
        select(DemandDaily.domain).where(DemandDaily.day == day, DemandDaily.known.is_(False),
                                         DemandDaily.callers >= settings.demand_min_callers)
        .order_by(DemandDaily.callers.desc(), DemandDaily.lookups.desc(), DemandDaily.domain)
        .limit(settings.demand_daily_cap)))
    if not names:
        return 0
    return add_domains(session, list(enumerate(names, start=1)), source=source, scan="none").created


def forget(client: redis.Redis, keys: list[str]) -> None:
    for start in range(0, len(keys), CHUNK):
        client.delete(*keys[start:start + CHUNK])


def run(session_maker, client: redis.Redis, settings: Settings, now: datetime | None = None) -> dict[str, int]:
    """Roll up every finished day still in Redis, add demanded domains, drop old daily rows."""
    now = now or datetime.now(UTC)
    stats = {"days": 0, "names": 0, "feeds": 0, "added": 0}
    for day in finished_days(client, settings, now):
        with session_maker() as session, session.begin():
            day_stats, keys = rollup(session, client, day)
            stats["added"] += add_demanded(session, settings, day)
        forget(client, keys)  # only after the totals are committed
        stats["days"] += 1
        stats["names"] += day_stats["names"]
        stats["feeds"] += day_stats["feeds"]
    with session_maker() as session, session.begin():
        session.execute(delete(DemandDaily).where(
            DemandDaily.day < local_day(now, settings) - timedelta(days=settings.demand_keep_days)))
    return stats
