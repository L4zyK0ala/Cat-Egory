"""Periodic feed regeneration (queue ``feeds``)."""
from __future__ import annotations

import logging

from classification.taxonomy import default_taxonomy
from common.config import get_settings
from database.session import session_factory
from feeds.builder import build

from .worker import celery_app

log = logging.getLogger("category.feeds")


@celery_app.task(name="category.feeds.build", ignore_result=True, soft_time_limit=1800, time_limit=1900)
def build_feeds() -> int:
    from crawler.storage import S3Store

    store = S3Store(get_settings())
    with session_factory()() as session, session.begin():
        releases = build(session, store, default_taxonomy())
    log.info("feeds built", extra={"new_releases": len(releases)})
    return len(releases)
