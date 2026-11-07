"""Celery application. Each workload has its own queue so it can be scaled (or isolated) separately:

* ``crawl``       – browser crawling, runs only in the crawler container (no DB access)
* ``scan``        – scan pipeline on the core worker: dispatch, DNS pre-check, persist results
* ``classify``    – AI classification + validation (Phase 4)
* ``ingest``      – imports and third-party source sync (Phase 5)
* ``feeds``       – feed generation (Phase 5)
* ``maintenance`` – health checks, retention, retries
"""
from __future__ import annotations

from celery import Celery
from kombu import Queue

from common.config import get_settings

QUEUES = ("crawl", "scan", "classify", "ingest", "feeds", "maintenance")

settings = get_settings()
celery_app = Celery("category", broker=settings.redis_url, backend=settings.redis_url,
                    include=["backend.app.scan_tasks", "backend.app.classify_tasks", "backend.app.feed_tasks",
                             "backend.app.ingest_tasks"])
celery_app.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],  # never pickle: queue messages are not trusted code
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    worker_prefetch_multiplier=1,
    task_time_limit=300,
    task_soft_time_limit=240,
    result_expires=86_400,
    task_default_queue="maintenance",
    task_queues=[Queue(q) for q in QUEUES],
    task_routes={"category.crawl.*": {"queue": "crawl"}, "category.scan.*": {"queue": "scan"},
                 "category.classify.*": {"queue": "classify"},
                 "category.ingest.*": {"queue": "ingest"}, "category.feeds.*": {"queue": "feeds"}},
    beat_schedule={
        "provider-health": {"task": "category.maintenance.provider_health", "schedule": 300.0},
        "scan-dispatch": {"task": "category.scan.dispatch", "schedule": 10.0, "options": {"expires": 9}},
        "scan-reap": {"task": "category.scan.reap", "schedule": 300.0},
        "scan-schedule": {"task": "category.scan.schedule", "schedule": 300.0},
        "feeds-build": {"task": "category.feeds.build", "schedule": 3600.0},
        "sources-sync": {"task": "category.ingest.sync_due", "schedule": 3600.0},
    },
    timezone="UTC",
)


@celery_app.task(name="category.maintenance.ping")
def ping() -> str:
    return "pong"


@celery_app.task(name="category.maintenance.provider_health")
def provider_health() -> dict:
    """Configuration-level health of the active AI provider (no paid live request)."""
    from ai_providers.registry import get_provider

    health = get_provider().health()
    return {"provider": health.provider, "status": health.status, "detail": health.detail}
