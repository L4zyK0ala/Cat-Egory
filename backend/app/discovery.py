"""Discovery: first scans of domains that discovery sources (e.g. a top-sites list) brought in.

A synced source only adds domains; nothing scans them until this task queues them. It works in small batches
under a daily budget so that crawling and the AI provider's quota grow slowly and predictably:

* at most ``discovery_batch_size`` per run (beat: every 15 minutes),
* at most ``discovery_daily_budget`` per UTC day (EVREN's quota also resets at 00:00 UTC). Only scans that can
  reach the AI use it up: failed, blocked and cancelled ones give their place back,
* nothing while the last hour has ``discovery_pause_after_infra_failures`` failures on our side, so an outage does
  not burn through the list,
* nothing while ``discovery_max_queued`` scans are already waiting, so manual and scheduled work goes first,
* each domain once, most popular first (``domain_source_records.source_rank``); later scans are scheduled by
  ``category.scan.schedule`` like any other domain.

A source takes part when its catalogue entry says ``discover: true`` and a maintainer approved its licence.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import exists, func, select
from sqlalchemy.orm import Session

from common.config import Settings, get_settings
from crawler.result import INFRA_ERRORS
from database.models import DataSource, Domain, DomainSourceRecord, JobStatus, ScanJob, ScanReason
from database.session import session_factory

from .services.domains import queue_scans
from .worker import celery_app

PRIORITY = 8  # after manual (5), evaluation (4) and scheduled re-scans (7)
NOT_SPENT = (JobStatus.FAILED, JobStatus.BLOCKED, JobStatus.CANCELLED)  # never reached the AI


def infra_failures_last_hour(session: Session, now: datetime | None = None) -> int:
    """Scans that failed on our side (sandbox, storage, proxy ...), not because of the site."""
    since = (now or datetime.now(UTC)) - timedelta(hours=1)
    return session.scalar(select(func.count()).select_from(ScanJob).where(
        ScanJob.status == JobStatus.FAILED, ScanJob.error_code.in_(INFRA_ERRORS), ScanJob.finished_at >= since)) or 0


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
    if not source_ids:
        return 0
    now = now or datetime.now(UTC)
    day_start = now.astimezone(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
    if infra_failures_last_hour(session, now) >= settings.discovery_pause_after_infra_failures:
        return 0
    used = session.scalar(select(func.count()).select_from(ScanJob).where(
        ScanJob.reason == ScanReason.DISCOVERY, ScanJob.queued_at >= day_start, ScanJob.status.not_in(NOT_SPENT))) or 0
    waiting = session.scalar(select(func.count()).select_from(ScanJob).where(ScanJob.status == JobStatus.QUEUED)) or 0
    n = min(settings.discovery_daily_budget - used, settings.discovery_max_queued - waiting,
            settings.discovery_batch_size)
    if n <= 0:
        return 0
    ever_queued = exists(select(ScanJob.id).where(ScanJob.domain_id == Domain.id))
    rank = func.min(DomainSourceRecord.source_rank)
    ids = list(session.scalars(
        select(Domain.id).join(DomainSourceRecord, DomainSourceRecord.domain_id == Domain.id)
        .where(DomainSourceRecord.source_id.in_(source_ids), DomainSourceRecord.is_listed, Domain.is_active,
               Domain.last_scanned.is_(None), ~ever_queued)
        .group_by(Domain.id).order_by(rank.asc().nulls_last(), Domain.id).limit(n)))
    return queue_scans(session, ids, reason=ScanReason.DISCOVERY, priority=PRIORITY, requested_by_id=None) if ids else 0


@celery_app.task(name="category.discovery.queue", ignore_result=True)
def queue() -> int:
    settings = get_settings()
    with session_factory()() as session, session.begin():
        return queue_discovery(session, settings)
