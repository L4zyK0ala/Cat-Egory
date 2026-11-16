"""Re-scans of pages that have not changed keep their AI answer instead of asking the model again.

Most sites look the same a month later, and once the free period of the AI provider ends every answer costs credits.
A scheduled re-scan may keep the domain's current answer when all of this holds; anything else asks the model:

* the scan is a scheduled one (manual, community, import and admin re-scans always ask),
* the domain is published (classified or verified) and has no active threat record, is not a look-alike candidate,
* the newest AI answer about the domain is valid, confident (auto-publish level), asked for no person, claims no risk,
  found no injection attempt, gave the domain's current main category and is younger than
  ``reclassify_unchanged_days`` (so prompt and model improvements still reach old answers),
* the page behind that answer and the new page have the same text fingerprint, title and final host.

A low-confidence answer that is waiting for re-analysis therefore always reaches the model again, and so does a page
that was only compared with an older crawler that did not record fingerprints.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from urllib.parse import urlsplit

from sqlalchemy import select
from sqlalchemy.orm import Session

from database.models import (
    AIPrediction,
    ClassificationStatus,
    Domain,
    ScanJob,
    ScanReason,
    ScanResult,
    ThreatIntelligence,
    ValidationStatus,
)

from .confidence import ConfidencePolicy

PUBLISHED = (ClassificationStatus.CLASSIFIED, ClassificationStatus.VERIFIED)


def _host(url: str | None) -> str | None:
    return urlsplit(url).hostname if url else None


def kept_answer(session: Session, scan: ScanResult, job: ScanJob, domain: Domain, *, policy: ConfidencePolicy,
                every_days: int, now: datetime) -> AIPrediction | None:
    """The AI answer this scan keeps without asking the model, or None when the model has to be asked."""
    from .engine import lookalike_reason, prediction_has_injection

    if every_days <= 0 or job.reason != ScanReason.SCHEDULED or not scan.content_fingerprint:
        return None
    if domain.classification_status not in PUBLISHED or domain.primary_category_id is None:
        return None
    last = session.scalar(select(AIPrediction).where(AIPrediction.domain_id == domain.id)
                          .order_by(AIPrediction.created_at.desc(), AIPrediction.id.desc()).limit(1))
    if (last is None or last.validation_status != ValidationStatus.VALID or last.scan_result_id is None
            or last.created_at < now - timedelta(days=every_days)
            or last.confidence is None or float(last.confidence) < policy.auto_approve
            or last.requires_human_review or last.risk_flags
            or last.primary_category_id != domain.primary_category_id):
        return None
    seen = session.get(ScanResult, last.scan_result_id)
    if (seen is None or seen.content_fingerprint != scan.content_fingerprint
            or (seen.page_title or "") != (scan.page_title or "") or _host(seen.final_url) != _host(scan.final_url)):
        return None
    if session.scalar(select(ThreatIntelligence.id).where(ThreatIntelligence.domain_id == domain.id,
                                                          ThreatIntelligence.is_active).limit(1)) is not None:
        return None
    if prediction_has_injection(session, last.id) or lookalike_reason(session, domain) is not None:
        return None
    return last
