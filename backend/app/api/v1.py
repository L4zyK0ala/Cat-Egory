"""Public API v1 (read-only, anonymous, rate-limited). The full contract is in documentation/API_DESIGN.md."""
from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta
from typing import Literal

import redis
from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy import ColumnElement, Date, cast, func, select
from sqlalchemy.orm import InstrumentedAttribute, Session

from classification.engine import threat_candidate
from common.config import get_settings
from common.domains import DomainError, normalize_domain, normalize_url
from common.timeutil import local_midnight
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
    TaxonomyVersion,
    ThreatIntelligence,
    ThreatType,
    Url,
)
from database.publication import publishable
from database.session import get_session
from threats.records import domain_scoped, listed, public, url_scoped

from ..demand import record_lookups
from ..deps import Principal, consume_quota, get_principal, get_redis, rate_limit
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
    lookalike_domain: bool = False
    sources: list[dict] = []
    # Single pages on this host that are confirmed as threats (e.g. a phishing page on a hacked site). They do
    # not flag the domain itself; the URLs are not published (they can contain a victim's address).
    reported_urls: int = 0


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
    url_risk: RiskOut  # threats confirmed for this exact URL (normalised), independent of the domain's flags


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


def sees_private(principal: Principal) -> bool:
    """Domains known only from a private import stay hidden from everyone but staff. Any API key used to be
    enough; a self-service developer key must not be."""
    return "domains:write" in principal.scopes


def _find(session: Session, name: str, principal: Principal) -> Domain | None:
    stmt = select(Domain).where(Domain.normalized_domain == name, Domain.is_active)
    if not sees_private(principal):
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
    # internal-use-only threat data is never exposed (threats.records.public)
    threats = session.execute(
        public(select(ThreatIntelligence, DataSource.slug)).where(ThreatIntelligence.domain_id == row.id)).all()
    tag = "|".join([str(row.id), row.updated_at.isoformat(), str(row.last_scanned), str(row.classification_status),
                    *(f"{dc.id}:{dc.valid_from.isoformat()}" for dc, _, _ in current),
                    *(f"t{t.id}:{t.last_seen.isoformat()}" for t, _ in threats)])

    # Risk flags come only from threat-intelligence rows (verified sources / human review), never from raw AI
    # claims; and only domain-scoped rows flag the domain.
    risk = _risk([(t, s) for t, s in threats if t.url_id is None])
    risk.reported_urls = len({t.url_id for t, _ in threats if t.url_id is not None})
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


def _risk(threats: list[tuple[ThreatIntelligence, str]]) -> RiskOut:
    risk = RiskOut()
    for threat, source_slug in threats:
        attr = {ThreatType.COMMAND_AND_CONTROL: "command_and_control",
                ThreatType.MALICIOUS_DOWNLOADS: "malicious_downloads",
                ThreatType.LOOKALIKE_DOMAIN: "lookalike_domain"}.get(threat.threat_type, str(threat.threat_type))
        setattr(risk, attr, True)
        risk.sources.append({"type": str(threat.threat_type), "source": source_slug,
                             "first_seen": threat.first_seen.isoformat()})
    return risk


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
def category_domains(category_id: str, response: Response,
                     primary_only: bool = Query(True, description="false: also domains where it is a secondary "
                                                                 "category (the lists use main categories only)"),
                     limit: int = Query(100, ge=1, le=1000), cursor: int | None = Query(None, ge=0),
                     session: Session = Depends(get_session)) -> CategoryDomainsOut:
    category = session.get(Category, category_id)
    if category is None:
        raise ApiError(404, "not_found", "category not found")
    if category.kind == "risk":  # same rules as the domain page: public, domain-scoped records only
        stmt = (public(select(Domain.id, Domain.normalized_domain)
                       .join(ThreatIntelligence, ThreatIntelligence.domain_id == Domain.id))
                .where(ThreatIntelligence.threat_type == category_id, domain_scoped(), Domain.is_active,
                       publishable())
                .distinct())  # one row per domain even when several sources report it
    else:
        stmt = (select(Domain.id, Domain.normalized_domain, DomainCategory.is_primary)
                .join(DomainCategory, DomainCategory.domain_id == Domain.id)
                .where(DomainCategory.category_id == category_id, DomainCategory.valid_to.is_(None), listed()))
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
               client: redis.Redis = Depends(get_redis), session: Session = Depends(get_session)) -> DomainOut | Response:
    name = _normalize(domain)
    record_lookups(client, request, principal, [name])  # also (especially) names we do not know yet
    row = _find(session, name, principal)
    if row is None:
        raise _not_found()
    out, tag = domain_view(session, row)
    return _cached(request, response, tag) or out


