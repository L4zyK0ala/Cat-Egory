"""Trends (admin "Yönelim" tab): what visitors look up and which lists they download, from the daily totals of
backend/app/demand.py. Totals only; nothing here says who asked."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from common.config import get_settings
from database.models import DemandDaily, Domain, FeedDownloadDaily
from database.session import get_session

from ..demand import local_day
from ..deps import Principal, rate_limit, require

router = APIRouter(prefix="/api/v1/admin/trends", tags=["admin"], dependencies=[Depends(rate_limit("write"))])


class DomainDemand(BaseModel):
    domain: str
    lookups: int
    callers: int  # sum of the daily distinct-visitor counts (a visitor on two days counts twice)
    days: int     # days it was looked up on
    known: bool   # in the database now
    classification_status: str | None
    primary: str | None


class FeedDemand(BaseModel):
    feed: str
    downloads: int  # requests, including "not modified" checks of polling clients
    callers: int


class DayTotal(BaseModel):
    day: str
    lookups: int
    domains: int


class TrendsOut(BaseModel):
    days: int
    since: str
    known: list[DomainDemand]
    unknown: list[DomainDemand]
    feeds: list[FeedDemand]
    daily: list[DayTotal]
    min_callers: int  # an unknown name joins discovery with at least this many visitors on one day


@router.get("", response_model=TrendsOut, summary="Most looked-up domains and most downloaded lists (finished days)")
def trends(days: int = Query(30, ge=1, le=90), limit: int = Query(50, ge=1, le=500),
           _: Principal = Depends(require("scans:read")), session: Session = Depends(get_session)) -> TrendsOut:
    settings = get_settings()
    since = local_day(datetime.now(UTC), settings) - timedelta(days=days)
    in_window = DemandDaily.day >= since
    lookups, callers = func.sum(DemandDaily.lookups), func.sum(DemandDaily.callers)
    totals = (select(DemandDaily.domain, lookups.label("lookups"), callers.label("callers"),
                     func.count().label("days"), Domain.classification_status, Domain.primary_category_id)
              .outerjoin(Domain, Domain.normalized_domain == DemandDaily.domain).where(in_window)
              .group_by(DemandDaily.domain, Domain.id)
              .order_by(callers.desc(), lookups.desc(), DemandDaily.domain).limit(limit))

    def listed(known: bool) -> list[DomainDemand]:
        rows = session.execute(totals.where(Domain.id.is_not(None) if known else Domain.id.is_(None))).all()
        return [DomainDemand(domain=r.domain, lookups=int(r.lookups), callers=int(r.callers), days=int(r.days),
                             known=known, classification_status=str(r.classification_status) if known else None,
                             primary=r.primary_category_id) for r in rows]

    known, unknown = listed(True), listed(False)
    feed_rows = session.execute(
        select(FeedDownloadDaily.feed, func.sum(FeedDownloadDaily.downloads), func.sum(FeedDownloadDaily.callers))
        .where(FeedDownloadDaily.day >= since).group_by(FeedDownloadDaily.feed)
        .order_by(func.sum(FeedDownloadDaily.callers).desc(), FeedDownloadDaily.feed)).all()
    daily = session.execute(select(DemandDaily.day, lookups, func.count()).where(in_window)
                            .group_by(DemandDaily.day).order_by(DemandDaily.day)).all()
    return TrendsOut(days=days, since=since.isoformat(), known=known, unknown=unknown,
                     feeds=[FeedDemand(feed=f, downloads=int(n), callers=int(c)) for f, n, c in feed_rows],
                     daily=[DayTotal(day=d.isoformat(), lookups=int(n), domains=int(k)) for d, n, k in daily],
                     min_callers=settings.demand_min_callers)
