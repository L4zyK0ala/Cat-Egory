"""Scan pipeline on the core worker (has DB access):

    dispatch (beat, every 10 s) ──► prepare (DNS + SSRF pre-check) ──► crawl.run (crawler container)
                                                                         │ link
    reap (beat, every 5 min: stale jobs) ◄──────────────────────── persist (store result, finalise job)

The database is the source of truth: a job is ``queued`` until dispatch marks it ``running`` and publishes it;
results are written by ``persist``. Lost messages are recovered by ``reap``.
"""
from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import dns.exception
import dns.resolver
from sqlalchemy import exists, func, select
from sqlalchemy.orm import Session

from common.config import Settings, get_settings
from crawler.result import INFRA_ERRORS, CrawlResult
from crawler.ssrf import why_blocked
from database.models import (
    ClassificationStatus,
    Domain,
    ExtractedContent,
    JobStatus,
    ScanJob,
    ScanReason,
    ScanResult,
    Screenshot,
    ThreatIntelligence,
)
from database.session import session_factory
from threats.lifecycle import after_scan, ai_answer_needed

from . import audit
from .worker import celery_app

log = logging.getLogger("category.scan")
UNREACHABLE_CODES = {"dns_nxdomain", "dns_no_address", "connection_failed", "robots_unavailable", "timeout",
                     "tls_error", "dns_failure"}


# ----------------------------------------------------------------------------- DNS


def lookup_dns(domain: str, resolver: Any = None) -> tuple[dict, list[str], str | None]:
    """Return (dns_info, addresses, error_code). error_code is None when the domain has usable addresses."""
    resolver = resolver or dns.resolver.Resolver()
    resolver.lifetime = getattr(resolver, "lifetime", 5.0) or 5.0
    info: dict[str, Any] = {"a": [], "aaaa": [], "cname": None, "ns": [], "mx": []}
    timeouts = 0
    for rtype in ("A", "AAAA"):
        try:
            answer = resolver.resolve(domain, rtype)
            info[rtype.lower()] = sorted({r.to_text() for r in answer})
            if str(answer.canonical_name).rstrip(".") != domain:
                info["cname"] = str(answer.canonical_name).rstrip(".")
        except dns.resolver.NXDOMAIN:
            return info, [], "dns_nxdomain"
        except (dns.resolver.NoAnswer, dns.resolver.NoNameservers):
            continue
        except dns.exception.Timeout:
            timeouts += 1
    for rtype, key in (("MX", "mx"), ("NS", "ns")):
        try:
            info[key] = sorted({r.to_text() for r in resolver.resolve(domain, rtype)})[:20]
        except (dns.exception.DNSException, ValueError):
            pass
    addresses = info["a"] + info["aaaa"]
    if not addresses:
        return info, [], "dns_timeout" if timeouts else "dns_no_address"
    return info, addresses, None


# ----------------------------------------------------------------------------- dispatch / reap


def is_fast(priority: int, settings: Settings) -> bool:
    """Threat fast lane: look-alikes from CT logs, Octryx findings, or a scan a person marked urgent."""
    return priority <= settings.fast_lane_max_priority


def claim_jobs(session: Session, settings: Settings) -> list[tuple[str, str, bool]]:
    """Claim queued jobs up to the in-flight limit, most urgent first: (job id, task id, fast lane).
    Fast-lane jobs may also use ``fast_lane_extra_slots`` above the limit, so they never wait for a free slot."""
    running = session.scalar(select(func.count()).select_from(ScanJob).where(ScanJob.status == JobStatus.RUNNING)) or 0
    capacity = settings.max_in_flight_scans - running
    fast_capacity = capacity + settings.fast_lane_extra_slots
    if fast_capacity <= 0:
        return []
    jobs = session.scalars(
        select(ScanJob).where(ScanJob.status == JobStatus.QUEUED)
        .order_by(ScanJob.priority, ScanJob.queued_at).limit(fast_capacity).with_for_update(skip_locked=True)
    ).all()
    claimed = []
    for job in jobs:  # most urgent first, so every fast-lane job comes before the routine ones
        fast = is_fast(job.priority, settings)
        if not fast and len(claimed) >= capacity:
            break
        job.status, job.started_at, job.finished_at = JobStatus.RUNNING, datetime.now(UTC), None
        job.attempts += 1
        job.task_id = uuid.uuid4().hex
        claimed.append((str(job.id), job.task_id, fast))
    return claimed


