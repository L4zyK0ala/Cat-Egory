"""Threats module tasks (queue ``maintenance`` on the core worker, which has internet access)."""
from __future__ import annotations

import logging
from collections.abc import Callable

import httpx
import redis

from common.config import get_settings
from database.session import session_factory
from threats import abuse, octryx, sgb, usom, virustotal

from . import audit
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
            counts = sgb.check(session, sgb.http_fetch(http), limit=limit or settings.sgb_check_batch,
                               max_age_hours=settings.sgb_check_max_age_hours, pause=settings.sgb_check_pause_seconds,
                               commit_each=True, progress=progress)
            counts["usom_listed_after_report"] = usom.mark_listed(session)
            session.commit()
            return counts

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


def run_octryx_sync() -> dict:
    """Pull Octryx's findings: proven ones are listed at once, the rest wait for a person."""
    settings = get_settings()
    token = settings.octryx_token.get_secret_value().strip() if settings.octryx_token else ""
    if not token or not settings.octryx_url:
        return {"disabled": 1}

    def run() -> dict:
        with octryx.client(token) as http:
            try:
                items = octryx.fetch(http, settings.octryx_url)
            except (httpx.HTTPError, ValueError, KeyError) as exc:
                log.warning("octryx sync failed", extra={"error": type(exc).__name__})
                return {"errors": 1}
        with session_factory()() as session, session.begin():
            result = octryx.ingest(session, items, max_auto=settings.octryx_auto_flag_max_per_run)
            for hit in result.flagged:
                audit.record(session, action="threat.auto", target_type="domain", target_id=hit["domain"][:64],
                             after={"source": "octryx", "brand": hit["brand"]})
        return {"flagged": len(result.flagged), "candidates": result.candidates, "known": result.known,
                "invalid": result.invalid}

    counts = _exclusive("octryx-sync", run)
    log.info("octryx sync", extra=counts)
    return counts


@celery_app.task(name="category.threats.octryx_sync", ignore_result=True, soft_time_limit=240, time_limit=300)
def octryx_sync() -> dict:
    return run_octryx_sync()


@celery_app.task(name="category.threats.sgb_check", ignore_result=True, soft_time_limit=240, time_limit=300)
def sgb_check() -> dict | None:
    if not get_settings().sgb_check_enabled:
        return None
    return run_sgb_check()


@celery_app.task(name="category.threats.virustotal_check", ignore_result=True, soft_time_limit=240, time_limit=300)
def virustotal_check() -> dict | None:
    return run_virustotal_check() if virustotal_key() else None


@celery_app.task(name="category.threats.usom_mail", ignore_result=True, soft_time_limit=60, time_limit=90)
def usom_mail(mail_id: int) -> str:
    """Send one USOM report e-mail a person queued from the panel (the API container has no internet)."""
    with httpx.Client(timeout=20, follow_redirects=False) as http, session_factory()() as session:
        return usom.deliver(session, mail_id, http, get_settings())


def run_abuse_drafts(limit: int | None = None) -> dict:
    """Find abuse contacts (RDAP) and write report drafts. Nothing is sent: a person does that from the panel."""
    settings = get_settings()

    def run() -> dict:
        with abuse.client() as http, session_factory()() as session:
            return abuse.draft_due(session, abuse.Rdap(abuse.http_fetch(http)),
                                   limit=limit or settings.abuse_drafts_batch,
                                   repeat_days=settings.abuse_report_repeat_days, site_url=settings.public_site_url)

    counts = _exclusive("abuse-drafts", run)
    log.info("abuse drafts", extra=counts)
    return counts


@celery_app.task(name="category.threats.abuse_drafts", ignore_result=True, soft_time_limit=240, time_limit=300)
def abuse_drafts() -> dict | None:
    return run_abuse_drafts() if get_settings().abuse_drafts_enabled else None


@celery_app.task(name="category.threats.feed_candidates", ignore_result=True, soft_time_limit=120, time_limit=180)
def feed_candidates() -> list[str]:
    """Queue this quarter-hour's share of threat-list hosts for a scan (threats/feeds.py). Candidates only."""
    from threats import feeds

    settings = get_settings()
    if settings.threat_feed_candidates_per_hour <= 0 and settings.threat_feed_global_per_hour <= 0:
        return []
    with session_factory()() as session, session.begin():
        queued = feeds.pick(session, settings)
    if queued:
        log.info("threat-list candidates queued", extra={"count": len(queued)})
    return queued


@celery_app.task(name="category.threats.page_check", ignore_result=True, soft_time_limit=120, time_limit=180)
def page_check(scan_result_id: int) -> str:
    """A scanned page judged by our detector alone, no AI (threats/pages.py): one-page scans and worldwide
    candidates. Runs on the classify / fast queue after the scan was stored."""
    from crawler.storage import S3Store
    from threats.pages import page_check as check

    settings = get_settings()
    with session_factory()() as session, session.begin():
        return check(session, scan_result_id, settings, S3Store(settings))
