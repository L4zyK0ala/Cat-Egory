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
from dataclasses import dataclass
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


@dataclass
class Stats:
    messages: int = 0     # received from certstream
    names: int = 0        # scanned
    hits: int = 0         # look-alikes found
    queued: int = 0       # scans queued (after dedupe and the hourly cap)
    dropped: int = 0      # messages dropped because our own queue was full


class Pipeline:
    """Reader → bounded queue → matcher → hits queue → writer. The reader never waits for matching, and matching
    never waits for Redis or the database, so short bursts from certstream are absorbed instead of skipped."""

    def __init__(self, watchlist: Watchlist, store_fn, *, queue_size: int = 50_000, top: TopIndex | None = None):
        self.watchlist, self.store_fn, self.top = watchlist, store_fn, top
        self.messages: asyncio.Queue[str | bytes] = asyncio.Queue(maxsize=queue_size)
        self.hits: asyncio.Queue[tuple[str, str]] = asyncio.Queue(maxsize=10_000)
        self.stats = Stats()

    async def read(self, ws) -> None:
        async for message in ws:
            self.stats.messages += 1
            try:
                self.messages.put_nowait(message)
            except asyncio.QueueFull:
                self.stats.dropped += 1

    async def match(self) -> None:
        handled = 0
        while True:
            message = await self.messages.get()
            try:
                data = json.loads(message)
            except ValueError:
                continue
            names = data.get("data") if isinstance(data, dict) else None
            if isinstance(names, list):
                names = [n for n in names if isinstance(n, str)]
                self.stats.names += len(names)
                for hit in scan(names, self.watchlist, self.top):
                    self.stats.hits += 1
                    try:
                        self.hits.put_nowait(hit)
                    except asyncio.QueueFull:
                        pass  # the hourly cap would refuse these anyway
            handled += 1
            if handled % 200 == 0:
                await asyncio.sleep(0)  # a non-empty queue never suspends: let the reader run

    async def write(self) -> None:
        while True:
            batch = [await self.hits.get()]
            while not self.hits.empty() and len(batch) < 200:
                batch.append(self.hits.get_nowait())
            try:
                self.stats.queued += await asyncio.to_thread(self.store_fn, batch)
            except Exception as exc:  # noqa: BLE001 - keep the pipeline alive; the names may come back later
                log.warning("storing look-alikes failed", extra={"error": type(exc).__name__, "count": len(batch)})


async def run(settings: Settings) -> None:  # pragma: no cover - network loop; Pipeline is tested on its own
    import websockets

    from database.session import session_factory

    client = redis.Redis.from_url(settings.redis_url, socket_timeout=5, socket_connect_timeout=5)
    factory = session_factory()
    pipe = Pipeline(default_watchlist(), lambda hits: store(hits, client, settings, factory))
    tasks = [asyncio.create_task(pipe.match()), asyncio.create_task(pipe.write())]

    async def tick() -> None:
        last, top_at = Stats(), 0.0
        while True:
            if settings.lookalike_top_n and time.monotonic() - top_at > 3600:  # the source syncs weekly
                try:
                    pipe.top = await asyncio.to_thread(_load_top, factory, settings.lookalike_top_n)
                    log.info("top-domain index loaded", extra={"domains": len(pipe.top.domains)})
                except Exception as exc:  # noqa: BLE001
                    log.warning("top-domain index not loaded", extra={"error": type(exc).__name__})
                top_at = time.monotonic()
            await asyncio.sleep(60)
            st = pipe.stats
            delta = {k: getattr(st, k) - getattr(last, k) for k in ("messages", "names", "hits", "queued", "dropped")}
            last = Stats(**vars(st))
            if delta["names"]:
                await asyncio.to_thread(client.hset, HEARTBEAT_KEY, "ct-watch.stream", int(time.time()))
            level = logging.WARNING if delta["dropped"] else logging.INFO
            log.log(level, "stream alive" if delta["names"] else "stream idle",
                    extra={**{f"{k}_last_minute": v for k, v in delta.items()}, "backlog": pipe.messages.qsize()})

    tasks.append(asyncio.create_task(tick()))
    delay = 1.0
    while True:
        try:
            async with websockets.connect(settings.certstream_url, ping_interval=30, ping_timeout=30,
                                          max_size=2**20, max_queue=None) as ws:
                log.info("connected", extra={"url": settings.certstream_url})
                delay = 1.0
                await pipe.read(ws)
        except Exception as exc:  # noqa: BLE001 - reconnect on any error, with backoff
            log.warning("stream error, reconnecting", extra={"error": type(exc).__name__, "in_seconds": round(delay)})
        await asyncio.sleep(delay + random.random())  # noqa: S311 - jitter, not security
        delay = min(delay * 2, 300)


def _load_top(factory, top_n: int) -> TopIndex:
    with factory() as session:
        return load_top_index(session, top_n)


if __name__ == "__main__":  # pragma: no cover
    s = get_settings()
    configure_logging(s.log_level)
    asyncio.run(run(s))