def reap_stale(session: Session, settings: Settings) -> dict[str, int]:
    cutoff = datetime.now(UTC) - timedelta(minutes=settings.stale_scan_minutes)
    jobs = session.scalars(select(ScanJob).where(ScanJob.status == JobStatus.RUNNING, ScanJob.started_at < cutoff)
                           .with_for_update(skip_locked=True)).all()
    counts = {"requeued": 0, "failed": 0}
    for job in jobs:
        if job.attempts < job.max_attempts:
            job.status, job.task_id, job.started_at, job.error_code = JobStatus.QUEUED, None, None, "stale"
            counts["requeued"] += 1
        else:
            job.status, job.finished_at, job.error_code = JobStatus.FAILED, datetime.now(UTC), "timeout"
            counts["failed"] += 1
    return counts


# ----------------------------------------------------------------------------- re-scan scheduling


def rescan_days(session: Session, domain: Domain, scan: ScanResult, status: JobStatus, settings: Settings) -> int:
    """How long until this domain is scanned again: shorter for risky or changing sites, longer for verified,
    stable ones. (A low-confidence classification shortens it further; see classification.engine.)"""
    if session.scalar(select(ThreatIntelligence.id).where(ThreatIntelligence.domain_id == domain.id,
                                                          ThreatIntelligence.is_active).limit(1)):
        return settings.scan_interval_risk_days
    if status == JobStatus.FAILED:
        return settings.scan_interval_unreachable_days
    if status == JobStatus.BLOCKED:
        return settings.scan_interval_blocked_days
    previous = session.scalar(select(ScanResult).where(ScanResult.domain_id == domain.id, ScanResult.id != scan.id,
                                                       ScanResult.http_status.is_not(None))
                              .order_by(ScanResult.created_at.desc()).limit(1))
    if previous is not None and (previous.page_title != scan.page_title
                                 or _host(previous.final_url) != _host(scan.final_url)):
        return settings.scan_interval_changed_days
    if domain.classification_status == ClassificationStatus.VERIFIED:
        return settings.scan_interval_verified_days
    return settings.scan_interval_days


def _host(url: str | None) -> str | None:
    from urllib.parse import urlsplit

    return urlsplit(url).hostname if url else None


def schedule_due(session: Session, settings: Settings, now: datetime | None = None) -> int:
    """Queue scans for active domains whose ``next_scan`` has passed (oldest first, bounded batch)."""
    from .services.domains import queue_scans

    now = now or datetime.now(UTC)
    open_job = exists(select(ScanJob.id).where(ScanJob.domain_id == Domain.id,
                                               ScanJob.status.in_([JobStatus.QUEUED, JobStatus.RUNNING])))
    ids = list(session.scalars(select(Domain.id).where(Domain.is_active, Domain.next_scan.is_not(None),
                                                       Domain.next_scan <= now, ~open_job)
                               .order_by(Domain.next_scan).limit(settings.schedule_batch_size)))
    return queue_scans(session, ids, reason=ScanReason.SCHEDULED, priority=7, requested_by_id=None) if ids else 0


# ----------------------------------------------------------------------------- persist


