"""Classification engine: turn a crawl into a provider input, record what the model claimed, and decide what
(if anything) becomes the domain's current classification.

Rules
-----
* Model output is a *claim*. It is stored in ``ai_predictions`` / ``evidence`` (unverified) no matter what.
* Only the decision engine writes ``domain_categories``, and only for content categories.
* AI never overwrites a human decision; a disagreement opens a review task instead.
* AI security-threat claims never set public risk flags; they always go to a human. A reviewer who confirms
  one creates a ``threat_intelligence`` row (source ``cat-egory-review``).
* Injection attempts, risk claims and low confidence go to a human regardless of the stated confidence.
"""
from __future__ import annotations

import gzip
import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from ai_providers.base import AIProvider
from ai_providers.schemas import ClassificationInput, ProviderResult, RedirectHop, Screenshot
from database.models import (
    AIPrediction,
    AssignmentOrigin,
    ClassificationStatus,
    DataSource,
    Domain,
    DomainCategory,
    Evidence,
    EvidenceType,
    ExtractedContent,
    ReviewStatus,
    ReviewTask,
    ScanResult,
    ThreatIntelligence,
    ThreatType,
)
from database.models import Screenshot as ScreenshotRow
from database.models import ValidationStatus as DBValidationStatus

from .confidence import ConfidencePolicy, Decision
from .taxonomy import Taxonomy

log = logging.getLogger("category.classify")
AI_SOURCE = "cat-egory-ai"
REVIEW_SOURCE = "cat-egory-review"


# ----------------------------------------------------------------------------- input


def build_input(session: Session, scan: ScanResult, store=None, *, use_screenshot: bool = True,
                max_html_bytes: int = 200_000) -> ClassificationInput:
    """Assemble the provider input from a stored scan. Missing artefacts are skipped, not fatal."""
    domain = session.get(Domain, scan.domain_id)
    content = session.scalar(select(ExtractedContent).where(ExtractedContent.scan_result_id == scan.id))
    shot = session.scalar(select(ScreenshotRow).where(ScreenshotRow.scan_result_id == scan.id))
    html = None
    screenshot = None
    if store is not None and content is not None and content.html_object_key:
        try:
            html = gzip.decompress(store.get(content.html_object_key))[:max_html_bytes].decode("utf-8", "replace")
        except Exception:  # noqa: BLE001 - classification can proceed from text alone
            log.warning("html artefact unavailable", extra={"scan_result_id": scan.id})
    if store is not None and use_screenshot and shot is not None:
        try:
            screenshot = Screenshot(store.get(shot.object_key), shot.mime_type)  # type: ignore[arg-type]
        except Exception:  # noqa: BLE001
            log.warning("screenshot artefact unavailable", extra={"scan_result_id": scan.id})
    return ClassificationInput(
        url=scan.requested_url, domain=domain.normalized_domain, final_url=scan.final_url,
        http_status=scan.http_status,
        redirect_chain=[RedirectHop(h.get("url", ""), h.get("status")) for h in scan.redirect_chain or []],
        response_headers=dict(scan.response_headers or {}), title=scan.page_title,
        meta_description=scan.meta_description, open_graph=dict(scan.open_graph or {}),
        text=content.text_excerpt if content else None, html=html, language_hint=scan.language_hint,
        screenshot=screenshot,
    )


# ----------------------------------------------------------------------------- recording


_STATUS_MAP = {s.value: DBValidationStatus(s.value) for s in DBValidationStatus}


def record_prediction(session: Session, domain: Domain, scan: ScanResult | None, result: ProviderResult) -> AIPrediction:
    out = result.output
    risk = list(out.risk_categories) if out else []
    pred = AIPrediction(
        domain_id=domain.id, scan_result_id=scan.id if scan else None, provider=result.provider, model=result.model,
        prompt_version=result.prompt_version, taxonomy_version=result.taxonomy_version,
        validation_status=_STATUS_MAP[result.status.value], validation_errors=result.errors[:20],
        primary_category_id=out.primary_category if out and result.ok else None,
        secondary_category_ids=list(out.secondary_categories) if out and result.ok else [],
        risk_flags=risk if result.ok else [],
        confidence=Decimal(str(round(out.confidence, 3))) if out else None,
        language=out.language if out else None, site_type=out.site_type if out else None,
        description=out.description if out else None,
        requires_human_review=bool(out.requires_human_review) if out else True,
        used_screenshot=result.used_screenshot, raw_response=result.raw_json,
        input_tokens=result.input_tokens, output_tokens=result.output_tokens, latency_ms=result.latency_ms,
        request_id=result.request_id,
    )
    session.add(pred)
    session.flush()
    if out is not None:
        for item in out.evidence:
            session.add(Evidence(ai_prediction_id=pred.id, type=EvidenceType(item.type.value),
                                 observation=item.observation[:500]))
    for signal in result.injection_signals:
        session.add(Evidence(ai_prediction_id=pred.id, type=EvidenceType.SYSTEM,
                             observation=f"page contains text aimed at automated classifiers: {signal!r}"))
    return pred


