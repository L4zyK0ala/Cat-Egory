"""Human review (Phase 4), AI provider status and calibration. Every decision is audit-logged."""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal

from fastapi import APIRouter, Depends, Query, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy import exists, func, select
from sqlalchemy.orm import Session

from ai_providers.registry import available
from classification import calibration
from classification.engine import REVIEW_SOURCE, apply_categories, open_review, record_threats
from classification.taxonomy import default_taxonomy
from common.config import get_settings
from common.domains import DomainError, normalize_domain
from database.models import (
    AIPrediction,
    AssignmentOrigin,
    ClassificationStatus,
    Domain,
    DomainCategory,
    Evidence,
    ReviewStatus,
    ReviewTask,
    ScanResult,
)
from database.models import Screenshot as ScreenshotRow
from database.session import get_session

from .. import audit
from ..deps import Principal, rate_limit, require
from ..errors import ApiError

router = APIRouter(prefix="/api/v1/admin", tags=["review"], dependencies=[Depends(rate_limit("write"))])


def get_store():
    from crawler.storage import S3Store

    return S3Store(get_settings())


class EvidenceOut(BaseModel):
    type: str
    observation: str
    verified: bool


class PredictionOut(BaseModel):
    id: int
    provider: str
    model: str
    primary_category: str | None
    secondary_categories: list[str]
    risk_categories: list[str]
    confidence: float | None
    description: str | None = Field(description="model claim, not verified")
    validation_status: str
    evidence: list[EvidenceOut]


class ReviewOut(BaseModel):
    id: int
    domain: str
    reason: str
    status: str
    created_at: datetime
    current_primary: str | None
    current_secondary: list[str]
    page_title: str | None
    final_url: str | None
    http_status: int | None
    has_screenshot: bool
    prediction: PredictionOut | None


class ReviewPage(BaseModel):
    items: list[ReviewOut]
    next_cursor: int | None


class DecisionIn(BaseModel):
    action: Literal["approve", "set", "reject"] = Field(
        description="approve = accept the model's categories; set = use the categories given here; "
                    "reject = keep the current state and close the task")
    primary: str | None = None
    secondary: list[str] = Field(default_factory=list, max_length=5)
    risk: list[str] = Field(default_factory=list, description="confirmed security-threat categories")
    note: str | None = Field(default=None, max_length=2000)


def _prediction(session: Session, prediction_id: int | None) -> PredictionOut | None:
    if prediction_id is None:
        return None
    p = session.get(AIPrediction, prediction_id)
    if p is None:
        return None
    evidence = session.scalars(select(Evidence).where(Evidence.ai_prediction_id == p.id).order_by(Evidence.id))
    return PredictionOut(
        id=p.id, provider=p.provider, model=p.model, primary_category=p.primary_category_id,
        secondary_categories=p.secondary_category_ids, risk_categories=p.risk_flags,
        confidence=float(p.confidence) if p.confidence is not None else None, description=p.description,
        validation_status=str(p.validation_status),
        evidence=[EvidenceOut(type=str(e.type), observation=e.observation, verified=e.verified) for e in evidence])


def _latest_scan(session: Session, domain_id: int) -> ScanResult | None:
    return session.scalar(select(ScanResult).where(ScanResult.domain_id == domain_id)
                          .order_by(ScanResult.created_at.desc()).limit(1))


def _review_out(session: Session, task: ReviewTask) -> ReviewOut:
    domain = session.get(Domain, task.domain_id)
    scan = _latest_scan(session, domain.id)
    shot = session.scalar(select(ScreenshotRow.id).where(ScreenshotRow.scan_result_id == scan.id)) if scan else None
    return ReviewOut(
        id=task.id, domain=domain.normalized_domain, reason=task.reason, status=str(task.status),
        created_at=task.created_at, current_primary=domain.primary_category_id,
        current_secondary=domain.secondary_categories, page_title=scan.page_title if scan else None,
        final_url=scan.final_url if scan else None, http_status=scan.http_status if scan else None,
        has_screenshot=shot is not None, prediction=_prediction(session, task.ai_prediction_id))