def store_result(session: Session, result: CrawlResult, job_id: str, settings: Settings) -> str:
    """Write a crawl result and finalise (or re-queue) its job. Returns the outcome."""
    job = session.get(ScanJob, uuid.UUID(job_id), with_for_update=True)
    if job is None or job.status != JobStatus.RUNNING:
        log.info("result ignored: job not running", extra={"job_id": job_id})
        return "ignored"
    domain = session.get(Domain, job.domain_id, with_for_update=True)
    now = datetime.now(UTC)

    if not result.ok and result.retryable and job.attempts < job.max_attempts:
        job.status, job.task_id, job.started_at = JobStatus.QUEUED, None, None
        job.error_code, job.error_detail = result.error_code, result.error_detail
        return "retry"

    scan = ScanResult(
        scan_job_id=job.id, domain_id=domain.id, requested_url=result.requested_url, final_url=result.final_url,
        http_status=result.http_status, redirect_chain=result.redirect_chain, resolved_ips=result.resolved_ips,
        dns=result.dns, tls=result.tls, response_headers=result.response_headers, content_type=result.content_type,
        content_length=result.content_length, page_title=result.page_title, meta_description=result.meta_description,
        open_graph=result.open_graph, language_hint=result.language_hint, robots_allowed=result.robots_allowed,
        blocked_reason=result.blocked_reason, timings=result.timings | {"sandboxed": result.sandboxed},
    )
    session.add(scan)
    session.flush()
    retention = now + timedelta(days=settings.artifact_retention_days)
    if result.screenshot:
        a = result.screenshot
        session.add(Screenshot(scan_result_id=scan.id, object_key=a.key, sha256=a.sha256, mime_type=a.mime_type,
                               width=a.width or 0, height=a.height or 0, size_bytes=a.size, retention_until=retention))
    if result.html or result.text:
        session.add(ExtractedContent(
            scan_result_id=scan.id, html_object_key=result.html.key if result.html else None,
            html_sha256=result.html.sha256 if result.html else None,
            html_size_bytes=result.html.size if result.html else None,
            text_object_key=result.text.key if result.text else None, text_excerpt=result.text_excerpt,
            word_count=result.word_count, link_count=result.link_count, retention_until=retention))

    if result.ok:
        job.status = JobStatus.SUCCEEDED
    elif result.blocked_reason:
        job.status = JobStatus.BLOCKED
    else:
        job.status = JobStatus.FAILED
    job.finished_at = now
    job.error_code = result.blocked_reason or result.error_code
    job.error_detail = result.error_detail

    # before the rescan interval below, which depends on whether active threats remain
    if change := after_scan(session, domain, result, expire_days=settings.threat_expire_days, now=now, scan_id=scan.id,
                            change_cooldown_days=settings.threat_change_cooldown_days):
        audit.record(session, action=f"threat.{change.action}", target_type="domain",
                     target_id=domain.normalized_domain,
                     after={"types": change.types, "last_seen": change.last_seen, "reason": change.reason})
        log.info("threat lifecycle", extra={"domain": domain.normalized_domain, "action": change.action})
    domain.last_scanned = now
    if job.status == JobStatus.FAILED and result.error_code in INFRA_ERRORS:
        domain.next_scan = now + timedelta(hours=settings.scan_retry_infra_hours)  # our fault, not the site's
    else:
        domain.next_scan = now + timedelta(days=rescan_days(session, domain, scan, job.status, settings))
    domain.scan_count += 1
    if job.status == JobStatus.FAILED and result.error_code in UNREACHABLE_CODES \
            and domain.classification_status == ClassificationStatus.UNCLASSIFIED:
        domain.classification_status = ClassificationStatus.UNREACHABLE
    elif result.ok and domain.classification_status == ClassificationStatus.UNREACHABLE:
        domain.classification_status = ClassificationStatus.UNCLASSIFIED
    if job.status in (JobStatus.FAILED, JobStatus.BLOCKED) and result.resolved_ips \
            and result.blocked_reason != "resolves_to_blocked_ip":
        # A live domain we could not read: its suffix may still say what it is (only public bodies get .gov.tr).
        from classification.registry_rules import apply_rule

        if rule := apply_rule(session, domain):
            log.info("registry rule applied", extra={"domain": domain.normalized_domain, "suffix": rule.suffix})
    return job.status.value if hasattr(job.status, "value") else str(job.status)


def precheck(session: Session, job_id: str, settings: Settings, resolver: Any = None) -> dict | None:
    """DNS + SSRF check before crawling. Returns the crawl payload, or None if the job was finalised here."""
    job = session.get(ScanJob, uuid.UUID(job_id))
    if job is None or job.status != JobStatus.RUNNING:
        return None
    domain = session.get(Domain, job.domain_id).normalized_domain
    info, addresses, error = lookup_dns(domain, resolver)
    host = domain
    if error == "dns_no_address" and not domain.startswith("www."):
        # Many sites answer only on www (the bare name has no A/AAAA record): read the site there instead.
        www_info, www_addresses, www_error = lookup_dns(f"www.{domain}", resolver)
        if www_error is None:
            host, addresses, error = f"www.{domain}", www_addresses, None
            info = info | {"a": www_info["a"], "aaaa": www_info["aaaa"], "host": host}
    result = CrawlResult(job_id=job_id, domain=domain, requested_url=f"https://{host}/", dns=info,
                         resolved_ips=addresses)
    if error:
        result.error_code = error
        store_result(session, result, job_id, settings)
        return None
    blocked = [a for a in addresses if why_blocked(a)]
    if blocked:
        result.blocked_reason = "resolves_to_blocked_ip"
        result.error_detail = f"{len(blocked)} non-public address(es)"
        store_result(session, result, job_id, settings)
        return None
    return {"job_id": job_id, "domain": domain, "host": host, "dns": info, "resolved_ips": addresses}


