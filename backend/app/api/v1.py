"""Public API v1 (read-only, anonymous, rate-limited). The full contract is in documentation/API_DESIGN.md."""
from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from pydantic import BaseModel
from sqlalchemy import Date, cast, func, select
from sqlalchemy.orm import Session

from common.domains import DomainError, normalize_domain, normalize_url
from database.models import (
    AIPrediction,
    Category,
    ClassificationStatus,
    DataSource,
    Domain,
    DomainCategory,
    ReviewStatus,
    ReviewTask,
    ScanJob,
    Screenshot,
    SourceLicense,
    TaxonomyVersion,
    ThreatIntelligence,
    ThreatType,
)
from database.publication import publishable, published
from database.session import get_session

from ..deps import Principal, get_principal, rate_limit
from ..errors import ApiError

router = APIRouter(prefix="/api/v1", tags=["public"], dependencies=[Depends(rate_limit("read"))])


# ----------------------------------------------------------------------------- schemas


class CategoryOut(BaseModel):
    id: str
    code: int
    name: str
    description: str
    kind: Literal["content", "risk"]
    parent_id: str | None
    is_group: bool
    deprecated_in: str | None
    replaced_by_id: str | None


class CategoryList(BaseModel):
    taxonomy_version: str
    license: str = "CC-BY-4.0"
    items: list[CategoryOut]


class DomainCategoryOut(BaseModel):
    id: str
    code: int
    name: str
    primary: bool
    confidence: float | None
    origin: str


class RiskOut(BaseModel):
    malware: bool = False
    phishing: bool = False
    scam: bool = False
    fraud: bool = False
    command_and_control: bool = False
    malicious_downloads: bool = False
    suspicious: bool = False
    sources: list[dict] = []


class DomainOut(BaseModel):
    domain: str
    display_domain: str
    registrable_domain: str
    categories: list[DomainCategoryOut]
    risk: RiskOut
    classification_status: str
    first_seen: datetime
    last_updated: datetime
    last_scanned: datetime | None
    data_source: str | None
    taxonomy_version: str
    license: str = "CC-BY-4.0"
    attribution: str = "Cat-egory contributors"


class UrlOut(BaseModel):
    url: str
    normalized_url: str
    match: Literal["host", "registrable_domain"]
    result: DomainOut


class HistoryItem(BaseModel):
    category: str
    primary: bool
    origin: str
    confidence: float | None
    valid_from: datetime
    valid_to: datetime | None


class HistoryOut(BaseModel):
    domain: str
    history: list[HistoryItem]
    next_cursor: int | None


class CategoryDomainsOut(BaseModel):
    category: str
    items: list[dict]
    next_cursor: int | None
    license: str = "CC-BY-4.0"


# ----------------------------------------------------------------------------- helpers


def _current_version(session: Session) -> str:
    version = session.scalar(select(TaxonomyVersion.version).where(TaxonomyVersion.is_current))
    if version is None:
        raise HTTPException(503, "taxonomy not loaded; run `python -m database.seed`")
    return version


def _normalize(domain: str) -> str:
    try:
        return normalize_domain(domain).ascii
    except DomainError as exc:
        raise ApiError(422, "invalid_domain", str(exc), details={"code": exc.code}) from exc


def _find(session: Session, name: str, principal: Principal) -> Domain | None:
    stmt = select(Domain).where(Domain.normalized_domain == name, Domain.is_active)
    if not principal.authenticated:
        stmt = stmt.where(publishable())
    return session.scalar(stmt)


def _not_found() -> ApiError:
    return ApiError(404, "not_found", "domain is not in the database yet",
                    details={"hint": "suggest it with POST /api/v1/community/submit"})


def domain_view(session: Session, row: Domain) -> tuple[DomainOut, str]:
    """Public representation + a version tag for the ETag."""
    current = session.execute(
        select(DomainCategory, Category, DataSource.slug)
        .join(Category, Category.id == DomainCategory.category_id)
        .outerjoin(DataSource, DataSource.id == DomainCategory.source_id)
        .where(DomainCategory.domain_id == row.id, DomainCategory.valid_to.is_(None))
        .order_by(DomainCategory.is_primary.desc(), Category.code)
    ).all()
    threats = session.execute(
        select(ThreatIntelligence, DataSource.slug)
        .join(DataSource, DataSource.id == ThreatIntelligence.source_id)
        .join(SourceLicense, SourceLicense.id == DataSource.license_id)
        .where(ThreatIntelligence.domain_id == row.id, ThreatIntelligence.is_active,
               SourceLicense.redistribution_allowed)  # internal-use-only threat data is never exposed
    ).all()
    tag = "|".join([str(row.id), row.updated_at.isoformat(), str(row.last_scanned), str(row.classification_status),
                    *(f"{dc.id}:{dc.valid_from.isoformat()}" for dc, _, _ in current),
                    *(f"t{t.id}:{t.last_seen.isoformat()}" for t, _ in threats)])

    # Risk flags come only from threat-intelligence rows (verified sources / human review), never from raw AI claims.
    risk = RiskOut()
    for threat, source_slug in threats:
        attr = {ThreatType.COMMAND_AND_CONTROL: "command_and_control",
                ThreatType.MALICIOUS_DOWNLOADS: "malicious_downloads"}.get(threat.threat_type, str(threat.threat_type))
        setattr(risk, attr, True)
        risk.sources.append({"type": str(threat.threat_type), "source": source_slug,
                             "first_seen": threat.first_seen.isoformat()})
    primary_source = next((src for dc, _, src in current if dc.is_primary), None)
    out = DomainOut(
        domain=row.normalized_domain, display_domain=row.domain, registrable_domain=row.registrable_domain,
        categories=[DomainCategoryOut(id=c.id, code=c.code, name=c.name, primary=dc.is_primary,
                                      confidence=float(dc.confidence) if dc.confidence is not None else None,
                                      origin=str(dc.origin)) for dc, c, _ in current],
        risk=risk, classification_status=str(row.classification_status), first_seen=row.first_seen,
        last_updated=row.updated_at, last_scanned=row.last_scanned, data_source=primary_source,
        taxonomy_version=_current_version(session),
    )
    return out, tag


