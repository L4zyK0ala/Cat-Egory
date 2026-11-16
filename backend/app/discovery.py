"""Discovery: first scans of domains that discovery sources (e.g. a top-sites list) brought in.

A synced source only adds domains; nothing scans them until this task queues them. It works in small batches
under a daily budget so that crawling and the AI provider's quota grow slowly and predictably:

* at most ``discovery_batch_size`` per run (beat: every 15 minutes),
* at most ``discovery_daily_budget`` per day, Türkiye time. Only scans that can
  reach the AI use it up: failed, blocked and cancelled ones give their place back,
* nothing while the last hour has ``discovery_pause_after_infra_failures`` failures on our side, so an outage does
  not burn through the list,
* nothing while ``discovery_max_queued`` scans are already waiting, so manual and scheduled work goes first,
* nothing once today's EVREN tokens (all work, Türkiye time) reach ``ai_daily_token_cap``, so discovery never eats
  into the share of the account's quota that other projects need,
* first the names several visitors looked up while we did not know them (``demand`` source, added nightly by
  ``backend/app/demand.py``),
* each domain once, most popular first (``domain_source_records.source_rank``, the row in the source file); later
  scans are scheduled by ``category.scan.schedule`` like any other domain,
* a quarter of each batch goes to names that hint at restricted content (``RESTRICTED_HINT``: casino, bahis, porn,
  vape, ...), most popular first, so the lists people block with most (adult, gambling, alcohol, tobacco, drugs,
  weapons) fill up without waiting for the whole popularity order. A wrong hint costs nothing: the scan decides.

A source takes part when its catalogue entry says ``discover: true`` and a maintainer approved its licence.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import exists, func, select, true
from sqlalchemy.orm import Session

from common.config import Settings, get_settings
from common.timeutil import local_midnight
from crawler.result import INFRA_ERRORS
from database.models import (
    AIPrediction,
    DataSource,
    Domain,
    DomainSourceRecord,
    JobStatus,
    ModelComparison,
    ScanJob,
    ScanReason,
)
from database.session import session_factory

from .services.domains import queue_scans
from .worker import celery_app

DEMAND_SOURCE = "demand"  # backend/app/demand.py (kept apart: that module is imported by the API)
PRIORITY = 8  # after manual (5), evaluation (4) and scheduled re-scans (7)
# Names that hint at restricted content (PostgreSQL regex, case-insensitive, on the ASCII name). Kept narrow: "gun"
# would catch every "gunes" (güneş), "sex" every "essex".
RESTRICTED_HINT = (
    r"casino|bahis|betting|sportsbook|poker|roulette|rulet|jackpot|iddaa|kumar|slots?[.-]|(^|[.-])bet|bet([0-9]|[.-])"
    r"|porn|xxx|xvideo|xnxx|xhamster|hentai|escort|onlyfans|nsfw"
    r"|vape|vaping|tobacco|sigara|cigar|nargile|hookah|shisha|cbd|cannabis|marijuana|kratom"
    r"|wine|sarap|beer|bira|vodka|whisk|liquor|brewery"
    r"|silah|firearm|ammo|airsoft|tabanca|tufek"
)
RESTRICTED_SHARE = 4  # one in four of each batch
NOT_SPENT = (JobStatus.FAILED, JobStatus.BLOCKED, JobStatus.CANCELLED)  # never reached the AI


def infra_failures_last_hour(session: Session, now: datetime | None = None) -> int:
    """Scans that failed on our side (sandbox, storage, proxy ...), not because of the site."""
    since = (now or datetime.now(UTC)) - timedelta(hours=1)
    return session.scalar(select(func.count()).select_from(ScanJob).where(
        ScanJob.status == JobStatus.FAILED, ScanJob.error_code.in_(INFRA_ERRORS), ScanJob.finished_at >= since)) or 0


def tokens_since(session: Session, since: datetime) -> int:
    """AI tokens (in + out) used since ``since`` by real providers (the offline mock costs nothing), including
    model comparisons (``classification/model_compare.py``): they spend the same account's quota."""
    total = 0
    for table in (AIPrediction, ModelComparison):
        total += int(session.scalar(select(func.coalesce(func.sum(
            func.coalesce(table.input_tokens, 0) + func.coalesce(table.output_tokens, 0)), 0))
            .where(table.created_at >= since, table.provider != "mock")) or 0)
    return total