# ----------------------------------------------------------------------------- celery tasks


@celery_app.task(name="category.scan.dispatch", ignore_result=True)
def dispatch() -> int:
    settings = get_settings()
    with session_factory()() as session, session.begin():
        claimed = claim_jobs(session, settings)
    for job_id, task_id, fast in claimed:
        if fast:
            prepare.apply_async(args=[job_id], kwargs={"fast": True}, task_id=task_id, queue="fast")
        else:
            prepare.apply_async(args=[job_id], task_id=task_id)
    return len(claimed)


@celery_app.task(name="category.scan.prepare", ignore_result=True)
def prepare(job_id: str, fast: bool = False) -> None:
    settings = get_settings()
    with session_factory()() as session, session.begin():
        payload = precheck(session, job_id, settings)
    if payload is None:
        return
    if fast:
        celery_app.send_task("category.crawl.run", args=[payload], queue="crawl-fast",
                             link=persist.s(job_id, fast=True).set(queue="fast"))
    else:
        celery_app.send_task("category.crawl.run", args=[payload], queue="crawl", link=persist.s(job_id))


@celery_app.task(name="category.scan.persist", ignore_result=True)
def persist(result: dict, job_id: str, fast: bool = False) -> str:
    settings = get_settings()
    with session_factory()() as session, session.begin():
        outcome = store_result(session, CrawlResult.from_dict(result), job_id, settings)
        scan_result_id = suggest_id = None
        if outcome == "succeeded":
            from .classify_tasks import scan_result_for_job

            scan_result_id = scan_result_for_job(session, job_id)
            if scan_result_id is not None and not ai_answer_needed(
                    session, session.get(ScanResult, scan_result_id).domain_id,
                    every_days=settings.threat_reclassify_days, now=datetime.now(UTC)):
                scan_result_id = None
        elif outcome == "blocked" and result.get("blocked_reason") == "robots_disallowed":
            from classification.engine import name_suggestion_due

            job = session.get(ScanJob, uuid.UUID(job_id))
            domain = session.get(Domain, job.domain_id)
            if name_suggestion_due(session, domain):
                suggest_id = domain.id
    log.info("scan persisted", extra={"job_id": job_id, "outcome": outcome})
    if suggest_id is not None:  # closed to bots: a suggestion from the name only, for a person to confirm
        celery_app.send_task("category.classify.name_only", args=[suggest_id], queue="fast" if fast else "classify")
    if scan_result_id is not None:
        celery_app.send_task("category.classify.scan", args=[scan_result_id], queue="fast" if fast else "classify")
    return outcome


def purge_artifacts(session: Session, store, *, now: datetime | None = None, limit: int = 500) -> int:
    """Delete screenshots and page content past ``retention_until``: objects first, then their rows (a failure
    leaves the row, and deleting an object twice is harmless). Returns how many rows went."""
    now = now or datetime.now(UTC)
    purged = 0
    for shot in session.scalars(select(Screenshot).where(Screenshot.retention_until < now).limit(limit)):
        store.delete(shot.object_key)
        session.delete(shot)
        purged += 1
    for content in session.scalars(select(ExtractedContent).where(ExtractedContent.retention_until < now).limit(limit)):
        for key in (content.html_object_key, content.text_object_key):
            if key:
                store.delete(key)
        session.delete(content)
        purged += 1
    return purged


@celery_app.task(name="category.maintenance.purge_artifacts", ignore_result=True, soft_time_limit=600, time_limit=660)
def purge_expired() -> int:
    from crawler.storage import S3Store

    settings = get_settings()
    with session_factory()() as session, session.begin():
        purged = purge_artifacts(session, S3Store(settings))
    log.info("artifacts purged", extra={"rows": purged})
    return purged


@celery_app.task(name="category.scan.schedule", ignore_result=True)
def schedule() -> int:
    with session_factory()() as session, session.begin():
        queued = schedule_due(session, get_settings())
    if queued:
        log.info("scheduled re-scans", extra={"queued": queued})
    return queued


@celery_app.task(name="category.scan.reap", ignore_result=True)
def reap() -> dict[str, int]:
    with session_factory()() as session, session.begin():
        return reap_stale(session, get_settings())
