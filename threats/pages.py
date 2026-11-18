"""One-page scans and AI-free checks of threat candidates (THREATS.md, "Worldwide candidates and one-page scans").

Two things that keep threat-list candidates cheap and close to the evidence:

* **One page, not the home page.** A phishing list such as OpenPhish names the exact link of the phishing page,
  often deep inside a hacked, otherwise honest site. ``queue_page`` scans that page (query string and fragment
  dropped: they often carry a victim's address or a token) as a scan of its own: ``ScanJob.url_id`` /
  ``ScanResult.url_id``. Such a scan never stands for the domain: no category, no re-scan schedule, no threat
  lifecycle (backend/app/scan_tasks.py).
* **No AI for what only needs the detector.** One-page scans, and the home pages of worldwide candidates (a host
  only a phishing list brought in, nothing Turkish about it), are not sent to the AI model: ``page_check`` runs our
  page-proof detector (threats/detector.py) on them. A person sees such a candidate only when it matters: the page
  asks for a secret or a phone number, or the host is Turkish-related (relevance ≥ 1, as before).
"""
from __future__ import annotations

import logging
import uuid
from urllib.parse import urlsplit, urlunsplit

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from common.config import Settings
from common.domains import DomainError, normalize_url
from database.models import (
    DataSource,
    Domain,
    DomainSourceRecord,
    JobStatus,
    ScanJob,
    ScanReason,
    ScanResult,
    SourceKind,
    ThreatFeedEntry,
)

log = logging.getLogger("category.threats.pages")


def clean_url(raw: str | None) -> str | None:
    """The link to scan: http(s) only, normalised, without query string and fragment. None if unusable."""
    if not raw or "://" not in raw:
        return None
    try:
        normalized = normalize_url(raw)
    except DomainError:
        return None
    parts = urlsplit(normalized.url)
    if parts.scheme not in ("http", "https"):
        return None
    return urlunsplit((parts.scheme, parts.netloc, parts.path or "/", "", ""))


def queue_page(session: Session, domain: Domain, raw_url: str, *, priority: int) -> bool:
    """Queue a scan of one page of ``domain`` (which must exist). False when the link is unusable, is just the home
    page (a normal scan then), or the domain already has an open scan."""
    from .records import url_row

    url = clean_url(raw_url)
    if url is None or urlsplit(url).path in ("", "/"):
        return False
    row = url_row(session, url)
    if row is None or row.domain_id != domain.id:
        return False
    from backend.app.services.domains import OPEN_JOB_WHERE

    stmt = insert(ScanJob).values(id=uuid.uuid4(), domain_id=domain.id, url_id=row.id, reason=ScanReason.DISCOVERY,
                                  priority=priority, status=JobStatus.QUEUED) \
        .on_conflict_do_nothing(index_elements=["domain_id"], index_where=OPEN_JOB_WHERE).returning(ScanJob.id)
    return session.execute(stmt).first() is not None


def relevance_of(session: Session, host: str) -> int | None:
    """How Türkiye-related the lists that brought ``host`` in judged it (threats/feeds.py), or None if no list did."""
    return session.scalar(select(func.max(ThreatFeedEntry.relevance)).where(
        ThreatFeedEntry.host == host, ThreatFeedEntry.picked_at.is_not(None)))


def worldwide_only(session: Session, domain: Domain) -> bool:
    """A host only threat lists brought in, with nothing Turkish about it: its home page needs no AI answer."""
    if relevance_of(session, domain.normalized_domain) != 0:
        return False
    other = session.scalar(select(DomainSourceRecord.id).join(DataSource, DataSource.id == DomainSourceRecord.source_id)
                           .where(DomainSourceRecord.domain_id == domain.id, DataSource.kind != SourceKind.THREAT_FEED)
                           .limit(1))
    return other is None


def detector_only(session: Session, scan: ScanResult) -> bool:
    """Whether a successful scan goes to ``page_check`` instead of the AI classification."""
    if scan.url_id is not None:
        return True
    domain = session.get(Domain, scan.domain_id)
    return domain is not None and worldwide_only(session, domain)


def page_check(session: Session, scan_result_id: int, settings: Settings, store=None) -> str:
    """Judge a scanned page with the detector alone. Opens a threat-candidate review when a person should look:
    the page asks for a secret or a phone number, or the host is Turkish-related. Returns what happened."""
    from classification.engine import build_input, open_review
    from threats.detector import asks, check, page_of

    from .feeds import picked_reason

    scan = session.get(ScanResult, scan_result_id)
    if scan is None:
        return "missing"
    domain = session.get(Domain, scan.domain_id, with_for_update=True)
    data = build_input(session, scan, store, use_screenshot=False)
    reason = picked_reason(session, domain)
    page = page_of(session, domain, scan, data)
    if reason is not None and (asks(page) or (relevance_of(session, domain.normalized_domain) or 0) >= 1):
        open_review(session, domain, None, reason)  # before the check: a flag it adds closes the review itself
        outcome = "review"
    else:
        outcome = "measured"
    try:
        with session.begin_nested():
            verdict = check(session, domain, scan, data, settings)
    except Exception:  # noqa: BLE001 - a detector failure leaves the review (if any) to a person
        log.warning("threat detector failed", extra={"scan_result_id": scan.id}, exc_info=True)
        return outcome
    if verdict is not None and verdict.action == "flagged":
        outcome = "flagged"
    log.info("page checked", extra={"domain": domain.normalized_domain, "scan_result_id": scan.id,
                                    "page": scan.url_id is not None, "outcome": outcome})
    return outcome
