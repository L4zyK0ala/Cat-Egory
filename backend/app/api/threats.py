"""Admin API for the threats module: list, confirm and withdraw threat records by hand.

Only on the admin instance (see main.py). A reviewer confirms a threat on a whole domain *or* on one URL, always
with a short evidence note (what they saw). Withdrawing only touches our own reviewer records; third-party feed
records belong to their feed. Every write is audit-logged in the same transaction.
"""
from __future__ import annotations

from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from classification.engine import REVIEW_SOURCE, record_threats, source_id
from classification.taxonomy import default_taxonomy
from common.domains import DomainError, normalize_domain
from database.models import DataSource, Domain, ExternalListing, ThreatIntelligence, ThreatType, Url
from database.session import get_session
from threats import sgb
from threats.records import UnknownDomain, domain_scoped, record_url_threats, url_row, url_scoped, withdraw

from .. import audit
from ..deps import Principal, rate_limit, require
from ..errors import ApiError
from .reviews import guard_protected

router = APIRouter(prefix="/api/v1/admin/threats", tags=["threats"], dependencies=[Depends(rate_limit("write"))])


class Target(BaseModel):
    domain: str | None = Field(default=None, description="flag the whole host")
    url: str | None = Field(default=None, max_length=2000, description="flag this one page only")

    @model_validator(mode="after")
    def _one_target(self):
        if (self.domain is None) == (self.url is None):
            raise ValueError("give exactly one of domain or url")
        return self


class RecordIn(Target):
    types: list[str] = Field(min_length=1, max_length=8, description="security-threat category ids")
    evidence: str = Field(min_length=10, max_length=2000, description="what the reviewer saw (kept internal)")
    confirm_protected: bool = Field(default=False, description="flag a threat on a popular or official site "
                                                               "anyway (otherwise 409 protected_domain)")


class WithdrawIn(Target):
    type: str | None = Field(default=None, description="only this threat type; all of ours when omitted")
    reason: str = Field(min_length=10, max_length=2000)


class SgbMatchOut(BaseModel):
    id: int | str | None
    value: str
    type: str
    category: str | None
    category_name: str | None
    criticality: int | None = Field(description="1 = most critical, 10 = least")
    date: str | None
    scope: str = Field(description="exact domain, a subdomain, or a URL on the domain")
    link: str | None


class SgbOut(BaseModel):
    """National malicious-address list (SGB/USOM), last check. Internal: never published."""
    listed: bool
    checked_at: datetime
    matches: list[SgbMatchOut]


def _sgb(row: ExternalListing | None) -> SgbOut | None:
    if row is None:
        return None
    return SgbOut(listed=row.listed, checked_at=row.checked_at, matches=[
        SgbMatchOut(id=m.get("id"), value=m.get("value", ""), type=m.get("type", ""), category=m.get("category"),
                    category_name=sgb.CATEGORIES.get(m.get("category") or ""), criticality=m.get("criticality"),
                    date=m.get("date"), scope=m.get("scope", "exact"),
                    link=sgb.DETAIL.format(id=m["id"]) if m.get("id") else None)
        for m in row.matches or []])


def _listings(session: Session, domain_ids: set[int]) -> dict[int, ExternalListing]:
    if not domain_ids:
        return {}
    return {r.domain_id: r for r in session.scalars(select(ExternalListing).where(
        ExternalListing.source == sgb.SOURCE, ExternalListing.domain_id.in_(domain_ids)))}


class ThreatOut(BaseModel):
    id: int
    domain: str
    url: str | None  # admin only; the public API never returns URL texts
    type: str
    source: str
    scope: Literal["domain", "url"]
    active: bool
    first_seen: datetime
    last_seen: datetime
    reference: str | None
    evidence: str | None
    sgb: SgbOut | None = None  # None = not checked yet


class ThreatPage(BaseModel):
    items: list[ThreatOut]
    next_cursor: int | None


class ChangeOut(BaseModel):
    target: str
    scope: Literal["domain", "url"]
    changed: int


