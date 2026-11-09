"""Keeping confirmed threats honest over time, using the scans that already run for them.

Applies to our own domain-scoped records (a reviewer's or Octryx's). A third-party feed's records are the feed's business, and one
page on a live site cannot be judged from a scan of its home page.

* The site is up: ``last_seen`` moves on.
* The site is gone (no DNS, no connection, 404/410) and has not been seen for ``threat_expire_days``: the record is
  deactivated, so it leaves the public pages and the block lists. It is kept, marked ``raw.expired_at``.
* An expired site comes back: a person decides. Nothing is flagged again automatically, because the name may have a
  new, innocent owner.

Anything else says nothing about the site (timeouts, robots.txt, our own failures, 403 and 5xx pages) and changes
nothing. A cleaned page that stays online is not detected here; a reviewer withdraws that flag.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from classification.engine import OCTRYX_SOURCE, OWN_THREAT_SOURCES, THREAT_RETURNED, open_review
from crawler.result import CrawlResult
from database.models import AIPrediction, DataSource, Domain, ThreatIntelligence

from .records import domain_scoped

DEAD_ERRORS = frozenset({"dns_nxdomain", "dns_no_address", "connection_failed"})
GONE_STATUSES = frozenset({404, 410})


def ai_answer_needed(session: Session, domain_id: int, *, every_days: int, now: datetime) -> bool:
    """A domain with a confirmed threat is scanned daily to see whether it is still there. That does not need a
    model each time: with an active domain-scoped threat and a valid answer within ``every_days``, skip the call."""
    flagged = session.scalar(select(ThreatIntelligence.id).where(
        ThreatIntelligence.domain_id == domain_id, ThreatIntelligence.is_active, domain_scoped()).limit(1))
    if flagged is None:
        return True
    automatic = session.scalar(
        select(ThreatIntelligence.id).join(DataSource, DataSource.id == ThreatIntelligence.source_id)
        .where(ThreatIntelligence.domain_id == domain_id, ThreatIntelligence.is_active, domain_scoped(),
               DataSource.slug == OCTRYX_SOURCE).limit(1))
    if automatic is not None:
        return False  # a copy of a bank's login page would come back as "banking"; the flag says what it is
    recent = session.scalar(select(AIPrediction.id).where(
        AIPrediction.domain_id == domain_id, AIPrediction.primary_category_id.is_not(None),
        AIPrediction.created_at >= now - timedelta(days=every_days)).limit(1))
    return recent is None


@dataclass(frozen=True)
class Change:
    action: str  # "expire" or "return"
    types: list[str]
    last_seen: str | None = None


def observe(result: CrawlResult) -> str | None:
    """"alive", "dead", or None when this scan says nothing about the site."""
    if result.error_code in DEAD_ERRORS:
        return "dead"
    if not result.ok or result.http_status is None:
        return None
    if result.http_status in GONE_STATUSES:
        return "dead"
    return "alive" if result.http_status < 400 else None


def after_scan(session: Session, domain: Domain, result: CrawlResult, *, expire_days: int,
               now: datetime) -> Change | None:
    state = observe(result)
    if state is None:
        return None
    rows = list(session.scalars(
        select(ThreatIntelligence).join(DataSource, DataSource.id == ThreatIntelligence.source_id)
        .where(ThreatIntelligence.domain_id == domain.id, DataSource.slug.in_(OWN_THREAT_SOURCES),
               domain_scoped())))
    active = [r for r in rows if r.is_active]
    if state == "alive":
        for row in active:
            row.last_seen = now
        back = [r for r in rows if not r.is_active and (r.raw or {}).get("expired_at")]
        if not back:
            return None
        for row in back:
            row.raw = {k: v for k, v in row.raw.items() if k != "expired_at"} | {"returned_at": now.isoformat()}
        open_review(session, domain, None, THREAT_RETURNED)
        return Change("return", sorted(str(r.threat_type) for r in back))
    limit = now - timedelta(days=expire_days)
    stale = [r for r in active if r.last_seen <= limit]
    if not stale:
        return None
    for row in stale:
        row.is_active, row.raw = False, (row.raw or {}) | {"expired_at": now.isoformat()}
    return Change("expire", sorted(str(r.threat_type) for r in stale), min(r.last_seen for r in stale).isoformat())
