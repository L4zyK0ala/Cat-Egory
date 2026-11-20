"""Monthly numbers for the admin "İnfografik" tab: the figures behind a month's social-media post (LinkedIn etc.).

Totals only, and only what Cat-egory may publish: its own categories, the threats on its public pages, and
visitor demand as category totals. Never a looked-up name (that would say what someone asked), never the
national (USOM) list or VirusTotal answers (their terms forbid republishing them)."""
from __future__ import annotations

import re
from datetime import UTC, date, datetime, timedelta

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import Integer, func, literal_column, select
from sqlalchemy.orm import Session

from classification.engine import DETECTOR_SOURCE, OCTRYX_SOURCE, REVIEW_SOURCE
from common.config import get_settings
from database.models import (
    AIPrediction,
    AuditLog,
    ClassificationStatus,
    DataSource,
    DemandDaily,
    DetectorVerdict,
    Domain,
    FeedDownloadDaily,
    JobStatus,
    ReviewStatus,
    ReviewTask,
    ScanJob,
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


class Funnel(BaseModel):
    """Discovery to verification, unique registrable domains (each a subset of the one before, except new_known)."""
    known: int             # active domains first seen before the month ended
    new_known: int         # of those, first seen in the month
    scanned: int           # unique domains with a successful whole-site scan in the month
    published: int         # with a published main category at the snapshot
    verified: int          # of those, checked by a person


class ThreatFlow(BaseModel):
    """Threat candidates people decide on (look-alikes, list hits, model claims), unique tasks in the month."""
    candidates: int        # opened in the month
    decided: int           # decided in the month
    confirmed: int         # decided in the month with at least one threat confirmed
    labels: int            # threat records first seen in the month (one domain can carry several)


class AiQuality(BaseModel):
    answers: int           # AI answers given in the month
    valid: int             # of those, valid JSON with known categories (format, not correctness)
    models: list[str]
    sample_n: int          # random verification sample decided in the month (published answers)
    sample_kept: int       # kept unchanged by the reviewer
    sample_low: float | None
    sample_high: float | None
    all_sample_n: int      # the same, all months (the accuracy page's figure)
    all_sample_kept: int
    hard_n: int            # unsure / disputed answers decided in the month
    hard_kept: int


class Crawler(BaseModel):
    jobs: int              # whole-site scan jobs finished in the month (one domain can have several)
    succeeded: int
    failed: int            # technical failures (DNS, timeout, browser ...)
    blocked: int           # refused by policy (robots.txt, SSRF guard): not a technical failure
    unique_succeeded: int  # unique domains with a successful scan
    retried: int           # jobs that needed more than one attempt
    recovered: int         # of those, succeeded in the end
    errors: list[Count]
    daily: list[Count]     # unique domains successfully scanned per day


class Usage(BaseModel):
    feed_downloads: int    # requests, not installations
    feed_clients: int      # distinct callers summed over days (approximate)
    formats: list[Count]
    lists: list[Count]
    lookups: int           # public API / site lookups
    lookup_daily: list[Count]


class InfographicOut(BaseModel):
    month: str
    start: str
    end: str
    complete: bool  # the month is over
    generated_at: str
    totals: Totals
    categories: list[Count]
    tr_categories: list[Count]
    threats: Threats
    demand: Demand
    funnel: Funnel
    threat_flow: ThreatFlow
    ai: AiQuality
    crawler: Crawler
    usage: Usage


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
                          complete=following <= today, generated_at=datetime.now(UTC).isoformat(), totals=totals,
                          categories=categories, tr_categories=tr_categories, threats=threats, demand=demand,
                          funnel=funnel(session, start, end, totals),
                          threat_flow=threat_flow(session, start, end, sum(c.count for c in threats.by_type)),
                          ai=ai_quality(session, start, end), crawler=crawler(session, start, end, offset),
                          usage=usage(session, first, following, demand))


def funnel(session: Session, start: datetime, end: datetime, totals: Totals) -> Funnel:
    known = (Domain.is_active, Domain.first_seen < end)
    scanned = (select(func.count(func.distinct(ScanJob.domain_id)))
               .where(ScanJob.status == JobStatus.SUCCEEDED, ScanJob.url_id.is_(None),
                      ScanJob.finished_at >= start, ScanJob.finished_at < end))
    return Funnel(known=session.scalar(select(func.count(Domain.id)).where(*known)) or 0,
                  new_known=session.scalar(select(func.count(Domain.id)).where(*known, Domain.first_seen >= start)) or 0,
                  scanned=session.scalar(scanned) or 0, published=totals.published, verified=totals.verified)


def threat_flow(session: Session, start: datetime, end: datetime, labels: int) -> ThreatFlow:
    from classification.engine import threat_candidate

    candidate = threat_candidate()
    opened = session.scalar(select(func.count(ReviewTask.id))
                            .where(candidate, ReviewTask.created_at >= start, ReviewTask.created_at < end)) or 0
    decided_ids = set(session.scalars(select(ReviewTask.id).where(
        candidate, ReviewTask.status == ReviewStatus.DONE, ReviewTask.decided_at >= start, ReviewTask.decided_at < end)))
    with_risk = set(session.scalars(
        select(AuditLog.after["task_id"].astext.cast(Integer))
        .where(AuditLog.action == "review.decide", AuditLog.occurred_at >= start, AuditLog.occurred_at < end,
               func.jsonb_array_length(AuditLog.after["risk"]) > 0))) if decided_ids else set()
    return ThreatFlow(candidates=opened, decided=len(decided_ids), confirmed=len(decided_ids & with_risk),
                      labels=labels)