@router.get("/reviews", response_model=ReviewPage, summary="Review queue (oldest first)")
def list_reviews(status: Literal["open", "done", "skipped"] = "open", limit: int = Query(20, ge=1, le=100),
                 cursor: int | None = None, _: Principal = Depends(require("reviews:write")),
                 session: Session = Depends(get_session)) -> ReviewPage:
    stmt = select(ReviewTask).where(ReviewTask.status == status).order_by(ReviewTask.id).limit(limit + 1)
    if cursor:
        stmt = stmt.where(ReviewTask.id > cursor)
    tasks = list(session.scalars(stmt))
    next_cursor = tasks[limit - 1].id if len(tasks) > limit else None
    return ReviewPage(items=[_review_out(session, t) for t in tasks[:limit]], next_cursor=next_cursor)


class SampleIn(BaseModel):
    n: int = Field(20, ge=1, le=50, description="how many published AI classifications to add for verification")


@router.post("/reviews/sample", summary="Queue a random sample of published AI classifications for human verification")
def sample_for_verification(body: SampleIn, request: Request, principal: Principal = Depends(require("reviews:write")),
                            session: Session = Depends(get_session)) -> dict:
    """Confident AI answers are published without a person. Verifying a random sample of them turns them into
    human-verified data and measures the model's real accuracy (calibration report)."""
    open_task = exists(select(ReviewTask.id).where(ReviewTask.domain_id == Domain.id,
                                                   ReviewTask.status == ReviewStatus.OPEN))
    rows = session.execute(
        select(Domain, DomainCategory.ai_prediction_id)
        .join(DomainCategory, DomainCategory.domain_id == Domain.id)
        .where(Domain.is_active, Domain.classification_status == ClassificationStatus.CLASSIFIED,
               DomainCategory.valid_to.is_(None), DomainCategory.is_primary,
               DomainCategory.origin == AssignmentOrigin.AI, DomainCategory.ai_prediction_id.is_not(None), ~open_task)
        .order_by(func.random()).limit(body.n)).all()
    for domain, prediction_id in rows:
        open_review(session, domain, session.get(AIPrediction, prediction_id), "verification_sample")
    audit.record(session, action="review.sample", target_type="review", after={"created": len(rows)},
                 principal=principal, request_id=getattr(request.state, "request_id", None))
    session.commit()
    return {"created": len(rows)}


@router.get("/reviews/{task_id}", response_model=ReviewOut, summary="One review task")
def get_review(task_id: int, _: Principal = Depends(require("reviews:write")),
               session: Session = Depends(get_session)) -> ReviewOut:
    task = session.get(ReviewTask, task_id)
    if task is None:
        raise ApiError(404, "not_found", "review task not found")
    return _review_out(session, task)


@router.post("/reviews/{task_id}/decision", response_model=ReviewOut, summary="Decide a review task")
def decide_review(task_id: int, body: DecisionIn, request: Request,
                  principal: Principal = Depends(require("reviews:write")),
                  session: Session = Depends(get_session)) -> ReviewOut:
    task = session.get(ReviewTask, task_id, with_for_update=True)
    if task is None:
        raise ApiError(404, "not_found", "review task not found")
    if task.status != ReviewStatus.OPEN:
        raise ApiError(409, "already_decided", f"task is {task.status}")
    domain = session.get(Domain, task.domain_id, with_for_update=True)
    taxonomy = default_taxonomy()
    before = {"primary": domain.primary_category_id, "secondary": domain.secondary_categories,
              "status": str(domain.classification_status)}
    risk_ids = set(taxonomy.assignable_ids("risk"))
    bad_risk = [r for r in body.risk if r not in risk_ids]
    if bad_risk:
        raise ApiError(422, "invalid_category", f"unknown security-threat categories: {bad_risk}")

    primary, secondary, confidence = None, [], None
    if body.action == "approve":
        pred = session.get(AIPrediction, task.ai_prediction_id) if task.ai_prediction_id else None
        if pred is None or pred.primary_category_id is None:
            raise ApiError(409, "nothing_to_approve", "this task has no valid model prediction; use action=set")
        # A person confirmed it: the model's self-reported confidence no longer describes this assignment.
        primary, secondary = pred.primary_category_id, pred.secondary_category_ids
        session.execute(Evidence.__table__.update().where(Evidence.ai_prediction_id == pred.id).values(verified=True))
    elif body.action == "set":
        if not body.primary:
            raise ApiError(422, "validation_error", "primary is required for action=set")
        primary, secondary = body.primary, body.secondary

    if primary is not None:
        try:
            apply_categories(session, domain, primary=primary, secondary=secondary, confidence=confidence,
                             origin=AssignmentOrigin.HUMAN, taxonomy=taxonomy, status=ClassificationStatus.VERIFIED,
                             prediction_id=task.ai_prediction_id, user_id=principal.user_id,
                             source_slug=REVIEW_SOURCE)
        except ValueError as exc:
            raise ApiError(422, "invalid_category", str(exc)) from exc
    elif domain.classification_status == ClassificationStatus.NEEDS_REVIEW:
        domain.classification_status = ClassificationStatus.REANALYZE  # rejected and nothing better known
    if body.risk:
        record_threats(session, domain, body.risk, source_slug=REVIEW_SOURCE, reference=f"review:{task.id}")

    task.status, task.decided_by_id, task.decided_at = ReviewStatus.DONE, principal.user_id, datetime.now(UTC)
    task.decision_primary_id, task.decision_secondary_ids = primary, secondary
    task.decision_note = body.note
    audit.record(session, action="review.decide", target_type="domain", target_id=domain.normalized_domain,
                 before=before, after={"action": body.action, "primary": primary, "secondary": secondary,
                                       "risk": body.risk, "task_id": task.id},
                 principal=principal, request_id=getattr(request.state, "request_id", None))
    session.commit()
    return _review_out(session, task)


