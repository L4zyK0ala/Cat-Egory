"""Monthly numbers for the admin "İnfografik" tab: the figures behind a month's social-media post (LinkedIn etc.).

Totals only, and only what Cat-egory may publish: its own categories, the threats on its public pages, and
visitor demand as category totals. Never a looked-up name (that would say what someone asked), never the
national (USOM) list or VirusTotal answers (their terms forbid republishing them)."""
from __future__ import annotations

import re
from datetime import UTC, date, datetime, timedelta

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from classification.engine import DETECTOR_SOURCE, OCTRYX_SOURCE, REVIEW_SOURCE
from common.config import get_settings
from database.models import (
    ClassificationStatus,
    DataSource,
    DemandDaily,
    DetectorVerdict,
    Domain,
    FeedDownloadDaily,
    ThreatIntelligence,
)
from database.session import get_session
from threats.usom import brand_of

from ..demand import local_day
from ..deps import Principal, rate_limit, require
from ..errors import ApiError

router = APIRouter(prefix="/api/v1/admin/infographic", tags=["admin"], dependencies=[Depends(rate_limit("write"))])

PUBLISHED = (ClassificationStatus.CLASSIFIED, ClassificationStatus.VERIFIED)
OWN_SOURCES = (REVIEW_SOURCE, DETECTOR_SOURCE, OCTRYX_SOURCE)
TOP = 8


class Count(BaseModel):
    key: str
    count: int


class Totals(BaseModel):
    published: int       # published at the end of the month (classified or verified now, first seen by then)
    new_published: int   # of those, first seen during the month
    verified: int        # published and checked by a person
    tr_published: int    # published .tr names
    categories_used: int


class Threats(BaseModel):
    new_domains: int     # domains with a threat record first seen this month (any public source)
    own_domains: int     # of those, confirmed by our reviewers, our detector or Octryx
    by_type: list[Count]
    brands: list[Count]  # brands the new threat domains imitate (by name, or by the brand their page claimed)
    suffixes: list[Count]


class Demand(BaseModel):
    lookups: int
    domains: int
    feed_downloads: int
    by_category: list[Count]  # visitors (summed per day) of looked-up known names, by main category
    daily: list[Count]


class InfographicOut(BaseModel):
    month: str
    start: str
    end: str
    complete: bool  # the month is over
    totals: Totals
    categories: list[Count]
    tr_categories: list[Count]
    threats: Threats
    demand: Demand


def month_bounds(month: str | None, today: date) -> tuple[date, date]:
    """First day of the month and first day of the next one; default: the last finished month."""
    if month is None:
        first = (today.replace(day=1) - timedelta(days=1)).replace(day=1)
    else:
        if not re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", month):
            raise ApiError(422, "validation_error", "month must look like 2026-10")
        first = date(int(month[:4]), int(month[5:]), 1)
        if first > today:
            raise ApiError(422, "validation_error", "that month has not started yet")
    following = (first.replace(day=28) + timedelta(days=4)).replace(day=1)
    return first, following


def _top(rows, limit: int = TOP) -> list[Count]:
    """The largest ``limit`` counts and the rest summed as "other"."""
    rows = sorted(((str(k), int(n)) for k, n in rows if k is not None and n), key=lambda r: (-r[1], r[0]))
    out = [Count(key=k, count=n) for k, n in rows[:limit]]
    rest = sum(n for _, n in rows[limit:])
    return out + ([Count(key="other", count=rest)] if rest else [])


