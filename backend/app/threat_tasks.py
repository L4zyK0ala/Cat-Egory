"""Threats module tasks (queue ``maintenance`` on the core worker, which has internet access)."""
from __future__ import annotations

import logging
from collections.abc import Callable

import redis

from common.config import get_settings
from database.session import session_factory
from threats import sgb, virustotal

from .worker import celery_app

log = logging.getLogger("category.threats")

IDLE = {"checked": 0, "listed": 0, "errors": 0}


def _exclusive(name: str, run: Callable[[], dict]) -> dict:
    """One run at a time (the scheduled task and the CLI would otherwise ask about the same domains)."""
    lock = redis.Redis.from_url(get_settings().redis_url).lock(f"category:lock:{name}", timeout=900, blocking=False)
    if not lock.acquire():
        return {**IDLE, "busy": 1}
    try:
        return run()
    finally:
        try:
            lock.release()
        except redis.exceptions.LockError:  # expired meanwhile: nothing to release
            pass


def run_sgb_check(limit: int | None = None, progress: Callable[[str, list[dict]], None] | None = None) -> dict:
    """Each answer is committed at once."""
    settings = get_settings()

    def run() -> dict:
        with sgb.client() as http, session_factory()() as session:
            return sgb.check(session, sgb.http_fetch(http), limit=limit or settings.sgb_check_batch,
                             max_age_hours=settings.sgb_check_max_age_hours, pause=settings.sgb_check_pause_seconds,
                             commit_each=True, progress=progress)

    counts = _exclusive("sgb-check", run)
    log.info("sgb cross-check", extra=counts)
    return counts


def virustotal_key() -> str:
    key = get_settings().virustotal_api_key
    return key.get_secret_value().strip() if key else ""


def run_virustotal_check(limit: int | None = None,
                         progress: Callable[[str, list[dict]], None] | None = None) -> dict:
    """Domain lookups only (see threats/virustotal.py). Each answer is committed at once."""
    settings, key = get_settings(), virustotal_key()
    if not key:
        return {**IDLE, "disabled": 1}

    def run() -> dict:
        with virustotal.client(key) as http, session_factory()() as session:
            return virustotal.check(session, virustotal.http_fetch(http),
                                    limit=limit or settings.virustotal_check_batch,
                                    max_age_hours=settings.virustotal_check_max_age_hours,
                                    pause=settings.virustotal_check_pause_seconds, commit_each=True, progress=progress)

    counts = _exclusive("virustotal-check", run)
    log.info("virustotal cross-check", extra=counts)
    return counts


@celery_app.task(name="category.threats.sgb_check", ignore_result=True, soft_time_limit=240, time_limit=300)
def sgb_check() -> dict | None:
    if not get_settings().sgb_check_enabled:
        return None
    return run_sgb_check()


@celery_app.task(name="category.threats.virustotal_check", ignore_result=True, soft_time_limit=240, time_limit=300)
def virustotal_check() -> dict | None:
    return run_virustotal_check() if virustotal_key() else None