@router.get("/url", response_model=UrlOut, summary="Classification for a URL (falls back to its registrable domain)")
def get_url(url: str, request: Request, response: Response, principal: Principal = Depends(get_principal),
            client: redis.Redis = Depends(get_redis), session: Session = Depends(get_session)) -> UrlOut | Response:
    try:
        normalized = normalize_url(url)
    except DomainError as exc:
        raise ApiError(422, "invalid_url", str(exc), details={"code": exc.code}) from exc
    record_lookups(client, request, principal, [normalized.domain.ascii])
    match = "host"
    row = _find(session, normalized.domain.ascii, principal)
    if row is None and normalized.domain.registrable != normalized.domain.ascii:
        row, match = _find(session, normalized.domain.registrable, principal), "registrable_domain"
    if row is None:
        raise _not_found()
    out, tag = domain_view(session, row)
    url_threats = session.execute(
        public(select(ThreatIntelligence, DataSource.slug).join(Url, Url.id == ThreatIntelligence.url_id))
        .where(Url.url_hash == normalized.sha256, Url.domain_id == row.id)).all()
    tag += match + "".join(f"|u{t.id}:{t.last_seen.isoformat()}" for t, _ in url_threats)
    return _cached(request, response, tag) or UrlOut(url=url[:2000], normalized_url=normalized.url, match=match,
                                                     result=out, url_risk=_risk(url_threats))


class LookupIn(BaseModel):
    domains: list[str] = Field(min_length=1, max_length=1000, description="names or URLs; 100 without a key, 1000 with")


class LookupItem(BaseModel):
    input: str
    domain: str | None = Field(description="normalised name, or None when the input is not a valid domain or URL")
    status: Literal["found", "registrable_domain", "not_found", "invalid"]
    matched: str | None = Field(description="the stored name the answer is for (the registrable domain on a fallback)")
    primary: str | None
    secondary: list[str]
    threats: list[str] = Field(description="confirmed threat types on the domain itself")
    classification_status: str | None


class LookupOut(BaseModel):
    taxonomy_version: str
    items: list[LookupItem]


def _lookup_name(raw: str) -> tuple[str, str] | None:
    """(ascii name, registrable domain) of a name or URL, or None."""
    value = raw.strip()
    try:
        if "/" in value or ":" in value:
            parsed = normalize_url(value).domain
        else:
            parsed = normalize_domain(value)
    except DomainError:
        return None
    return parsed.ascii, parsed.registrable


@router.post("/lookup", response_model=LookupOut, summary="Look up many domains or URLs at once")
def bulk_lookup(body: LookupIn, request: Request, principal: Principal = Depends(get_principal),
                client: redis.Redis = Depends(get_redis), session: Session = Depends(get_session)) -> LookupOut:
    settings = get_settings()
    limit = settings.bulk_lookup_max_with_key if principal.authenticated else settings.bulk_lookup_max_anonymous
    if len(body.domains) > limit:
        raise ApiError(422, "too_many_domains", f"at most {limit} names per request"
                       + ("" if principal.authenticated else "; a free developer key allows more"))
    consume_quota(client, principal, len(body.domains) - 1)  # the request itself already counted once
    parsed = [(raw[:300], _lookup_name(raw[:300])) for raw in body.domains]
    record_lookups(client, request, principal, [p[0] for _, p in parsed if p])
    wanted = {n for _, p in parsed if p for n in p}
    stmt = select(Domain).where(Domain.normalized_domain.in_(wanted), Domain.is_active)
    if not sees_private(principal):
        stmt = stmt.where(publishable())
    rows = {d.normalized_domain: d for d in session.scalars(stmt)} if wanted else {}
    ids = [d.id for d in rows.values()]
    primary: dict[int, str] = {}
    secondary: dict[int, list[str]] = {}
    threats: dict[int, list[str]] = {}
    if ids:
        for domain_id, category_id, is_primary in session.execute(
                select(DomainCategory.domain_id, DomainCategory.category_id, DomainCategory.is_primary)
                .where(DomainCategory.domain_id.in_(ids), DomainCategory.valid_to.is_(None))
                .order_by(DomainCategory.category_id)):
            if is_primary:
                primary[domain_id] = category_id
            else:
                secondary.setdefault(domain_id, []).append(category_id)
        for domain_id, threat in session.execute(
                public(select(ThreatIntelligence.domain_id, ThreatIntelligence.threat_type))
                .where(ThreatIntelligence.domain_id.in_(ids), ThreatIntelligence.url_id.is_(None)).distinct()):
            threats.setdefault(domain_id, []).append(str(threat))
    items = []
    for raw, names in parsed:
        if names is None:
            items.append(LookupItem(input=raw, domain=None, status="invalid", matched=None, primary=None, secondary=[],
                                    threats=[], classification_status=None))
            continue
        name, registrable = names
        row, status = rows.get(name), "found"
        if row is None and registrable != name:
            row, status = rows.get(registrable), "registrable_domain"
        if row is None:
            items.append(LookupItem(input=raw, domain=name, status="not_found", matched=None, primary=None,
                                    secondary=[], threats=[], classification_status=None))
            continue
        items.append(LookupItem(input=raw, domain=name, status=status, matched=row.normalized_domain,
                                primary=primary.get(row.id), secondary=secondary.get(row.id, []),
                                threats=sorted(threats.get(row.id, [])),
                                classification_status=str(row.classification_status)))
    return LookupOut(taxonomy_version=_current_version(session), items=items)


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