@router.get("", response_model=InfographicOut, summary="One month's numbers for a social-media infographic")
def infographic(month: str | None = Query(None, description="YYYY-MM (Türkiye time); default: last finished month"),
                _: Principal = Depends(require("scans:read")), session: Session = Depends(get_session)) -> InfographicOut:
    settings = get_settings()
    today = local_day(datetime.now(UTC), settings)
    first, following = month_bounds(month, today)
    offset = timedelta(hours=settings.app_utc_offset_hours)
    start = datetime(first.year, first.month, 1, tzinfo=UTC) - offset
    end = datetime(following.year, following.month, 1, tzinfo=UTC) - offset

    published = (Domain.classification_status.in_(PUBLISHED), Domain.is_active, Domain.first_seen < end,
                 Domain.primary_category_id.is_not(None))
    tr = (Domain.public_suffix == "tr") | Domain.public_suffix.like("%.tr")
    count = func.count(Domain.id)
    totals = Totals(
        published=session.scalar(select(count).where(*published)) or 0,
        new_published=session.scalar(select(count).where(*published, Domain.first_seen >= start)) or 0,
        verified=session.scalar(select(count).where(*published, Domain.classification_status
                                                    == ClassificationStatus.VERIFIED)) or 0,
        tr_published=session.scalar(select(count).where(*published, tr)) or 0,
        categories_used=session.scalar(select(func.count(func.distinct(Domain.primary_category_id)))
                                       .where(*published)) or 0)
    by_category = select(Domain.primary_category_id, count).where(*published).group_by(Domain.primary_category_id)
    categories = _top(session.execute(by_category).all())
    tr_categories = _top(session.execute(by_category.where(tr)).all())

    rows = session.execute(
        select(ThreatIntelligence.domain_id, ThreatIntelligence.threat_type, DataSource.slug)
        .join(DataSource, DataSource.id == ThreatIntelligence.source_id)
        .where(ThreatIntelligence.first_seen >= start, ThreatIntelligence.first_seen < end)).all()
    by_type: dict[str, set[int]] = {}
    for r in rows:
        by_type.setdefault(str(r.threat_type), set()).add(r.domain_id)
    hit = {r.domain_id for r in rows}
    hosts = session.execute(select(Domain.id, Domain.normalized_domain, Domain.public_suffix)
                            .where(Domain.id.in_(hit))).all() if hit else []
    claimed = dict(session.execute(
        select(DetectorVerdict.domain_id, DetectorVerdict.signals["brand"].astext)
        .where(DetectorVerdict.domain_id.in_(hit), DetectorVerdict.positive)
        .order_by(DetectorVerdict.id)).all()) if hit else {}  # the newest verdict wins
    brands: dict[str, int] = {}
    suffixes: dict[str, int] = {}
    for i, name, suffix in hosts:
        if brand := brand_of(name, claimed.get(i)):
            brands[brand] = brands.get(brand, 0) + 1
        suffixes["." + suffix] = suffixes.get("." + suffix, 0) + 1
    threats = Threats(
        new_domains=len(hit),
        own_domains=len({r.domain_id for r in rows if r.slug in OWN_SOURCES}),
        by_type=_top(((k, len(v)) for k, v in by_type.items()), limit=len(by_type) or 1),
        brands=[c for c in _top(brands.items()) if c.key != "other"],
        suffixes=_top(suffixes.items(), limit=6))

    in_month = (DemandDaily.day >= first, DemandDaily.day < following)
    demand = Demand(
        lookups=session.scalar(select(func.coalesce(func.sum(DemandDaily.lookups), 0)).where(*in_month)) or 0,
        domains=session.scalar(select(func.count(func.distinct(DemandDaily.domain))).where(*in_month)) or 0,
        feed_downloads=session.scalar(select(func.coalesce(func.sum(FeedDownloadDaily.downloads), 0))
                                      .where(FeedDownloadDaily.day >= first, FeedDownloadDaily.day < following)) or 0,
        by_category=_top(session.execute(
            select(Domain.primary_category_id, func.sum(DemandDaily.callers))
            .join(Domain, Domain.normalized_domain == DemandDaily.domain)
            .where(*in_month, Domain.primary_category_id.is_not(None))
            .group_by(Domain.primary_category_id)).all(), limit=6),
        daily=[Count(key=d.isoformat(), count=int(n)) for d, n in session.execute(
            select(DemandDaily.day, func.sum(DemandDaily.lookups)).where(*in_month)
            .group_by(DemandDaily.day).order_by(DemandDaily.day)).all()])
    return InfographicOut(month=first.strftime("%Y-%m"), start=start.isoformat(), end=end.isoformat(),
                          complete=following <= today, totals=totals, categories=categories,
                          tr_categories=tr_categories, threats=threats, demand=demand)