def _types(ids: list[str]) -> list[str]:
    known = set(default_taxonomy().assignable_ids("risk"))
    bad = [t for t in ids if t not in known]
    if bad:
        raise ApiError(422, "invalid_category", f"unknown security-threat categories: {bad}")
    return list(dict.fromkeys(ids))


def _domain(session: Session, raw: str) -> Domain:
    try:
        name = normalize_domain(raw).ascii
    except DomainError as exc:
        raise ApiError(422, "invalid_domain", str(exc)) from exc
    row = session.scalar(select(Domain).where(Domain.normalized_domain == name))
    if row is None:
        raise ApiError(404, "not_found", "domain is not in the database; add it first")
    return row


def _url(session: Session, raw: str, *, create: bool) -> Url | None:
    try:
        return url_row(session, raw, create=create)
    except DomainError as exc:
        raise ApiError(422, "invalid_url", str(exc)) from exc
    except UnknownDomain as exc:
        raise ApiError(404, "not_found", f"host {exc} is not in the database; add the domain first") from exc


@router.get("", response_model=ThreatPage, summary="Threat records, newest first (all sources, incl. internal)")
def list_threats(scope: Literal["all", "domain", "url"] = "all", active: bool | None = True,
                 limit: int = Query(50, ge=1, le=500), cursor: int | None = Query(None, ge=1),
                 _: Principal = Depends(require("reviews:write")),
                 session: Session = Depends(get_session)) -> ThreatPage:
    stmt = (select(ThreatIntelligence, Domain.normalized_domain, Url.normalized_url, DataSource.slug)
            .join(Domain, Domain.id == ThreatIntelligence.domain_id)
            .join(DataSource, DataSource.id == ThreatIntelligence.source_id)
            .outerjoin(Url, Url.id == ThreatIntelligence.url_id))
    if scope != "all":
        stmt = stmt.where(domain_scoped() if scope == "domain" else url_scoped())
    if active is not None:
        stmt = stmt.where(ThreatIntelligence.is_active.is_(active))
    if cursor:
        stmt = stmt.where(ThreatIntelligence.id < cursor)
    rows = session.execute(stmt.order_by(ThreatIntelligence.id.desc()).limit(limit + 1)).all()
    listings = _listings(session, {t.domain_id for t, _, _, _ in rows[:limit]})
    items = [ThreatOut(id=t.id, domain=d, url=u, type=str(t.threat_type), source=s,
                       scope="domain" if t.url_id is None else "url", active=t.is_active, first_seen=t.first_seen,
                       last_seen=t.last_seen, reference=t.source_reference, evidence=(t.raw or {}).get("evidence"),
                       sgb=_sgb(listings.get(t.domain_id)))
             for t, d, u, s in rows[:limit]]
    return ThreatPage(items=items, next_cursor=rows[limit - 1][0].id if len(rows) > limit else None)


class SgbReportItem(BaseModel):
    domain: str
    types: list[str]
    first_seen: datetime
    status: Literal["listed", "missing", "unchecked"]
    sgb: SgbOut | None


class SgbReport(BaseModel):
    counts: dict[str, int]
    items: list[SgbReportItem]


@router.get("/sgb-report", response_model=SgbReport,
            summary="Our confirmed threats vs the national SGB/USOM list (missing = candidates to report)")