@router.get("/accuracy", summary="How right the published data is, measured against people")
def accuracy(response: Response, session: Session = Depends(get_session)) -> dict:
    from classification.accuracy import report

    response.headers["Cache-Control"] = "public, max-age=300"
    return report(session)


@router.get("/stats", summary="Database statistics for dashboards")
def stats(response: Response, session: Session = Depends(get_session)) -> dict:
    now = datetime.now(UTC)
    offset = get_settings().app_utc_offset_hours
    today = local_midnight(now, offset)
    count = lambda stmt: session.scalar(stmt) or 0  # noqa: E731
    by_status = dict(session.execute(select(Domain.classification_status, func.count())
                                     .where(Domain.is_active).group_by(Domain.classification_status)).all())
    distribution = session.execute(
        select(Domain.primary_category_id, func.count()).where(listed(), Domain.primary_category_id.is_not(None))
        .group_by(Domain.primary_category_id).order_by(func.count().desc())).all()
    month_ago = now - timedelta(days=30)
    predictions = count(select(func.count()).select_from(AIPrediction).where(AIPrediction.created_at >= month_ago))
    valid = count(select(func.count()).select_from(AIPrediction).where(AIPrediction.created_at >= month_ago,
                                                                         AIPrediction.validation_status == "valid"))
    daily = session.execute(
        select(cast(func.timezone(func.make_interval(0, 0, 0, 0, offset), ScanJob.finished_at), Date).label("day"),
               ScanJob.status, func.count())
        .where(ScanJob.finished_at >= today - timedelta(days=13)).group_by("day", ScanJob.status).order_by("day")).all()
    daily_out: dict[str, dict[str, int]] = {}
    for day, status, n in daily:
        daily_out.setdefault(day.isoformat(), {})[str(status)] = n
    # Same rules as the domain page: public threat records on published domains. Domains flagged as a whole and
    # single reported URLs (on otherwise legitimate sites) are counted apart.
    def threat_counts(scope: ColumnElement[bool], counted: InstrumentedAttribute) -> dict:
        return {str(k): v for k, v in session.execute(
            public(select(ThreatIntelligence.threat_type, func.count(func.distinct(counted)))
                   .join(Domain, Domain.id == ThreatIntelligence.domain_id))
            .where(scope, Domain.is_active, publishable())
            .group_by(ThreatIntelligence.threat_type)).all()}
    threats = threat_counts(domain_scoped(), ThreatIntelligence.domain_id)
    threat_urls = threat_counts(url_scoped(), ThreatIntelligence.url_id)
    response.headers["Cache-Control"] = "public, max-age=60"
    return {
        "generated_at": now.isoformat(),
        "total_domains": sum(by_status.values()),
        "categorized_domains": (by_status.get(ClassificationStatus.CLASSIFIED, 0)
                                + by_status.get(ClassificationStatus.VERIFIED, 0)),
        "verified_domains": by_status.get(ClassificationStatus.VERIFIED, 0),
        "by_status": {str(k): v for k, v in by_status.items()},
        # category questions and threat candidates (look-alikes, threat claims) are two separate queues
        "pending_reviews": count(select(func.count()).select_from(ReviewTask)
                                 .where(ReviewTask.status == ReviewStatus.OPEN, ~threat_candidate())),
        "pending_threat_candidates": count(select(func.count()).select_from(ReviewTask)
                                           .where(ReviewTask.status == ReviewStatus.OPEN, threat_candidate())),
        "domains_scanned_today": count(select(func.count(func.distinct(ScanJob.domain_id)))
                                       .where(ScanJob.finished_at >= today, ScanJob.status == "succeeded")),
        "total_screenshots": count(select(func.count()).select_from(Screenshot)),
        "ai_predictions_30d": predictions,
        "ai_valid_answer_rate_30d": round(valid / predictions, 4) if predictions else None,
        "category_distribution": [{"category": c, "domains": n} for c, n in distribution],
        "threats": threats,
        "threat_urls": threat_urls,
        "daily_scans": daily_out,
    }
