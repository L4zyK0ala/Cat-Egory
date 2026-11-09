"""Shared plumbing for outside lists and services asked about threat domains (threats/sgb.py, threats/virustotal.py).

Answers go to ``external_listings``, one row per domain and source. They are for reviewers only: nothing here is
ever published, and an answer never creates a threat flag.
"""
from __future__ import annotations

import logging
import time
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

import httpx
from sqlalchemy import exists, or_, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from classification.engine import threat_candidate
from database.models import Domain, ExternalListing, ReviewStatus, ReviewTask, ThreatIntelligence

from .records import domain_scoped

log = logging.getLogger("category.threats.listings")

Fetch = Callable[[str], list[dict]]


def due(session: Session, source: str, *, max_age_hours: int, limit: int) -> list[Domain]:
    """Domains worth asking about: an active domain-scoped threat record (any source) or an open
    threat-candidate review; not checked by ``source`` within ``max_age_hours``. Never checked first."""
    has_threat = exists(select(ThreatIntelligence.id).where(ThreatIntelligence.domain_id == Domain.id,
                                                           ThreatIntelligence.is_active, domain_scoped()))
    candidate = exists(select(ReviewTask.id).where(ReviewTask.domain_id == Domain.id,
                                                   ReviewTask.status == ReviewStatus.OPEN, threat_candidate()))
    stale = datetime.now(UTC) - timedelta(hours=max_age_hours)
    checked = (select(ExternalListing.domain_id, ExternalListing.checked_at)
               .where(ExternalListing.source == source).subquery())
    return list(session.scalars(
        select(Domain).outerjoin(checked, checked.c.domain_id == Domain.id)
        .where(Domain.is_active, or_(has_threat, candidate),
               or_(checked.c.checked_at.is_(None), checked.c.checked_at < stale))
        .order_by(checked.c.checked_at.asc().nulls_first(), Domain.id).limit(limit)))


def save(session: Session, source: str, domain_id: int, *, listed: bool, matches: list[dict]) -> None:
    stmt = insert(ExternalListing).values(domain_id=domain_id, source=source, listed=listed, matches=matches,
                                          checked_at=datetime.now(UTC))
    session.execute(stmt.on_conflict_do_update(
        index_elements=["domain_id", "source"],
        set_={"listed": stmt.excluded.listed, "matches": stmt.excluded.matches, "checked_at": stmt.excluded.checked_at}))


def latest(session: Session, source: str, domain_ids: set[int]) -> dict[int, ExternalListing]:
    if not domain_ids:
        return {}
    return {r.domain_id: r for r in session.scalars(select(ExternalListing).where(
        ExternalListing.source == source, ExternalListing.domain_id.in_(domain_ids)))}


def check(session: Session, source: str, fetch: Fetch, *, is_listed: Callable[[list[dict]], bool], limit: int,
          max_age_hours: int, pause: float, sleep: Callable[[float], None] = time.sleep, commit_each: bool = False,
          progress: Callable[[str, list[dict]], None] | None = None) -> dict:
    """Ask about due domains. Stops at the first network or API error, a used-up quota included (the rest waits
    for the next run). ``commit_each``: write every answer at once (nothing is held while waiting for the API)."""
    counts = {"checked": 0, "listed": 0, "errors": 0}
    domains = [(d.id, d.normalized_domain) for d in due(session, source, max_age_hours=max_age_hours, limit=limit)]
    if commit_each:
        session.commit()
    for i, (domain_id, name) in enumerate(domains):
        if i:
            sleep(pause)
        try:
            matches = fetch(name)
            listed = is_listed(matches)
        except (httpx.HTTPError, ValueError, TypeError, AttributeError) as exc:  # the last two: a malformed answer
            status = exc.response.status_code if isinstance(exc, httpx.HTTPStatusError) else None
            log.warning("external lookup failed", extra={"source": source, "domain": name,
                                                         "error": type(exc).__name__, "status": status})
            counts["errors"] += 1
            break
        save(session, source, domain_id, listed=listed, matches=matches)
        if commit_each:
            session.commit()
        counts["checked"] += 1
        counts["listed"] += listed
        if progress:
            progress(name, matches)
    return counts
