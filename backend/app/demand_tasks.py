"""Nightly roll-up of demand counts (backend/app/demand.py) on the maintenance queue."""
from __future__ import annotations

import logging

from common.config import get_settings
from database.session import session_factory

from .demand import run
from .worker import celery_app

log = logging.getLogger("category.demand")


@celery_app.task(name="category.maintenance.demand_rollup", ignore_result=True)
def demand_rollup() -> dict[str, int]:
    from .deps import get_redis

    stats = run(session_factory(), get_redis(), get_settings())
    log.info("demand rolled up", extra=stats)
    return stats