def _etag(tag: str) -> str:
    return f'W/"{hashlib.sha256(tag.encode()).hexdigest()[:20]}"'


def _cached(request: Request, response: Response, tag: str, max_age: int = 300) -> Response | None:
    etag = _etag(tag)
    headers = {"ETag": etag, "Cache-Control": f"public, max-age={max_age}"}
    if request.headers.get("if-none-match") == etag:
        return Response(status_code=304, headers=headers)
    response.headers.update(headers)
    return None


# ----------------------------------------------------------------------------- categories


@router.get("/categories", response_model=CategoryList, summary="List the category taxonomy")
def list_categories(response: Response, kind: Literal["content", "risk"] | None = None,
                    session: Session = Depends(get_session)) -> CategoryList:
    stmt = select(Category).order_by(Category.code)
    if kind:
        stmt = stmt.where(Category.kind == kind)
    items = [CategoryOut.model_validate(c, from_attributes=True) for c in session.scalars(stmt)]
    response.headers["Cache-Control"] = "public, max-age=3600"
    return CategoryList(taxonomy_version=_current_version(session), items=items)


@router.get("/categories/{category_id}", response_model=CategoryOut, summary="Get one category")
def get_category(category_id: str, session: Session = Depends(get_session)) -> CategoryOut:
    category = session.get(Category, category_id)
    if category is None:
        raise ApiError(404, "not_found", "category not found")
    return CategoryOut.model_validate(category, from_attributes=True)


@router.get("/categories/{category_id}/domains", response_model=CategoryDomainsOut,
            summary="Published domains in a category (keyset pagination)")
def category_domains(category_id: str, response: Response, primary_only: bool = False,
                     limit: int = Query(100, ge=1, le=1000), cursor: int | None = Query(None, ge=0),
                     session: Session = Depends(get_session)) -> CategoryDomainsOut:
    category = session.get(Category, category_id)
    if category is None:
        raise ApiError(404, "not_found", "category not found")
    if category.kind == "risk":
        stmt = (select(Domain.id, Domain.normalized_domain, ThreatIntelligence.first_seen)
                .join(ThreatIntelligence, ThreatIntelligence.domain_id == Domain.id)
                .where(ThreatIntelligence.threat_type == category_id, ThreatIntelligence.is_active,
                       Domain.is_active, publishable()))
    else:
        stmt = (select(Domain.id, Domain.normalized_domain, DomainCategory.is_primary)
                .join(DomainCategory, DomainCategory.domain_id == Domain.id)
                .where(DomainCategory.category_id == category_id, DomainCategory.valid_to.is_(None), published()))
        if primary_only:
            stmt = stmt.where(DomainCategory.is_primary)
    if cursor is not None:
        stmt = stmt.where(Domain.id > cursor)
    rows = session.execute(stmt.order_by(Domain.id).limit(limit + 1)).all()
    items = [{"domain": r[1], "primary": r[2]} if category.kind == "content" else {"domain": r[1]}
             for r in rows[:limit]]
    response.headers["Cache-Control"] = "public, max-age=300"
    return CategoryDomainsOut(category=category_id, items=items,
                              next_cursor=rows[limit - 1][0] if len(rows) > limit else None)


# ----------------------------------------------------------------------------- lookups


@router.get("/domain/{domain}", response_model=DomainOut, summary="Classification of a domain",
            responses={304: {"description": "Not modified (ETag match)"}, 404: {"description": "Unknown domain"}})
def get_domain(domain: str, request: Request, response: Response, principal: Principal = Depends(get_principal),
               session: Session = Depends(get_session)) -> DomainOut | Response:
    row = _find(session, _normalize(domain), principal)
    if row is None:
        raise _not_found()
    out, tag = domain_view(session, row)
    return _cached(request, response, tag) or out


