"""Classification on the core worker (queue ``classify``): runs after a scan succeeded."""
from __future__ import annotations

import logging

from sqlalchemy import select
from sqlalchemy.orm import Session

from ai_providers.base import AIProvider
from ai_providers.registry import get_provider, get_second_opinion
from classification.confidence import ConfidencePolicy
from classification.engine import Outcome, build_input, decide
from classification.taxonomy import default_taxonomy, load_taxonomy
from common.config import Settings, get_settings
from database.models import Domain, ScanResult
from database.session import session_factory

from .worker import celery_app

log = logging.getLogger("category.classify")


def policy_from(settings: Settings) -> ConfidencePolicy:
    return ConfidencePolicy(auto_approve=settings.confidence_auto_approve,
                            extra_validation=settings.confidence_extra_validation,
                            human_review=settings.confidence_human_review)


def classify_scan(session: Session, scan_result_id: int, settings: Settings, *, provider: AIProvider,
                  second: AIProvider | None = None, store=None) -> Outcome | None:
    scan = session.get(ScanResult, scan_result_id)
    if scan is None:
        return None
    domain = session.get(Domain, scan.domain_id, with_for_update=True)
    taxonomy = load_taxonomy(settings.taxonomy_path) if settings.taxonomy_path else default_taxonomy()
    data = build_input(session, scan, store, use_screenshot=settings.classify_use_screenshot)
    result = provider.classify(data)
    return decide(session, domain, scan, result, taxonomy=taxonomy, policy=policy_from(settings), data=data,
                  second_provider=second, reanalyze_days=settings.reanalyze_days)


@celery_app.task(name="category.classify.scan", bind=True, ignore_result=True, max_retries=3,
                 soft_time_limit=600, time_limit=660)
def classify(self, scan_result_id: int) -> str | None:
    from crawler.storage import S3Store

    settings = get_settings()
    with session_factory()() as session, session.begin():
        outcome = classify_scan(session, scan_result_id, settings, provider=get_provider(settings=settings),
                                second=get_second_opinion(settings), store=S3Store(settings))
    if outcome is None:
        return None
    log.info("classified", extra={"scan_result_id": scan_result_id, "action": outcome.action, "reason": outcome.reason})
    if outcome.action == "retry":  # provider unavailable: the prediction row records the error; try again later
        raise self.retry(countdown=300 * (self.request.retries + 1))
    return outcome.action


def scan_result_for_job(session: Session, job_id: str) -> int | None:
    import uuid

    return session.scalar(select(ScanResult.id).where(ScanResult.scan_job_id == uuid.UUID(job_id)))
