"""Public API v1 (read-only, anonymous, rate-limited). The full contract is in documentation/API_DESIGN.md."""
from __future__ import annotations

import hashlib
from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from common.domains import DomainError, normalize_domain
from database.models import (
    Category,
    DataSource,
    Domain,
    DomainCategory,
    TaxonomyVersion,
    ThreatIntelligence,
    ThreatType,
)
from database.session import get_session

from ..deps import rate_limit
from ..errors import ApiError

router = APIRouter(prefix="/api/v1", tags=["public"], dependencies=[Depends(rate_limit("read"))])


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


def _current_version(session: Session) -> str:
    version = session.scalar(select(TaxonomyVersion.version).where(TaxonomyVersion.is_current))
    if version is None:
        raise HTTPException(503, "taxonomy not loaded; run `python -m database.seed`")
    return version


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
        raise HTTPException(404, "category not found")
    return CategoryOut.model_validate(category, from_attributes=True)


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


@router.get("/domain/{domain}", response_model=DomainOut, summary="Classification of a domain",
            responses={304: {"description": "Not modified (ETag match)"}, 404: {"description": "Unknown domain"}})
def get_domain(domain: str, request: Request, response: Response,
               session: Session = Depends(get_session)) -> DomainOut | Response:
    try:
        name = normalize_domain(domain).ascii
    except DomainError as exc:
        raise ApiError(422, "invalid_domain", str(exc), details={"code": exc.code}) from exc
    row = session.scalar(select(Domain).where(Domain.normalized_domain == name, Domain.is_active))
    if row is None:
        raise ApiError(404, "not_found", "domain is not in the database yet",
                       details={"hint": "suggest it via the community endpoint (Phase 6)"})

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
        .where(ThreatIntelligence.domain_id == row.id, ThreatIntelligence.is_active)
    ).all()

    version_tag = "|".join([str(row.id), row.updated_at.isoformat(), str(row.last_scanned),
                            *(f"{dc.id}:{dc.valid_from.isoformat()}" for dc, _, _ in current),
                            *(f"t{t.id}:{t.last_seen.isoformat()}" for t, _ in threats)])
    etag = f'W/"{hashlib.sha256(version_tag.encode()).hexdigest()[:20]}"'
    headers = {"ETag": etag, "Cache-Control": "public, max-age=300"}
    if request.headers.get("if-none-match") == etag:
        return Response(status_code=304, headers=headers)
    response.headers.update(headers)

    # Risk flags come only from threat-intelligence sources here; unreviewed AI suspicions are not exposed.
    risk = RiskOut()
    for threat, source_slug in threats:
        attr = {ThreatType.COMMAND_AND_CONTROL: "command_and_control",
                ThreatType.MALICIOUS_DOWNLOADS: "malicious_downloads"}.get(threat.threat_type,
                                                                           str(threat.threat_type))
        setattr(risk, attr, True)
        risk.sources.append({"type": str(threat.threat_type), "source": source_slug,
                             "first_seen": threat.first_seen.isoformat()})

    primary = next((src for dc, _, src in current if dc.is_primary), None)
    taxonomy_version = session.scalar(select(TaxonomyVersion.version).where(TaxonomyVersion.is_current)) or "unknown"
    return DomainOut(
        domain=row.normalized_domain, display_domain=row.domain, registrable_domain=row.registrable_domain,
        categories=[DomainCategoryOut(id=c.id, code=c.code, name=c.name, primary=dc.is_primary,
                                      confidence=float(dc.confidence) if dc.confidence is not None else None,
                                      origin=str(dc.origin)) for dc, c, _ in current],
        risk=risk, classification_status=str(row.classification_status), first_seen=row.first_seen,
        last_updated=row.updated_at, last_scanned=row.last_scanned, data_source=primary,
        taxonomy_version=taxonomy_version,
    )
