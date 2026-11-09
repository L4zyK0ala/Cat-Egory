"""Threats module tasks (queue ``maintenance`` on the core worker, which has internet access)."""
from __future__ import annotations

import logging

from common.config import get_settings
from database.session import session_factory
from threats import sgb

from .worker import celery_app

log = logging.getLogger("category.threats")


def run_sgb_check(limit: int | None = None) -> dict:
    settings = get_settings()
    with sgb.client() as http, session_factory()() as session, session.begin():
        counts = sgb.check(session, sgb.http_fetch(http), limit=limit or settings.sgb_check_batch,
                           max_age_hours=settings.sgb_check_max_age_hours, pause=settings.sgb_check_pause_seconds)
    log.info("sgb cross-check", extra=counts)
    return counts


@celery_app.task(name="category.threats.sgb_check", ignore_result=True, soft_time_limit=240, time_limit=300)
def sgb_check() -> dict | None:
    if not get_settings().sgb_check_enabled:
        return None
    return run_sgb_check()