@router.get("/domains/{domain}/screenshot", summary="Latest screenshot of a domain (private artefact)",
            responses={200: {"content": {"image/png": {}}}})
def screenshot(domain: str, _: Principal = Depends(require("reviews:write")),
               session: Session = Depends(get_session), store=Depends(get_store)) -> Response:
    try:
        name = normalize_domain(domain).ascii
    except DomainError as exc:
        raise ApiError(422, "invalid_domain", str(exc)) from exc
    row = session.execute(
        select(ScreenshotRow).join(ScanResult, ScanResult.id == ScreenshotRow.scan_result_id)
        .join(Domain, Domain.id == ScanResult.domain_id).where(Domain.normalized_domain == name)
        .order_by(ScreenshotRow.created_at.desc()).limit(1)).scalar()
    if row is None:
        raise ApiError(404, "not_found", "no screenshot for this domain")
    # Served with a restrictive CSP and as an attachment-safe image; the bytes come from our own capture.
    return Response(store.get(row.object_key), media_type="image/png",
                    headers={"Cache-Control": "private, max-age=300", "Content-Security-Policy": "default-src 'none'",
                             "X-Content-Type-Options": "nosniff"})


def ask_worker(probe: bool) -> dict | None:
    """The API container has no AI key by design; the worker that classifies reports its own provider status."""
    from ..worker import celery_app

    try:
        return celery_app.send_task("category.maintenance.provider_health", kwargs={"probe": probe},
                                    queue="maintenance").get(timeout=300 if probe else 15)
    except Exception:  # noqa: BLE001 - timeout, broker down, worker error: reported as unknown
        return None


@router.get("/providers", summary="AI provider status (reported by the classification worker)")
def providers(probe: bool = Query(False, description="send one small live request (uses quota, can take minutes)"),
              _: Principal = Depends(require("reviews:write"))) -> dict:
    settings = get_settings()
    worker = ask_worker(probe)
    active = worker or {"provider": settings.ai_provider, "model": None, "status": "unknown",
                        "detail": "the classification worker did not answer", "supports_images": None,
                        "second_opinion": None}
    return {
        "available": available(),
        "active": {k: active.get(k) for k in ("provider", "model", "status", "detail", "supports_images")},
        "second_opinion": active.get("second_opinion"),
        "policy": {"auto_approve": settings.confidence_auto_approve,
                   "extra_validation": settings.confidence_extra_validation,
                   "human_review": settings.confidence_human_review},
    }


@router.get("/calibration", summary="Model accuracy per confidence band (from human reviews)")
def calibration_report(_: Principal = Depends(require("reviews:write")),
                       session: Session = Depends(get_session)) -> dict:
    return calibration.report(session)


def send_task(name: str) -> None:
    from ..worker import celery_app

    celery_app.send_task(name)


@router.post("/feeds/build", status_code=202, summary="Rebuild feeds now (normally hourly)")
def build_feeds(request: Request, principal: Principal = Depends(require("reviews:write")),
                session: Session = Depends(get_session)) -> dict:
    send_task("category.feeds.build")
    audit.record(session, action="feeds.build", target_type="feeds", principal=principal,
                 request_id=getattr(request.state, "request_id", None))
    session.commit()
    return {"queued": True}
