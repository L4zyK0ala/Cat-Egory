"""Celery application. Each workload has its own queue so it can be scaled (or isolated) separately:

* ``crawl``       – browser crawling, runs only in the crawler container (no DB access)
* ``scan``        – scan pipeline (dispatch, DNS pre-check, persist results) on its own worker (worker-scan), so
                    long AI calls on the main worker can never starve the 10-second dispatch
* ``classify``    – AI classification + validation (Phase 4)
* ``ingest``      – imports and third-party source sync (Phase 5)
* ``feeds``       – feed generation (Phase 5)
* ``maintenance`` – health checks, retention, retries
* ``fast``, ``crawl-fast`` – the threat fast lane: scan and classification steps (``fast``, own worker) and crawling
  (``crawl-fast``, read before ``crawl``) of suspected threats, so routine work never delays them
"""
from __future__ import annotations

import logging
import time
from functools import lru_cache

import redis
from celery import Celery
from celery.signals import task_success
from kombu import Queue

from common.config import get_settings

QUEUES = ("crawl", "scan", "classify", "ingest", "feeds", "maintenance", "fast", "crawl-fast")

settings = get_settings()
celery_app = Celery("category", broker=settings.redis_url, backend=settings.redis_url,
                    include=["backend.app.scan_tasks", "backend.app.classify_tasks", "backend.app.feed_tasks",
                             "backend.app.ingest_tasks", "backend.app.discovery", "backend.app.threat_tasks"])
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
        "artifacts-purge": {"task": "category.maintenance.purge_artifacts", "schedule": 3600.0, "options": {"expires": 3000}},
        "scan-schedule": {"task": "category.scan.schedule", "schedule": 300.0},
        "feeds-build": {"task": "category.feeds.build", "schedule": 3600.0},
        "sources-sync": {"task": "category.ingest.sync_due", "schedule": 3600.0},
        "discovery-queue": {"task": "category.discovery.queue", "schedule": 900.0, "options": {"expires": 800}},
        "sgb-check": {"task": "category.threats.sgb_check", "schedule": 3600.0, "options": {"expires": 3000}},
        "octryx-sync": {"task": "category.threats.octryx_sync", "schedule": 3600.0, "options": {"expires": 3000}},
        "virustotal-check": {"task": "category.threats.virustotal_check", "schedule": 900.0,
                             "options": {"expires": 800}},
        "abuse-drafts": {"task": "category.threats.abuse_drafts", "schedule": 3600.0, "options": {"expires": 3000}},
    },
    timezone="UTC",
)


# ----------------------------------------------------------------------------- heartbeats

HEARTBEAT_KEY = "category:task-heartbeat"  # hash: task name -> unix time of its last success
log = logging.getLogger("category.worker")


@lru_cache(maxsize=1)
def _heartbeat_client(url: str) -> redis.Redis:
    return redis.Redis.from_url(url, socket_timeout=1, socket_connect_timeout=1)


@task_success.connect
def record_heartbeat(sender=None, **_kwargs) -> None:
    """Remember when each task last succeeded. The API exports it (category_task_last_success_timestamp_seconds),
    and alerts fire when the 10-second dispatcher stops, i.e. when beat or the worker is down."""
    name = getattr(sender, "name", None)
    if not name:
        return
    try:
        _heartbeat_client(get_settings().redis_url).hset(HEARTBEAT_KEY, name, int(time.time()))
    except redis.RedisError as exc:  # never fail a task because the heartbeat could not be written
        log.debug("heartbeat not recorded", extra={"task": name, "error": type(exc).__name__})


@celery_app.task(name="category.maintenance.ping")
def ping() -> str:
    return "pong"


@celery_app.task(name="category.maintenance.provider_health", soft_time_limit=300, time_limit=320)
def provider_health(probe: bool = False) -> dict:
    """Health of the active AI provider in the worker, where the API key lives. Without ``probe`` it is a
    configuration check (no paid request); the admin API asks for a probe on demand."""
    from ai_providers.registry import report

    return report(probe=probe)