def ai_quality(session: Session, start: datetime, end: datetime) -> AiQuality:
    from ai_providers.schemas import ValidationStatus
    from classification.accuracy import HARD_REASONS, SAMPLE_REASON, wilson

    in_month = (AIPrediction.created_at >= start, AIPrediction.created_at < end)
    answers = session.scalar(select(func.count(AIPrediction.id)).where(*in_month)) or 0
    valid = session.scalar(select(func.count(AIPrediction.id))
                           .where(*in_month, AIPrediction.validation_status == ValidationStatus.VALID)) or 0
    models = sorted(m for (m,) in session.execute(select(func.distinct(AIPrediction.model)).where(*in_month)) if m)
    decided = (select(ReviewTask.reason, ReviewTask.decided_at, AIPrediction.primary_category_id,
                      ReviewTask.decision_primary_id)
               .join(AIPrediction, AIPrediction.id == ReviewTask.ai_prediction_id)
               .where(ReviewTask.status == ReviewStatus.DONE, AIPrediction.primary_category_id.is_not(None),
                      ReviewTask.reason.in_((SAMPLE_REASON, *HARD_REASONS))))
    rows = session.execute(decided).all()
    month = [r for r in rows if r.decided_at is not None and start <= r.decided_at < end]
    sample = [r for r in month if r.reason == SAMPLE_REASON]
    every_sample = [r for r in rows if r.reason == SAMPLE_REASON]
    hard = [r for r in month if r.reason in HARD_REASONS]
    kept = sum(r.primary_category_id == r.decision_primary_id for r in sample)
    interval = wilson(kept, len(sample))
    return AiQuality(answers=answers, valid=valid, models=models, sample_n=len(sample), sample_kept=kept,
                     sample_low=interval[0] if interval else None, sample_high=interval[1] if interval else None,
                     all_sample_n=len(every_sample),
                     all_sample_kept=sum(r.primary_category_id == r.decision_primary_id for r in every_sample),
                     hard_n=len(hard), hard_kept=sum(r.primary_category_id == r.decision_primary_id for r in hard))


def crawler(session: Session, start: datetime, end: datetime, offset: timedelta) -> Crawler:
    finished = (ScanJob.url_id.is_(None), ScanJob.finished_at >= start, ScanJob.finished_at < end,
                ScanJob.status.in_((JobStatus.SUCCEEDED, JobStatus.FAILED, JobStatus.BLOCKED)))
    by_status = dict(session.execute(select(ScanJob.status, func.count()).where(*finished)
                                     .group_by(ScanJob.status)).all())
    retried = session.execute(select(ScanJob.status, func.count()).where(*finished, ScanJob.attempts > 1)
                              .group_by(ScanJob.status)).all()
    errors = session.execute(select(ScanJob.error_code, func.count())
                             .where(*finished, ScanJob.status != JobStatus.SUCCEEDED, ScanJob.error_code.is_not(None))
                             .group_by(ScanJob.error_code)).all()
    local_day = func.date(ScanJob.finished_at + literal_column(f"interval '{int(offset.total_seconds())} seconds'"))
    daily = session.execute(select(local_day, func.count(func.distinct(ScanJob.domain_id)))
                            .where(*finished, ScanJob.status == JobStatus.SUCCEEDED)
                            .group_by(local_day).order_by(local_day)).all()
    unique = session.scalar(select(func.count(func.distinct(ScanJob.domain_id)))
                            .where(*finished, ScanJob.status == JobStatus.SUCCEEDED)) or 0
    return Crawler(jobs=sum(by_status.values()), succeeded=by_status.get(JobStatus.SUCCEEDED, 0),
                   failed=by_status.get(JobStatus.FAILED, 0), blocked=by_status.get(JobStatus.BLOCKED, 0),
                   unique_succeeded=unique, retried=sum(n for _, n in retried),
                   recovered=sum(n for st, n in retried if st == JobStatus.SUCCEEDED),
                   errors=_top(errors, limit=5), daily=[Count(key=d.isoformat(), count=int(n)) for d, n in daily])


def usage(session: Session, first: date, following: date, demand: Demand) -> Usage:
    rows = session.execute(select(FeedDownloadDaily.feed, func.sum(FeedDownloadDaily.downloads),
                                  func.sum(FeedDownloadDaily.callers))
                           .where(FeedDownloadDaily.day >= first, FeedDownloadDaily.day < following)
                           .group_by(FeedDownloadDaily.feed)).all()
    formats: dict[str, int] = {}
    lists: dict[str, int] = {}
    for feed, downloads, _ in rows:
        name, _, ext = feed.rpartition(".")
        formats[ext or "other"] = formats.get(ext or "other", 0) + int(downloads)
        lists[name or feed] = lists.get(name or feed, 0) + int(downloads)
    return Usage(feed_downloads=sum(int(d) for _, d, _ in rows), feed_clients=sum(int(c) for _, _, c in rows),
                 formats=_top(formats.items(), limit=6), lists=_top(lists.items(), limit=6),
                 lookups=demand.lookups, lookup_daily=demand.daily)
