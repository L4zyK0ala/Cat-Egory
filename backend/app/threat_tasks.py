"""Threats module tasks (queue ``maintenance`` on the core worker, which has internet access)."""
from __future__ import annotations

import logging
from collections.abc import Callable

import redis

from common.config import get_settings
from database.session import session_factory
from threats import sgb

from .worker import celery_app

log = logging.getLogger("category.threats")


LOCK = "category:lock:sgb-check"


def run_sgb_check(limit: int | None = None, progress: Callable[[str, list[dict]], None] | None = None) -> dict:
    """One run at a time (the hourly task and the CLI would otherwise ask about the same domains). Each answer
    is committed at once."""
    settings = get_settings()
    lock = redis.Redis.from_url(settings.redis_url).lock(LOCK, timeout=900, blocking=False)
    if not lock.acquire():
        return {"checked": 0, "listed": 0, "errors": 0, "busy": 1}
    try:
        with sgb.client() as http, session_factory()() as session:
            counts = sgb.check(session, sgb.http_fetch(http), limit=limit or settings.sgb_check_batch,
                               max_age_hours=settings.sgb_check_max_age_hours,
                               pause=settings.sgb_check_pause_seconds, commit_each=True, progress=progress)
    finally:
        try:
            lock.release()
        except redis.exceptions.LockError:  # expired meanwhile: nothing to release
            pass
    log.info("sgb cross-check", extra=counts)
    return counts


@celery_app.task(name="category.threats.sgb_check", ignore_result=True, soft_time_limit=240, time_limit=300)
def sgb_check() -> dict | None:
    if not get_settings().sgb_check_enabled:
        return None
    return run_sgb_check()
