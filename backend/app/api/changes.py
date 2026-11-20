"""What changed: a pull feed of category and threat changes, and line diffs between feed versions.

``GET /api/v1/changes?since=<time>`` returns events after ``since`` (oldest first, at most ``limit``) and ``until``,
the time to pass as ``since`` next. Events: a published domain's main category changed (``category``), a threat was
flagged (``threat_added``) or taken back (``threat_removed``, with ``reason`` withdrawn or expired). Only what the
public API shows anyway: published domains, threat records from licences that allow redistribution, never URLs.

``GET /api/v1/feeds/{name}/diff?from=<version>`` returns the lines added (``+``) and removed (``-``) since that
version, so a filter can apply a change instead of reloading a whole list.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Literal

from fastapi import APIRouter, Depends, Query, Response
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from database.models import DataSource, Domain, DomainCategory, FeedRelease, SourceLicense, ThreatIntelligence
from database.publication import publishable, published
from database.session import get_session
from feeds.builder import FEED_NAME_RE, THREAT_SOURCES
from threats.records import domain_scoped

from ..deps import rate_limit
from ..errors import ApiError
from .feeds import get_feed_store

router = APIRouter(prefix="/api/v1", tags=["changes"], dependencies=[Depends(rate_limit("read"))])
MAX_DAYS = 30


class ChangeOut(BaseModel):
    at: datetime
    domain: str
    kind: Literal["category", "threat_added", "threat_removed"]
    old: str | None = None  # previous main category
    new: str | None = None  # new main category, or the threat type
    reason: Literal["withdrawn", "expired"] | None = None


class ChangesOut(BaseModel):
    since: datetime
    until: datetime
    more: bool
    items: list[ChangeOut]


def changes(session: Session, since: datetime, limit: int, *, newest: bool = False) -> tuple[list[ChangeOut], bool]:
    """Events after ``since``, oldest first and cut so the next call can continue (``newest``: the latest
    ``limit`` events, newest first, for showing; not a cursor)."""
    previous = func.lag(DomainCategory.category_id).over(partition_by=DomainCategory.domain_id,
                                                         order_by=DomainCategory.valid_from)
    primaries = (select(DomainCategory.domain_id, DomainCategory.category_id, DomainCategory.valid_from,
                        previous.label("old"))
                 .where(DomainCategory.is_primary).subquery())
    category_rows = session.execute(
        select(primaries.c.valid_from, Domain.normalized_domain, primaries.c.old, primaries.c.category_id)
        .join(Domain, Domain.id == primaries.c.domain_id)
        .where(primaries.c.valid_from > since, published(),
               primaries.c.old.is_distinct_from(primaries.c.category_id))
        .order_by(primaries.c.valid_from.desc() if newest else primaries.c.valid_from).limit(limit + 1)).all()
    licensed = (select(ThreatIntelligence.first_seen, ThreatIntelligence.deactivated_at, ThreatIntelligence.threat_type,
                       ThreatIntelligence.raw, Domain.normalized_domain)
                .join(Domain, Domain.id == ThreatIntelligence.domain_id)
                .join(DataSource, DataSource.id == ThreatIntelligence.source_id)
                .join(SourceLicense, SourceLicense.id == DataSource.license_id)
                .where(SourceLicense.redistribution_allowed, DataSource.slug.in_(THREAT_SOURCES), domain_scoped(),
                       Domain.is_active, publishable()))  # the same records the threat feeds list
    first, gone = ThreatIntelligence.first_seen, ThreatIntelligence.deactivated_at
    added = session.execute(licensed.where(first > since)
                            .order_by(first.desc() if newest else first).limit(limit + 1)).all()
    removed = session.execute(licensed.where(gone > since)
                              .order_by(gone.desc() if newest else gone).limit(limit + 1)).all()
    items = ([ChangeOut(at=at, domain=d, kind="category", old=old, new=new) for at, d, old, new in category_rows]
             + [ChangeOut(at=r.first_seen, domain=r.normalized_domain, kind="threat_added", new=str(r.threat_type))
                for r in added]
             + [ChangeOut(at=r.deactivated_at, domain=r.normalized_domain, kind="threat_removed", new=str(r.threat_type),
                          reason="expired" if (r.raw or {}).get("expired_at") else "withdrawn") for r in removed])
    items.sort(key=lambda c: (c.at, c.domain, c.kind), reverse=newest)
    if newest:
        return items[:limit], len(items) > limit
    if len(items) <= limit:
        return items, False
    # Cut between two times, never inside one: the next call asks for "after until", so events sharing the last
    # time must all be in this page. (Only more than ``limit`` changes in the same instant still lose some.)
    boundary = items[limit].at
    page = [c for c in items[:limit] if c.at < boundary]
    return page or items[:limit], True


@router.get("/changes", response_model=ChangesOut, summary="Category and threat changes since a time (oldest first)")
def list_changes(response: Response, since: datetime | None = None, limit: int = Query(500, ge=1, le=1000),
                 newest: bool = Query(False, description="the latest `limit` events, newest first (for showing; "
                                                         "to follow changes, keep the default and pass `until` on)"),
                 session: Session = Depends(get_session)) -> ChangesOut:
    now = datetime.now(UTC)
    since = since or now - timedelta(days=1)
    if since.tzinfo is None:
        since = since.replace(tzinfo=UTC)
    if since < now - timedelta(days=MAX_DAYS):
        raise ApiError(422, "since_too_old", f"at most {MAX_DAYS} days back; download the full feed instead")
    items, more = changes(session, since, limit, newest=newest)
    response.headers["Cache-Control"] = "public, max-age=60"
    last = (items[0] if newest else items[-1]).at if items else since
    return ChangesOut(since=since, until=last, more=more, items=items)


def _domains(name: str, body: bytes) -> set[str]:
    """The domains listed in a feed file, whatever its format."""
    ext = name.rsplit(".", 1)[-1]
    out: set[str] = set()
    for line in body.decode("utf-8", "replace").splitlines():
        if not line or line[0] in "#;!$@" or line.startswith("*."):
            continue
        if ext == "hosts":
            parts = line.split()
            out.add(parts[1]) if len(parts) > 1 else None
        elif ext == "rpz":
            out.add(line.split()[0])
        elif ext == "adblock":
            out.add(line.removeprefix("||").removesuffix("^"))
        else:
            out.add(line.strip())
    return out


@router.get("/feeds/{name}/diff", summary="Lines added (+) and removed (-) since a feed version",
            responses={200: {"content": {"text/plain": {}}}})
def feed_diff(name: str, from_: str = Query(alias="from", min_length=1, max_length=32),
              session: Session = Depends(get_session), store=Depends(get_feed_store)) -> Response:
    if not FEED_NAME_RE.match(name) or name.rsplit(".", 1)[-1] not in ("txt", "hosts", "rpz", "adblock"):
        raise ApiError(404, "not_found", "diffs exist for list feeds (.txt .hosts .rpz .adblock)")
    old = session.scalar(select(FeedRelease).where(FeedRelease.feed == name, FeedRelease.version == from_))
    new = session.scalar(select(FeedRelease).where(FeedRelease.feed == name).order_by(FeedRelease.id.desc()).limit(1))
    if old is None or new is None:
        raise ApiError(404, "not_found", "unknown feed or version")
    before, after = _domains(name, store.get(old.object_key)), _domains(name, store.get(new.object_key))
    lines = [f"# Cat-egory {name}: {old.version} -> {new.version}", f"# Added: {len(after - before)}",
             f"# Removed: {len(before - after)}"]
    lines += [f"+{d}" for d in sorted(after - before)] + [f"-{d}" for d in sorted(before - after)]
    return Response("\n".join(lines) + "\n", media_type="text/plain; charset=utf-8",
                    headers={"X-Feed-Version": new.version, "Cache-Control": "public, max-age=900"})