def sgb_report(status: Literal["all", "listed", "missing", "unchecked"] = "missing",
               limit: int = Query(200, ge=1, le=1000), _: Principal = Depends(require("reviews:write")),
               session: Session = Depends(get_session)) -> SgbReport:
    """Domain-scoped threats a reviewer confirmed (active), grouped by domain, with the list's last answer."""
    rows = session.execute(
        select(Domain.id, Domain.normalized_domain, ThreatIntelligence.threat_type, ThreatIntelligence.first_seen)
        .join(ThreatIntelligence, ThreatIntelligence.domain_id == Domain.id)
        .where(ThreatIntelligence.is_active, domain_scoped(),
               ThreatIntelligence.source_id == source_id(session, REVIEW_SOURCE))
        .order_by(Domain.normalized_domain)).all()
    grouped: dict[int, dict] = {}
    for did, name, threat, first in rows:
        g = grouped.setdefault(did, {"domain": name, "types": [], "first_seen": first})
        g["types"].append(str(threat))
        g["first_seen"] = min(g["first_seen"], first)
    listings = _listings(session, set(grouped))
    items, counts = [], {"listed": 0, "missing": 0, "unchecked": 0}
    for did, g in grouped.items():
        row = listings.get(did)
        state = "unchecked" if row is None else ("listed" if row.listed else "missing")
        counts[state] += 1
        if status in ("all", state):
            items.append(SgbReportItem(domain=g["domain"], types=sorted(set(g["types"])), first_seen=g["first_seen"],
                                       status=state, sgb=_sgb(row)))
    items.sort(key=lambda i: i.first_seen)
    return SgbReport(counts=counts, items=items[:limit])


@router.post("", response_model=ChangeOut, status_code=201, summary="Confirm threats on a domain or one URL")
def record(body: RecordIn, request: Request, principal: Principal = Depends(require("reviews:write")),
           session: Session = Depends(get_session)) -> ChangeOut:
    types = _types(body.types)
    reference = "admin"
    if body.url is not None:
        url = _url(session, body.url, create=True)
        assert url is not None
        if not body.confirm_protected:
            guard_protected(session, session.get(Domain, url.domain_id).normalized_domain)
        record_url_threats(session, url.normalized_url, types, reference=reference)
        session.flush()
        target, scope, where = url.normalized_url, "url", ThreatIntelligence.url_id == url.id
    else:
        domain = _domain(session, body.domain or "")
        if not body.confirm_protected:
            guard_protected(session, domain.normalized_domain)
        record_threats(session, domain, types, reference=reference)
        session.flush()
        target, scope, where = domain.normalized_domain, "domain", (
            (ThreatIntelligence.domain_id == domain.id) & domain_scoped())
    # Keep the reviewer's note with the record (internal: never returned by the public API).
    changed = session.execute(
        update(ThreatIntelligence)
        .where(where, ThreatIntelligence.source_id == source_id(session, REVIEW_SOURCE),
               ThreatIntelligence.threat_type.in_([ThreatType(t) for t in types]))
        .values(raw={"evidence": body.evidence, "by": principal.user_id}, source_reference=reference)).rowcount
    audit.record(session, action="threat.record", target_type=scope, target_id=target[:500],
                 after={"types": types, "evidence": body.evidence}, principal=principal,
                 request_id=getattr(request.state, "request_id", None))
    session.commit()
    return ChangeOut(target=target, scope=scope, changed=changed or 0)


@router.post("/withdraw", response_model=ChangeOut, summary="Withdraw our own threat records (false positive)")
def withdraw_threats(body: WithdrawIn, request: Request, principal: Principal = Depends(require("reviews:write")),
                     session: Session = Depends(get_session)) -> ChangeOut:
    threat = ThreatType(_types([body.type])[0]) if body.type else None
    if body.url is not None:
        url = _url(session, body.url, create=False)
        if url is None:
            raise ApiError(404, "not_found", "no threat record for this URL")
        domain = session.get(Domain, url.domain_id)
        target, scope = url.normalized_url, "url"
    else:
        domain, url = _domain(session, body.domain or ""), None
        target, scope = domain.normalized_domain, "domain"
    changed = withdraw(session, domain=domain, threat=threat, url=url)
    audit.record(session, action="threat.withdraw", target_type=scope, target_id=target[:500],
                 after={"type": body.type, "reason": body.reason, "withdrawn": changed}, principal=principal,
                 request_id=getattr(request.state, "request_id", None))
    session.commit()
    return ChangeOut(target=target, scope=scope, changed=changed)