# ----------------------------------------------------------------------------- applying categories


def current_assignments(session: Session, domain_id: int) -> list[DomainCategory]:
    return list(session.scalars(select(DomainCategory).where(DomainCategory.domain_id == domain_id,
                                                             DomainCategory.valid_to.is_(None))))


def source_id(session: Session, slug: str) -> int | None:
    return session.scalar(select(DataSource.id).where(DataSource.slug == slug))


def apply_categories(session: Session, domain: Domain, *, primary: str, secondary: list[str],
                     confidence: float | None, origin: AssignmentOrigin, taxonomy: Taxonomy,
                     status: ClassificationStatus, prediction_id: int | None = None,
                     user_id: int | None = None, source_slug: str = AI_SOURCE) -> bool:
    """Make (primary, secondary) the current content classification. History rows are closed, not deleted.
    Returns True if anything changed."""
    content = set(taxonomy.assignable_ids("content"))
    wanted = {primary: True} | {c: False for c in secondary if c != primary}
    unknown = [c for c in wanted if c not in content]
    if unknown:
        raise ValueError(f"not assignable content categories: {unknown}")

    now = datetime.now(UTC)
    current = current_assignments(session, domain.id)
    changed = False
    keep: set[str] = set()
    for row in current:
        if wanted.get(row.category_id) == row.is_primary and row.origin == origin:
            keep.add(row.category_id)
            if confidence is not None:
                row.confidence = Decimal(str(round(confidence, 3)))
            continue
        row.valid_to = now
        changed = True
    session.flush()  # closed rows must leave the partial unique indexes before new rows arrive
    src = source_id(session, source_slug)
    conf = Decimal(str(round(confidence, 3))) if confidence is not None else None
    for category_id, is_primary in wanted.items():
        if category_id in keep:
            continue
        session.add(DomainCategory(domain_id=domain.id, category_id=category_id, is_primary=is_primary,
                                   confidence=conf, origin=origin, source_id=src, ai_prediction_id=prediction_id,
                                   assigned_by_id=user_id, taxonomy_version=taxonomy.version, valid_from=now))
        changed = True
    domain.primary_category_id = primary
    domain.secondary_categories = [c for c in wanted if c != primary]
    domain.confidence = conf
    domain.classification_status = status
    domain.updated_at = now
    return changed


def record_threats(session: Session, domain: Domain, risk_ids: list[str], *, source_slug: str = REVIEW_SOURCE,
                   reference: str | None = None) -> int:
    src = source_id(session, source_slug)
    added = 0
    for rid in risk_ids:
        threat = ThreatType(rid)
        row = session.scalar(select(ThreatIntelligence).where(
            ThreatIntelligence.domain_id == domain.id, ThreatIntelligence.threat_type == threat,
            ThreatIntelligence.source_id == src, ThreatIntelligence.url_id.is_(None)))
        if row is None:
            session.add(ThreatIntelligence(domain_id=domain.id, threat_type=threat, source_id=src,
                                           source_reference=reference))
            added += 1
        else:
            row.is_active, row.last_seen = True, datetime.now(UTC)
    return added


def open_review(session: Session, domain: Domain, prediction: AIPrediction | None, reason: str) -> ReviewTask:
    task = session.scalar(select(ReviewTask).where(ReviewTask.domain_id == domain.id,
                                                   ReviewTask.status == ReviewStatus.OPEN))
    if task is None:
        task = ReviewTask(domain_id=domain.id, reason=reason)
        session.add(task)
    task.ai_prediction_id = prediction.id if prediction else task.ai_prediction_id
    task.reason = reason
    session.flush()
    return task


# Reasons that only say "the model was not sure". A later confident (or two-model) classification answers them.
# Risk claims, injection and model-requested reviews stay open for a person even then.
SUPERSEDABLE_REASONS = ("low_confidence", "needs_extra_validation", "model_disagreement")


def supersede_reviews(session: Session, domain: Domain, note: str, *, reasons=SUPERSEDABLE_REASONS) -> int:
    """Close open review tasks whose question a newer classification answered, so nobody later approves the
    outdated prediction attached to them. Returns how many were closed."""
    # The offline mock always asks for review; that request carries no information once a real model answered.
    tasks = list(session.scalars(
        select(ReviewTask).outerjoin(AIPrediction, AIPrediction.id == ReviewTask.ai_prediction_id)
        .where(ReviewTask.domain_id == domain.id, ReviewTask.status == ReviewStatus.OPEN,
               or_(ReviewTask.reason.in_(reasons), AIPrediction.provider == "mock"))))
    for task in tasks:
        task.status, task.decided_at, task.decision_note = ReviewStatus.SKIPPED, datetime.now(UTC), note
    return len(tasks)


