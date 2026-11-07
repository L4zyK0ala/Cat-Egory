"""Scheduled third-party source synchronisation (queue ``ingest``)."""
from __future__ import annotations

import logging

from database.session import session_factory
from integrations.engine import SourceError, catalogue, due_sources, sync

from .worker import celery_app

log = logging.getLogger("category.ingest")


@celery_app.task(name="category.ingest.sync_due", ignore_result=True, soft_time_limit=3000, time_limit=3300)
def sync_due() -> list[str]:
    with session_factory()() as session:
        slugs = due_sources(session)
    entries = catalogue()
    done = []
    for slug in slugs:
        try:
            with session_factory()() as session, session.begin():
                result = sync(session, slug, entry=entries.get(slug, {}))
            log.info("source synced", extra={"source": slug, "entries": result.entries, "errors": result.errors})
            done.append(slug)
        except SourceError as exc:
            log.warning("source sync refused", extra={"source": slug, "code": exc.code})
    return done