@router.get("/url", response_model=UrlOut, summary="Classification for a URL (falls back to its registrable domain)")
def get_url(url: str, request: Request, response: Response, principal: Principal = Depends(get_principal),
            session: Session = Depends(get_session)) -> UrlOut | Response:
    try:
        normalized = normalize_url(url)
    except DomainError as exc:
        raise ApiError(422, "invalid_url", str(exc), details={"code": exc.code}) from exc
    match = "host"
    row = _find(session, normalized.domain.ascii, principal)
    if row is None and normalized.domain.registrable != normalized.domain.ascii:
        row, match = _find(session, normalized.domain.registrable, principal), "registrable_domain"
    if row is None:
        raise _not_found()
    out, tag = domain_view(session, row)
    return _cached(request, response, tag + match) or UrlOut(url=url[:2000], normalized_url=normalized.url,
                                                              match=match, result=out)


@router.get("/history/{domain}", response_model=HistoryOut, summary="Category history of a domain (newest first)")
def history(domain: str, limit: int = Query(100, ge=1, le=500), cursor: int | None = Query(None, ge=0),
            principal: Principal = Depends(get_principal), session: Session = Depends(get_session)) -> HistoryOut:
    row = _find(session, _normalize(domain), principal)
    if row is None:
        raise _not_found()
    stmt = (select(DomainCategory).where(DomainCategory.domain_id == row.id)
            .order_by(DomainCategory.id.desc()).limit(limit + 1))
    if cursor:
        stmt = stmt.where(DomainCategory.id < cursor)
    rows = list(session.scalars(stmt))
    return HistoryOut(
        domain=row.normalized_domain,
        history=[HistoryItem(category=r.category_id, primary=r.is_primary, origin=str(r.origin),
                             confidence=float(r.confidence) if r.confidence is not None else None,
                             valid_from=r.valid_from, valid_to=r.valid_to) for r in rows[:limit]],
        next_cursor=rows[limit - 1].id if len(rows) > limit else None)


# ----------------------------------------------------------------------------- stats


@router.get("/stats", summary="Database statistics for dashboards")
def stats(response: Response, session: Session = Depends(get_session)) -> dict:
    now = datetime.now(UTC)
    today = now.replace(hour=0, minute=0, second=0, microsecond=0)
    count = lambda stmt: session.scalar(stmt) or 0  # noqa: E731
    by_status = dict(session.execute(select(Domain.classification_status, func.count())
                                     .where(Domain.is_active).group_by(Domain.classification_status)).all())
    distribution = session.execute(
        select(Domain.primary_category_id, func.count()).where(published(), Domain.primary_category_id.is_not(None))
        .group_by(Domain.primary_category_id).order_by(func.count().desc())).all()
    month_ago = now - timedelta(days=30)
    predictions = count(select(func.count()).select_from(AIPrediction).where(AIPrediction.created_at >= month_ago))
    valid = count(select(func.count()).select_from(AIPrediction).where(AIPrediction.created_at >= month_ago,
                                                                         AIPrediction.validation_status == "valid"))
    daily = session.execute(
        select(cast(ScanJob.finished_at, Date).label("day"), ScanJob.status, func.count())
        .where(ScanJob.finished_at >= today - timedelta(days=13)).group_by("day", ScanJob.status).order_by("day")).all()
    daily_out: dict[str, dict[str, int]] = {}
    for day, status, n in daily:
        daily_out.setdefault(day.isoformat(), {})[str(status)] = n
    threats = dict(session.execute(select(ThreatIntelligence.threat_type, func.count())
                                   .where(ThreatIntelligence.is_active).group_by(ThreatIntelligence.threat_type)).all())
    response.headers["Cache-Control"] = "public, max-age=60"
    return {
        "generated_at": now.isoformat(),
        "total_domains": sum(by_status.values()),
        "categorized_domains": (by_status.get(ClassificationStatus.CLASSIFIED, 0)
                                + by_status.get(ClassificationStatus.VERIFIED, 0)),
        "verified_domains": by_status.get(ClassificationStatus.VERIFIED, 0),
        "by_status": {str(k): v for k, v in by_status.items()},
        "pending_reviews": count(select(func.count()).select_from(ReviewTask).where(ReviewTask.status == ReviewStatus.OPEN)),
        "domains_scanned_today": count(select(func.count(func.distinct(ScanJob.domain_id)))
                                       .where(ScanJob.finished_at >= today, ScanJob.status == "succeeded")),
        "total_screenshots": count(select(func.count()).select_from(Screenshot)),
        "ai_predictions_30d": predictions,
        "ai_valid_answer_rate_30d": round(valid / predictions, 4) if predictions else None,
        "category_distribution": [{"category": c, "domains": n} for c, n in distribution],
        "threats": {str(k): v for k, v in threats.items()},
        "daily_scans": daily_out,
    }