def discovery_source_ids(session: Session, entries: dict[str, dict[str, Any]] | None = None) -> list[int]:
    if entries is None:
        from integrations.engine import catalogue

        entries = catalogue()
    slugs = [slug for slug, entry in entries.items() if entry.get("discover")]
    if not slugs:
        return []
    return list(session.scalars(select(DataSource.id).where(DataSource.slug.in_(slugs), DataSource.enabled,
                                                            DataSource.license_id.is_not(None))))


def queue_discovery(session: Session, settings: Settings, *, entries: dict[str, dict[str, Any]] | None = None,
                    now: datetime | None = None) -> int:
    """Queue the next batch of never-scanned discovery domains. Returns how many were queued."""
    if settings.discovery_daily_budget <= 0:
        return 0
    source_ids = discovery_source_ids(session, entries)
    # names several visitors looked up while we did not know them (backend/app/demand.py): before the lists
    demand_id = session.scalar(select(DataSource.id).where(DataSource.slug == DEMAND_SOURCE, DataSource.enabled))
    if not source_ids and demand_id is None:
        return 0
    now = now or datetime.now(UTC)
    day_start = local_midnight(now, settings.app_utc_offset_hours)
    if infra_failures_last_hour(session, now) >= settings.discovery_pause_after_infra_failures:
        return 0
    if settings.ai_daily_token_cap and tokens_since(session, day_start) >= settings.ai_daily_token_cap:
        return 0  # today's share of the AI quota is used up; tomorrow it starts again
    used = session.scalar(select(func.count()).select_from(ScanJob).where(
        ScanJob.reason == ScanReason.DISCOVERY, ScanJob.queued_at >= day_start, ScanJob.status.not_in(NOT_SPENT))) or 0
    waiting = session.scalar(select(func.count()).select_from(ScanJob).where(ScanJob.status == JobStatus.QUEUED)) or 0
    n = min(settings.discovery_daily_budget - used, settings.discovery_max_queued - waiting,
            settings.discovery_batch_size)
    if n <= 0:
        return 0
    # A cancelled job never ran (895 domains were cancelled when the offline demo was cleaned up and then never
    # came back): only a job that was not cancelled counts as "already queued once".
    ever_queued = exists(select(ScanJob.id).where(ScanJob.domain_id == Domain.id,
                                                  ScanJob.status != JobStatus.CANCELLED))
    rank = func.min(DomainSourceRecord.source_rank)

    def candidates(ids: list[int]):
        return (select(Domain.id).join(DomainSourceRecord, DomainSourceRecord.domain_id == Domain.id)
                .where(DomainSourceRecord.source_id.in_(ids), DomainSourceRecord.is_listed, Domain.is_active,
                       Domain.last_scanned.is_(None), ~ever_queued)
                .group_by(Domain.id).order_by(rank.asc().nulls_last(), Domain.id))

    ids = list(session.scalars(candidates([demand_id]).limit(n))) if demand_id is not None else []
    if source_ids and len(ids) < n:
        listed = candidates(source_ids).where(Domain.id.not_in(ids) if ids else true())
        room = n - len(ids)
        hinted = list(session.scalars(listed.where(Domain.normalized_domain.op("~*")(RESTRICTED_HINT))
                                      .limit(max(1, room // RESTRICTED_SHARE))))
        ids += hinted + list(session.scalars(listed.where(Domain.id.not_in(hinted) if hinted else true())
                                             .limit(room - len(hinted))))
    return queue_scans(session, ids, reason=ScanReason.DISCOVERY, priority=PRIORITY, requested_by_id=None) if ids else 0


@celery_app.task(name="category.discovery.queue", ignore_result=True)
def queue() -> int:
    settings = get_settings()
    with session_factory()() as session, session.begin():
        return queue_discovery(session, settings)