# ----------------------------------------------------------------------------- decision


@dataclass
class Outcome:
    action: str  # applied | review | reanalyze | invalid | retry
    reason: str = ""
    prediction_ids: list[int] = field(default_factory=list)
    review_task_id: int | None = None


def _review_reason(result: ProviderResult, decision: Decision) -> str:
    out = result.output
    if result.injection_signals:
        return "prompt_injection"
    if out and out.risk_categories:
        return "risk_claim"
    if out and out.requires_human_review:
        return "model_requested_review"
    return "low_confidence" if decision == Decision.HUMAN_REVIEW else "needs_extra_validation"


def decide(session: Session, domain: Domain, scan: ScanResult | None, result: ProviderResult, *,
           taxonomy: Taxonomy, policy: ConfidencePolicy, data: ClassificationInput | None = None,
           second_provider: AIProvider | None = None, reanalyze_days: int = 1) -> Outcome:
    first = record_prediction(session, domain, scan, result)
    outcome = Outcome(action="invalid", prediction_ids=[first.id])

    if not result.ok:
        if result.status.value == "provider_error":
            outcome.action, outcome.reason = "retry", (result.errors or ["provider_error"])[0]
            return outcome
        outcome.reason = result.status.value
        _mark_reanalyze(domain, reanalyze_days)
        return outcome

    out = result.output
    decision = policy.decide(out.confidence, requires_human_review=out.requires_human_review,
                             has_risk=bool(out.risk_categories), injection_suspected=bool(result.injection_signals))

    human_rows = [r for r in current_assignments(session, domain.id)
                  if r.origin in (AssignmentOrigin.HUMAN, AssignmentOrigin.COMMUNITY)]
    human_primary = next((r.category_id for r in human_rows if r.is_primary), None)

    if decision == Decision.REANALYZE:
        _mark_reanalyze(domain, reanalyze_days)
        outcome.action, outcome.reason = "reanalyze", "low_confidence"
        return outcome

    if human_primary is not None:
        if human_primary == out.primary_category:
            outcome.action, outcome.reason = "applied", "agrees_with_human"  # nothing to change
            return outcome
        task = open_review(session, domain, first, "conflicts_with_human_decision")
        outcome.action, outcome.reason, outcome.review_task_id = "review", task.reason, task.id
        return outcome

    if decision == Decision.EXTRA_VALIDATION and second_provider is not None and data is not None \
            and not out.risk_categories and not result.injection_signals and not out.requires_human_review:
        second = second_provider.classify(data)
        second_pred = record_prediction(session, domain, scan, second)
        outcome.prediction_ids.append(second_pred.id)
        if second.ok and second.output.primary_category == out.primary_category:
            confidence = min(out.confidence, second.output.confidence)
            apply_categories(session, domain, primary=out.primary_category, secondary=out.secondary_categories,
                             confidence=confidence, origin=AssignmentOrigin.AI, taxonomy=taxonomy,
                             status=ClassificationStatus.CLASSIFIED, prediction_id=first.id)
            supersede_reviews(session, domain, f"superseded by prediction {first.id} (two models agree)")
            outcome.action, outcome.reason = "applied", "two_models_agree"
            return outcome
        task = open_review(session, domain, first, "model_disagreement")
        domain.classification_status = _review_status(domain)
        outcome.action, outcome.reason, outcome.review_task_id = "review", task.reason, task.id
        return outcome

    if decision == Decision.AUTO_APPROVE_CANDIDATE:
        apply_categories(session, domain, primary=out.primary_category, secondary=out.secondary_categories,
                         confidence=out.confidence, origin=AssignmentOrigin.AI, taxonomy=taxonomy,
                         status=ClassificationStatus.CLASSIFIED, prediction_id=first.id)
        supersede_reviews(session, domain, f"superseded by prediction {first.id} (high confidence)")
        outcome.action, outcome.reason = "applied", "high_confidence"
        return outcome

    task = open_review(session, domain, first, _review_reason(result, decision))
    domain.classification_status = _review_status(domain)
    outcome.action, outcome.reason, outcome.review_task_id = "review", task.reason, task.id
    return outcome


def _review_status(domain: Domain) -> ClassificationStatus:
    # A domain that already has a public classification keeps it while a re-classification is reviewed.
    if domain.classification_status in (ClassificationStatus.CLASSIFIED, ClassificationStatus.VERIFIED):
        return domain.classification_status
    return ClassificationStatus.NEEDS_REVIEW


def _mark_reanalyze(domain: Domain, days: int) -> None:
    if domain.classification_status not in (ClassificationStatus.CLASSIFIED, ClassificationStatus.VERIFIED):
        domain.classification_status = ClassificationStatus.REANALYZE
    domain.next_scan = datetime.now(UTC) + timedelta(days=days)
