"""Third-party threat lists as **candidates** (the semi-dependent phase of THREATS.md).

A list such as Phishing.Database, OpenPhish or URLhaus says a host is malicious. We do not repeat that: their
verdicts often may not be republished, and they are sometimes wrong. Instead:

1. ``sync`` keeps the hosts the list names in ``threat_feed_entries`` (not in ``domains``: hundreds of thousands
   of names would bury the real catalogue), with how closely each relates to Türkiye: 2 = the name resembles a
   watched Turkish brand (integrations/ct_watchlist.yaml), 1 = a .tr name, 0 = anything else (``brand`` then names
   a watched worldwide brand the host resembles, integrations/global_brands.yaml, if any).
2. ``pick`` queues a few of them an hour for a scan, never a popular, official or restricted-suffix host
   (threats/guard.py): ``threat_feed_candidates_per_hour`` Türkiye-related ones (most related first), and
   ``threat_feed_global_per_hour`` worldwide phishing ones — resembling a global brand, or with a link — newest
   first, so their pages are still up when we look. Where a phishing list gives the page's link, that one page is
   scanned (threats/pages.py), not the home page.
3. A Türkiye-related candidate goes to the threat-candidate queue like a CT look-alike (reason
   ``feed_listed:<slug>``, classification/engine.py); a worldwide one only when its page asks for a secret or a
   phone number (threats/pages.py). A person decides, or, once it has proven itself, our own page-proof detector
   (threats/detector.py). Either way the record is ours.

A link the list gives (``url``) is kept for the reviewer and for the one-page scan, never published: it can carry a
victim's address (the scan drops the query string).
"""
from __future__ import annotations

import logging
import math
from datetime import UTC, datetime

from sqlalchemy import or_, select, update
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
    """(how Türkiye-related, the watched brand the name resembles): a Turkish brand 2, a .tr name 1, else 0 with a
    worldwide brand if the name resembles one."""
    from integrations.brand_watch import default_watchlist, global_watchlist

    if hit := default_watchlist().match(host):
        return 2, hit.brand.name[:100]
    if host.endswith(".tr"):
        return 1, None
    hit = global_watchlist().match(host)
    return 0, hit.brand.name[:100] if hit else None


def sync(session: Session, source: DataSource, rows: list[tuple[int, str]], *, now: datetime | None = None,
         watched_only: bool = False) -> dict:
    """Bring the list's entries up to date. ``rows`` are the list's lines (host names or links). ``watched_only``
    (``watched_only: true`` in sources.yaml) keeps only hosts related to Türkiye or resembling a watched worldwide
    brand: for very long lists, whose other names we would never scan."""
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
    kept = 0
    for start in range(0, len(names), _CHUNK):
        values = []
        for host in names[start:start + _CHUNK]:
            score, brand = relevance(host)
            if watched_only and score == 0 and brand is None:
                continue
            values.append({"source_id": source.id, "host": host, "url": hosts[host], "relevance": score,
                           "brand": brand, "is_listed": True, "first_seen": now, "last_seen": now})
        if not values:
            continue
        kept += len(values)
        stmt = insert(ThreatFeedEntry).values(values)
        session.execute(stmt.on_conflict_do_update(
            index_elements=["source_id", "host"],
            set_={"is_listed": True, "last_seen": now, "url": stmt.excluded.url, "relevance": stmt.excluded.relevance,
                  "brand": stmt.excluded.brand}))
    gone = session.execute(update(ThreatFeedEntry).where(
        ThreatFeedEntry.source_id == source.id, ThreatFeedEntry.is_listed, ThreatFeedEntry.last_seen < now)
        .values(is_listed=False)).rowcount or 0
    return {"entries": len(rows), "hosts": kept, "invalid": invalid, "delisted": gone,
            "related": sum(1 for h in names if h.endswith(".tr"))}


def phishing_slugs() -> list[str]:
    """Candidate lists of phishing (not malware: a malware link is a download, never opened by our crawler)."""
    from integrations.engine import catalogue

    return [slug for slug, e in catalogue().items() if e.get("candidates_only") and e.get("threat_type") == "phishing"]


def pick(session: Session, settings: Settings, *, now: datetime | None = None, runs_per_hour: int = 4) -> list[str]:
    """Queue this run's share of ``threat_feed_candidates_per_hour`` (Türkiye-related) and of
    ``threat_feed_global_per_hour`` (worldwide phishing). Returns the hosts queued."""
    now = now or datetime.now(UTC)
    flagged = select(ThreatIntelligence.id).join(Domain, Domain.id == ThreatIntelligence.domain_id).where(
        Domain.normalized_domain == ThreatFeedEntry.host, ThreatIntelligence.is_active, domain_scoped())
    base = (select(ThreatFeedEntry).join(DataSource, DataSource.id == ThreatFeedEntry.source_id)
            .where(ThreatFeedEntry.is_listed, ThreatFeedEntry.picked_at.is_(None), DataSource.enabled,
                   ~flagged.exists()))
    queued: list[str] = []
    want = math.ceil(settings.threat_feed_candidates_per_hour / runs_per_hour)
    if want > 0:
        queued += _queue(session, settings, now, want, base.where(
            ThreatFeedEntry.relevance >= max(settings.threat_feed_min_relevance, 1))
            .order_by(ThreatFeedEntry.relevance.desc(), ThreatFeedEntry.last_seen.desc(), ThreatFeedEntry.id))
    want = math.ceil(settings.threat_feed_global_per_hour / runs_per_hour)
    if want > 0:
        queued += _queue(session, settings, now, want, base.where(
            ThreatFeedEntry.relevance == 0, DataSource.slug.in_(phishing_slugs()),
            or_(ThreatFeedEntry.brand.is_not(None), ThreatFeedEntry.url.is_not(None)))
            .order_by(ThreatFeedEntry.first_seen.desc(), ThreatFeedEntry.id.desc()))
    return queued


def _queue(session: Session, settings: Settings, now: datetime, want: int, stmt) -> list[str]:
    from backend.app.services.domains import add_domains

    from .pages import queue_page

    pages = set(phishing_slugs()) if settings.threat_page_scans else set()
    queued: list[str] = []
    for entry in session.scalars(stmt.limit(want * 5).with_for_update(skip_locked=True)):
        if len(queued) >= want:
            break
        entry.picked_at = now
        # the same host in another list: picked once
        session.execute(update(ThreatFeedEntry).where(ThreatFeedEntry.host == entry.host, ThreatFeedEntry.id != entry.id)
                        .values(picked_at=now))
        if protected_owner(session, entry.host):  # a hacked page on a popular site is not the site: never queued
            continue
        source = session.get(DataSource, entry.source_id)
        page = entry.url if source.slug in pages else None
        add_domains(session, [(1, entry.host)], source=source, scan="none" if page else "all",
                    reason=ScanReason.DISCOVERY, priority=PRIORITY)
        domain = session.scalar(select(Domain).where(Domain.normalized_domain == entry.host))
        entry.domain_id = domain.id if domain else None
        if page and domain is not None and not queue_page(session, domain, page, priority=PRIORITY):
            from backend.app.services.domains import queue_scans  # the link is the home page, or unusable

            queue_scans(session, [domain.id], reason=ScanReason.DISCOVERY, priority=PRIORITY, requested_by_id=None)
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
