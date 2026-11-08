"""Certificate Transparency brand watch: new certificates → look-alike host names → high-priority scans.

Reads the domains-only stream of a certstream server (``CATEGORY_CERTSTREAM_URL``), matches every name against
integrations/ct_watchlist.yaml (integrations/brand_watch.py) and against homographs/typos of the most popular
domains (integrations/lookalike.py), and adds matches as domains from the source
``ct-brand-watch``. Their scans run at priority 3 and their classification always goes to a person
(classification/engine.py). Bounded on purpose: each name once per 30 days, at most
``CATEGORY_CT_WATCH_MAX_PER_HOUR`` per hour, so a burst of certificates cannot flood the crawler or the AI quota.

    python -m integrations.ct_watch          # the `ct-watch` container runs this
"""
from __future__ import annotations

import asyncio
import json
import logging
import random
import time
from datetime import UTC, datetime

import redis
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from classification.engine import BRAND_WATCH_SOURCE
from common.config import Settings, get_settings
from common.logging import configure_logging
from database.models import DataSource, Domain, DomainSourceRecord, ScanReason

from .brand_watch import Watchlist, default_watchlist
from .lookalike import TopIndex, load_top_index

log = logging.getLogger("category.ct_watch")
SEEN_SECONDS = 30 * 86400
PRIORITY = 3
HEARTBEAT_KEY = "category:task-heartbeat"  # same hash as the Celery heartbeats (backend/app/worker.py)


def admit(client: redis.Redis, name: str, settings: Settings, now: datetime | None = None) -> bool:
    """True if this name should be queued now: not seen in 30 days and the hourly budget is not used up."""
    if not client.set(f"ct:seen:{name}", 1, nx=True, ex=SEEN_SECONDS):
        return False
    hour = (now or datetime.now(UTC)).strftime("%Y%m%d%H")
    count = client.incr(f"ct:hour:{hour}")
    client.expire(f"ct:hour:{hour}", 7200)
    if count > settings.ct_watch_max_per_hour:
        client.delete(f"ct:seen:{name}")  # not taken: a later certificate for the same name may still get in
        return False
    return True


def ingest(session: Session, name: str, label: str) -> int:
    """Add the matched name (and queue its scan). Returns the number of scans queued (0 or 1)."""
    from backend.app.services.domains import add_domains

    source = session.scalar(select(DataSource).where(DataSource.slug == BRAND_WATCH_SOURCE))
    if source is None:
        raise RuntimeError(f"data source {BRAND_WATCH_SOURCE} missing: run the seed (migrate container)")
    result = add_domains(session, [(1, name)], source=source, scan="all", reason=ScanReason.DISCOVERY,
                         priority=PRIORITY)
    session.execute(update(DomainSourceRecord)
                    .where(DomainSourceRecord.source_id == source.id,
                           DomainSourceRecord.domain_id == select(Domain.id)
                           .where(Domain.normalized_domain == name).scalar_subquery())
                    .values(source_category=f"look-alike: {label}"[:200]))
    log.info("look-alike queued", extra={"domain": name, "of": label})
    return result.scans_queued


def scan(names: list[str], watchlist: Watchlist, top: TopIndex | None = None) -> list[tuple[str, str]]:
    """(name, label) for every look-alike: Turkish brands first (integrations/ct_watchlist.yaml), then homographs and
    typos of the most popular domains. Pure CPU and fast (pre-filters), so it runs inside the stream loop."""
    hits = []
    for raw in names:
        if match := watchlist.match(raw):
            hits.append((match.name, match.brand.label))
        elif top is not None and (hit := top.check(raw)):
            hits.append((hit.name, f"{hit.target} ({hit.kind})"))
    return hits


def store(hits: list[tuple[str, str]], client: redis.Redis, settings: Settings, session_factory) -> int:
    """Admit (dedupe, hourly cap) and add the hits. Database and Redis I/O: runs in a worker thread."""
    queued = 0
    for name, label in hits:
        if not admit(client, name, settings):
            continue
        with session_factory() as session, session.begin():
            queued += ingest(session, name, label)
    return queued


def handle(names: list[str], watchlist: Watchlist, client: redis.Redis, settings: Settings, session_factory,
           top: TopIndex | None = None) -> int:
    return store(scan(names, watchlist, top), client, settings, session_factory)


async def run(settings: Settings) -> None:  # pragma: no cover - network loop, exercised in the live stack
    import websockets

    from database.session import session_factory

    watchlist = default_watchlist()
    client = redis.Redis.from_url(settings.redis_url, socket_timeout=5, socket_connect_timeout=5)
    factory = session_factory()
    top, top_at = TopIndex(), 0.0
    delay = 1.0
    while True:
        try:
            async with websockets.connect(settings.certstream_url, ping_interval=30, ping_timeout=30,
                                          max_size=2**20) as ws:
                log.info("connected", extra={"url": settings.certstream_url})
                delay, seen, beat = 1.0, 0, time.monotonic()
                async for message in ws:
                    data = json.loads(message)
                    names = data.get("data") if isinstance(data, dict) else None
                    if settings.lookalike_top_n and time.monotonic() - top_at > 3600:  # source syncs weekly
                        with factory() as session:
                            top = await asyncio.to_thread(load_top_index, session, settings.lookalike_top_n)
                        top_at = time.monotonic()
                        log.info("top-domain index loaded", extra={"domains": len(top.domains)})
                    if isinstance(names, list):
                        seen += len(names)
                        # matching stays in the loop (a thread hop per message could not keep up with ~1000 msg/s);
                        # only the rare hits go to a thread for Redis/database writes
                        if hits := scan([n for n in names if isinstance(n, str)], watchlist, top):
                            await asyncio.to_thread(store, hits, client, settings, factory)
                    if time.monotonic() - beat > 60:
                        client.hset(HEARTBEAT_KEY, "ct-watch.stream", int(time.time()))
                        log.info("stream alive", extra={"names_last_minute": seen})
                        seen, beat = 0, time.monotonic()
        except Exception as exc:  # noqa: BLE001 - reconnect on any error, with backoff
            log.warning("stream error, reconnecting", extra={"error": type(exc).__name__, "in_seconds": round(delay)})
            await asyncio.sleep(delay + random.random())  # noqa: S311 - jitter, not security
            delay = min(delay * 2, 300)


if __name__ == "__main__":  # pragma: no cover
    s = get_settings()
    configure_logging(s.log_level)
    asyncio.run(run(s))
