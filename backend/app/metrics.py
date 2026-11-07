"""Business metrics for Prometheus, computed from the database at scrape time (cached for 30 s).

Exposed on the API's /metrics next to the HTTP metrics, so one scrape target covers the whole pipeline:
domains per status, scan queue depth per status, open reviews / moderation items, AI answer validity and
the age of the newest feed build.
"""
from __future__ import annotations

import logging
import time
from datetime import UTC, datetime, timedelta

from prometheus_client.core import GaugeMetricFamily
from prometheus_client.registry import REGISTRY, Collector
from sqlalchemy import func, select

from database.models import (
    AIPrediction,
    CommunitySubmission,
    Domain,
    FeedRelease,
    ModerationStatus,
    ReviewStatus,
    ReviewTask,
    ScanJob,
    ThreatIntelligence,
)
from database.session import session_factory

log = logging.getLogger("category.metrics")
CACHE_SECONDS = 30


class DatabaseCollector(Collector):
    def __init__(self) -> None:
        self._cache: list = []
        self._at = 0.0

    def _query(self) -> list:
        families = []
        with session_factory()() as s:
            g = GaugeMetricFamily("category_domains", "Active domains by classification status", labels=["status"])
            for status, n in s.execute(select(Domain.classification_status, func.count()).where(Domain.is_active)
                                       .group_by(Domain.classification_status)):
                g.add_metric([str(status)], n)
            families.append(g)
            g = GaugeMetricFamily("category_scan_jobs", "Scan jobs by status", labels=["status"])
            for status, n in s.execute(select(ScanJob.status, func.count()).group_by(ScanJob.status)):
                g.add_metric([str(status)], n)
            families.append(g)
            families.append(GaugeMetricFamily("category_open_reviews", "Open human-review tasks", value=s.scalar(
                select(func.count()).select_from(ReviewTask).where(ReviewTask.status == ReviewStatus.OPEN)) or 0))
            families.append(GaugeMetricFamily("category_pending_submissions", "Community items awaiting moderation",
                                              value=s.scalar(select(func.count()).select_from(CommunitySubmission).where(
                                                  CommunitySubmission.status == ModerationStatus.PENDING)) or 0))
            families.append(GaugeMetricFamily("category_active_threats", "Active threat records", value=s.scalar(
                select(func.count()).select_from(ThreatIntelligence).where(ThreatIntelligence.is_active)) or 0))
            g = GaugeMetricFamily("category_ai_predictions_24h", "AI predictions in the last 24 h by validation status",
                                  labels=["status"])
            since = datetime.now(UTC) - timedelta(hours=24)
            for status, n in s.execute(select(AIPrediction.validation_status, func.count())
                                       .where(AIPrediction.created_at >= since).group_by(AIPrediction.validation_status)):
                g.add_metric([str(status)], n)
            families.append(g)
            newest = s.scalar(select(func.max(FeedRelease.generated_at)))
            families.append(GaugeMetricFamily("category_feed_age_seconds", "Seconds since the newest feed release",
                                              value=(datetime.now(UTC) - newest).total_seconds() if newest else -1))
        return families

    def collect(self):
        if time.monotonic() - self._at > CACHE_SECONDS:
            try:
                self._cache, self._at = self._query(), time.monotonic()
            except Exception as exc:  # noqa: BLE001 - metrics must never break the scrape
                log.warning("database metrics unavailable", extra={"error": type(exc).__name__})
                up = GaugeMetricFamily("category_db_metrics_up", "1 if database metrics could be collected", value=0)
                return iter([up])
        return iter([*self._cache, GaugeMetricFamily("category_db_metrics_up", "1 if database metrics could be collected",
                                                     value=1)])


_installed = False


def install() -> None:
    global _installed
    if not _installed:
        REGISTRY.register(DatabaseCollector())
        _installed = True
