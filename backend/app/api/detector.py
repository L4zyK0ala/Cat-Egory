"""Threat detector (admin tab): our own page-proof rule measured against people's decisions, whether it may flag by
itself yet, and the third-party lists it gets candidates from (threats/detector.py, threats/feeds.py). Read-only."""
from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from common.config import get_settings
from database.models import DataSource, DetectorVerdict, Domain, ThreatFeedEntry
from database.session import get_session

from ..deps import Principal, rate_limit, require

router = APIRouter(prefix="/api/v1/admin/detector", tags=["admin"], dependencies=[Depends(rate_limit("write"))])


class Measure(BaseModel):
    rule: str
    verdicts: int      # domains judged (newest verdict each)
    positive: int
    tp: int            # positive, and a person flagged it
    fp: int            # positive, but a person decided it is not a threat (or withdrew the detector's flag)
    fn: int            # negative, but a person flagged it
    tn: int
    pending: int       # nobody decided yet
    precision: float | None
    recall: float | None
    listed: int        # positive verdicts a threat list also names
    flagged: int       # flags the detector added itself (phase 3)
    withdrawn: int


class Gate(BaseModel):
    auto: bool                 # CATEGORY_THREAT_DETECTOR_AUTO
    qualified: bool            # enough decisions, precise enough
    min_decisions: int
    min_precision: float
    max_per_day: int
    flagged_today: int


class Verdict(BaseModel):
    domain: str
    positive: bool
    signals: dict
    action: str | None
    created_at: datetime


class Feed(BaseModel):
    slug: str
    enabled: bool
    last_success_at: datetime | None
    last_error: str | None
    listed: int
    brand: int     # names resembling a watched Turkish brand
    tr: int        # .tr names (no brand)
    picked: int    # queued for a scan so far


class DetectorOut(BaseModel):
    measure: Measure
    gate: Gate
    recent: list[Verdict]
    feeds: list[Feed]
    per_hour: int


@router.get("", response_model=DetectorOut, summary="Threat detector: precision against people, gate, candidate lists")
def detector(limit: int = Query(50, ge=1, le=200), _: Principal = Depends(require("scans:read")),
             session: Session = Depends(get_session)) -> DetectorOut:
    from integrations.engine import catalogue
    from threats.detector import flagged_today, measure, qualified

    settings = get_settings()
    stats = measure(session)
    rows = session.execute(select(DetectorVerdict, Domain.normalized_domain)
                           .join(Domain, Domain.id == DetectorVerdict.domain_id)
                           .order_by(DetectorVerdict.created_at.desc(), DetectorVerdict.id.desc()).limit(limit)).all()
    slugs = [s for s, e in catalogue().items() if e.get("candidates_only")]
    feeds = []
    for source in session.scalars(select(DataSource).where(DataSource.slug.in_(slugs)).order_by(DataSource.slug)):
        counts = dict(session.execute(
            select(ThreatFeedEntry.relevance, func.count()).where(ThreatFeedEntry.source_id == source.id,
                                                                  ThreatFeedEntry.is_listed)
            .group_by(ThreatFeedEntry.relevance)).all())
        picked = session.scalar(select(func.count()).select_from(ThreatFeedEntry).where(
            ThreatFeedEntry.source_id == source.id, ThreatFeedEntry.picked_at.is_not(None))) or 0
        feeds.append(Feed(slug=source.slug, enabled=source.enabled, last_success_at=source.last_success_at,
                          last_error=source.last_error, listed=sum(counts.values()), brand=counts.get(2, 0),
                          tr=counts.get(1, 0), picked=picked))
    return DetectorOut(
        measure=Measure(**stats),
        gate=Gate(auto=settings.threat_detector_auto, qualified=qualified(stats, settings),
                  min_decisions=settings.threat_detector_min_decisions,
                  min_precision=settings.threat_detector_min_precision,
                  max_per_day=settings.threat_detector_max_per_day, flagged_today=flagged_today(session, settings)),
        recent=[Verdict(domain=name, positive=v.positive, signals=v.signals, action=v.action, created_at=v.created_at)
                for v, name in rows],
        feeds=feeds, per_hour=settings.threat_feed_candidates_per_hour)
