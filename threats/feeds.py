"""Third-party threat lists as **candidates** (the semi-dependent phase of THREATS.md).

A list such as Phishing.Database, OpenPhish or URLhaus says a host is malicious. We do not repeat that: their
verdicts often may not be republished, and they are sometimes wrong. Instead:

1. ``sync`` keeps the hosts the list names in ``threat_feed_entries`` (not in ``domains``: hundreds of thousands
   of names would bury the real catalogue), with how closely each relates to Türkiye: 2 = the name resembles a
   watched Turkish brand (integrations/ct_watchlist.yaml), 1 = a .tr name, 0 = anything else.
2. ``pick`` queues a few of them an hour for a scan, most related to Türkiye and newest first, never a popular,
   official or restricted-suffix host (threats/guard.py).
3. The scanned page then goes to the threat-candidate queue like a CT look-alike (reason ``feed_listed:<slug>``,
   classification/engine.py): a person decides, or, once it has proven itself, our own page-proof detector
   (threats/detector.py). Either way the record is ours.

A link the list gives (``url``) is kept for the reviewer only: it can carry a victim's address.
"""
from __future__ import annotations

import logging
import math
from datetime import UTC, datetime

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from common.config import Settings
from common.domains import DomainError, normalize_domain
from database.models import DataSource, Domain, ScanReason, ThreatFeedEntry, ThreatIntelligence

from .guard import protected_owner
from .records import domain_scoped

log = logging.getLogger("category.threats.feeds")
REASON = "feed_listed:"  # + the list's slug: a threat-candidate review (classification/engine.py)
PRIORITY = 3  # threat fast lane, like CT look-alikes
_CHUNK = 5000


def relevance(host: str) -> tuple[int, str | None]:
    from integrations.brand_watch import default_watchlist

    if hit := default_watchlist().match(host):
        return 2, hit.brand.name[:100]
    return (1, None) if host.endswith(".tr") else (0, None)


def sync(session: Session, source: DataSource, rows: list[tuple[int, str]], *, now: datetime | None = None) -> dict:
    """Bring the list's entries up to date. ``rows`` are the list's lines (host names or links)."""
    now = now or datetime.now(UTC)
    hosts: dict[str, str | None] = {}
    invalid = 0
    for _, raw in rows:
        value = raw.strip()
        try:
            host = normalize_domain(value).ascii
        except DomainError:
            invalid += 1  # an IP address, a broken name
            continue
        if host not in hosts or (hosts[host] is None and "://" in value):
            hosts[host] = value[:2000] if "://" in value else None
    names = list(hosts)
    for start in range(0, len(names), _CHUNK):
        values = []
        for host in names[start:start + _CHUNK]:
            score, brand = relevance(host)
            values.append({"source_id": source.id, "host": host, "url": hosts[host], "relevance": score,
                           "brand": brand, "is_listed": True, "first_seen": now, "last_seen": now})
        stmt = insert(ThreatFeedEntry).values(values)
        session.execute(stmt.on_conflict_do_update(
            index_elements=["source_id", "host"],
            set_={"is_listed": True, "last_seen": now, "url": stmt.excluded.url, "relevance": stmt.excluded.relevance,
                  "brand": stmt.excluded.brand}))
    gone = session.execute(update(ThreatFeedEntry).where(
        ThreatFeedEntry.source_id == source.id, ThreatFeedEntry.is_listed, ThreatFeedEntry.last_seen < now)
        .values(is_listed=False)).rowcount or 0
    return {"entries": len(rows), "hosts": len(names), "invalid": invalid, "delisted": gone,
            "related": sum(1 for h in names if h.endswith(".tr"))}


def pick(session: Session, settings: Settings, *, now: datetime | None = None, runs_per_hour: int = 4) -> list[str]:
    """Queue this run's share of ``threat_feed_candidates_per_hour``. Returns the hosts queued."""
    from backend.app.services.domains import add_domains

    want = math.ceil(settings.threat_feed_candidates_per_hour / runs_per_hour)
    if want <= 0:
        return []
    now = now or datetime.now(UTC)
    flagged = select(ThreatIntelligence.id).join(Domain, Domain.id == ThreatIntelligence.domain_id).where(
        Domain.normalized_domain == ThreatFeedEntry.host, ThreatIntelligence.is_active, domain_scoped())
    rows = list(session.scalars(
        select(ThreatFeedEntry).join(DataSource, DataSource.id == ThreatFeedEntry.source_id)
        .where(ThreatFeedEntry.is_listed, ThreatFeedEntry.picked_at.is_(None), DataSource.enabled,
               ThreatFeedEntry.relevance >= settings.threat_feed_min_relevance, ~flagged.exists())
        .order_by(ThreatFeedEntry.relevance.desc(), ThreatFeedEntry.last_seen.desc(), ThreatFeedEntry.id)
        .limit(want * 5).with_for_update(skip_locked=True)))
    queued: list[str] = []
    for entry in rows:
        if len(queued) >= want:
            break
        entry.picked_at = now
        # the same host in another list: picked once
        session.execute(update(ThreatFeedEntry).where(ThreatFeedEntry.host == entry.host, ThreatFeedEntry.id != entry.id)
                        .values(picked_at=now))
        if protected_owner(session, entry.host):  # a hacked page on a popular site is not the site: never queued
            continue
        source = session.get(DataSource, entry.source_id)
        add_domains(session, [(1, entry.host)], source=source, scan="all", reason=ScanReason.DISCOVERY,
                    priority=PRIORITY)
        entry.domain_id = session.scalar(select(Domain.id).where(Domain.normalized_domain == entry.host))
        queued.append(entry.host)
    return queued


def listing(session: Session, host: str) -> list[ThreatFeedEntry]:
    """The lists that name ``host`` now (for the review card and the detector's comparison)."""
    return list(session.scalars(select(ThreatFeedEntry).where(ThreatFeedEntry.host == host, ThreatFeedEntry.is_listed)
                                .order_by(ThreatFeedEntry.relevance.desc(), ThreatFeedEntry.id)))


def picked_reason(session: Session, domain: Domain) -> str | None:
    """``feed_listed:<slug>`` if a list names this domain and we queued it from there, else None."""
    slug = session.scalar(select(DataSource.slug).join(ThreatFeedEntry, ThreatFeedEntry.source_id == DataSource.id)
                          .where(ThreatFeedEntry.host == domain.normalized_domain, ThreatFeedEntry.is_listed,
                                 ThreatFeedEntry.picked_at.is_not(None))
                          .order_by(ThreatFeedEntry.relevance.desc(), ThreatFeedEntry.id).limit(1))
    return f"{REASON}{slug}"[:64] if slug else None
