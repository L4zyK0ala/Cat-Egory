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

from classification.engine import OWN_THREAT_SOURCES, REVIEW_SOURCE, record_threats, source_id
from classification.taxonomy import default_taxonomy
from common.domains import DomainError, normalize_domain
from database.models import DataSource, Domain, ThreatIntelligence, ThreatType, Url
from database.session import get_session
from threats.records import UnknownDomain, domain_scoped, record_url_threats, url_row, url_scoped, withdraw

from .. import audit
from ..deps import Principal, rate_limit, require
from ..errors import ApiError
from .reviews import guard_protected
from .sgb_view import SgbOut, sgb_listings, sgb_out
from .vt_view import VtOut, vt_listings, vt_out

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
    vt: VtOut | None = None    # None = not checked yet, or no VirusTotal key


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
    ids = {t.domain_id for t, _, _, _ in rows[:limit]}
    listings, vt = sgb_listings(session, ids), vt_listings(session, ids)
    items = [ThreatOut(id=t.id, domain=d, url=u, type=str(t.threat_type), source=s,
                       scope="domain" if t.url_id is None else "url", active=t.is_active, first_seen=t.first_seen,
                       last_seen=t.last_seen, reference=t.source_reference, evidence=(t.raw or {}).get("evidence"),
                       sgb=sgb_out(listings.get(t.domain_id)), vt=vt_out(vt.get(t.domain_id), d))
             for t, d, u, s in rows[:limit]]
    return ThreatPage(items=items, next_cursor=rows[limit - 1][0].id if len(rows) > limit else None)


class SgbReportItem(BaseModel):
    domain: str
    types: list[str]
    first_seen: datetime
    status: Literal["listed", "missing", "unchecked"]
    automatic: bool = False  # listed from Octryx's own findings, no person involved
    sgb: SgbOut | None
    vt: VtOut | None = None


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
        select(Domain.id, Domain.normalized_domain, ThreatIntelligence.threat_type, ThreatIntelligence.first_seen,
               DataSource.slug)
        .join(ThreatIntelligence, ThreatIntelligence.domain_id == Domain.id)
        .join(DataSource, DataSource.id == ThreatIntelligence.source_id)
        .where(ThreatIntelligence.is_active, domain_scoped(), DataSource.slug.in_(OWN_THREAT_SOURCES))
        .order_by(Domain.normalized_domain)).all()
    grouped: dict[int, dict] = {}
    for did, name, threat, first, slug in rows:
        g = grouped.setdefault(did, {"domain": name, "types": [], "first_seen": first, "reviewed": False})
        g["reviewed"] = g["reviewed"] or slug == REVIEW_SOURCE
        g["types"].append(str(threat))
        g["first_seen"] = min(g["first_seen"], first)
    listings, vt = sgb_listings(session, set(grouped)), vt_listings(session, set(grouped))
    items, counts = [], {"listed": 0, "missing": 0, "unchecked": 0}
    for did, g in grouped.items():
        row = listings.get(did)
        state = "unchecked" if row is None else ("listed" if row.listed else "missing")
        counts[state] += 1
        if status in ("all", state):
            items.append(SgbReportItem(domain=g["domain"], types=sorted(set(g["types"])), first_seen=g["first_seen"],
                                       status=state, automatic=not g["reviewed"], sgb=sgb_out(row),
                                       vt=vt_out(vt.get(did), g["domain"])))
    items.sort(key=lambda i: i.first_seen)
    return SgbReport(counts=counts, items=items[:limit])


@router.post("", response_model=ChangeOut, status_code=201, summary="Confirm threats on a domain or one URL")
def record(body: RecordIn, request: Request, principal: Principal = Depends(require("reviews:write")),
           session: Session = Depends(get_session)) -> ChangeOut:
    types = _types(body.types)
    reference = "admin"
    if body.url is not None:
        url = _url(session, body.url, create=True)
        if url is None:  # create=True always returns a row; kept explicit rather than an assert
            raise ApiError(500, "internal_error", "URL record could not be created")
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
    audit.record(session, action="threat.record", target_type=scope, target_id=target,
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
    audit.record(session, action="threat.withdraw", target_type=scope, target_id=target,
                 after={"type": body.type, "reason": body.reason, "withdrawn": changed}, principal=principal,
                 request_id=getattr(request.state, "request_id", None))
    session.commit()
    return ChangeOut(target=target, scope=scope, changed=changed)


# --- USOM report queue (threats/usom.py) -----------------------------------------------------------------------------

class UsomItem(BaseModel):
    domain: str
    defanged: str
    types: list[str]
    brand: str | None
    confirmed_by: list[str]  # review, detector, octryx
    first_seen: datetime | None
    checked_at: datetime | None  # the national list's last answer
    status_at: datetime | None   # reported or set aside
    listed_at: datetime | None   # the list had it, after our report
    days_to_list: float | None
    mail_id: int | None
    sending: bool
    still_flagged: bool


class UsomMailSetup(BaseModel):
    ready: bool
    sender: str | None
    recipient: str


class UsomQueueOut(BaseModel):
    view: str
    counts: dict[str, int]
    median_days_to_list: float | None
    mail: UsomMailSetup
    items: list[UsomItem]


class UsomStatusIn(BaseModel):
    domains: list[str] = Field(min_length=1, max_length=500)
    action: Literal["dismissed", "pending"]


class UsomSendIn(BaseModel):
    domains: list[str] = Field(min_length=1, max_length=100)
    subject: str = Field(min_length=1, max_length=200)
    body: str = Field(min_length=1, max_length=60000)  # 100 domains with type, brand and date fit easily


class UsomMailOut(BaseModel):
    id: int
    status: str
    recipient: str
    domains: list[str]
    error: str | None
    created_at: datetime
    sent_at: datetime | None


def _mail_out(m) -> UsomMailOut:
    return UsomMailOut(id=m.id, status=m.status, recipient=m.recipient, domains=m.domains, error=m.error,
                       created_at=m.created_at, sent_at=m.sent_at)


@router.get("/usom", response_model=UsomQueueOut,
            summary="Our confirmed threats and the national list: waiting to report, reported, listed, set aside")
def usom_queue(view: Literal["pending", "reported", "listed", "dismissed"] = "pending",
               _: Principal = Depends(require("reviews:write")), session: Session = Depends(get_session)) -> UsomQueueOut:
    from common.config import get_settings
    from threats import usom

    settings = get_settings()
    data = usom.queue(session, view)
    return UsomQueueOut(view=view, counts=data["counts"], median_days_to_list=data["median_days_to_list"],
                        mail=UsomMailSetup(ready=usom.mail_ready(settings), sender=settings.usom_mail_from.strip() or None,
                                           recipient=settings.usom_mail_to.strip()),
                        items=[UsomItem(**i) for i in data["items"]])


@router.post("/usom/status", summary="Set domains aside (not to report) or put them back in the queue")
def usom_status(body: UsomStatusIn, request: Request, principal: Principal = Depends(require("reviews:write")),
                session: Session = Depends(get_session)) -> dict:
    from threats import usom

    changed = usom.set_status(session, body.domains, body.action, principal.user_id)
    audit.record(session, action=f"usom.{body.action}", target_type="domains", target_id=",".join(changed[:3]),
                 after={"domains": changed}, principal=principal, request_id=getattr(request.state, "request_id", None))
    session.commit()
    return {"changed": len(changed)}


@router.post("/usom/send", response_model=UsomMailOut, status_code=202,
             summary="Queue a report e-mail to USOM; the worker sends it and marks the domains reported")
def usom_send(body: UsomSendIn, request: Request, principal: Principal = Depends(require("reviews:write")),
              session: Session = Depends(get_session)) -> UsomMailOut:
    from common.config import get_settings
    from threats import usom

    from ..worker import celery_app

    settings = get_settings()
    if not usom.mail_ready(settings):
        raise ApiError(409, "mail_not_configured", "set CATEGORY_USOM_MAIL_FROM and CATEGORY_RESEND_API_KEY first")
    try:
        mail = usom.queue_mail(session, body.domains, body.subject, body.body, recipient=settings.usom_mail_to,
                               user_id=principal.user_id)
    except ValueError as exc:
        raise ApiError(422, "validation_error", str(exc)) from exc
    audit.record(session, action="usom.emailed", target_type="usom_mail", target_id=mail.id,
                 after={"domains": mail.domains, "recipient": mail.recipient, "subject": mail.subject},
                 principal=principal, request_id=getattr(request.state, "request_id", None))
    session.commit()
    celery_app.send_task("category.threats.usom_mail", args=[mail.id])
    return _mail_out(mail)


@router.get("/usom/mails/{mail_id}", response_model=UsomMailOut, summary="One report e-mail: queued, sent or failed")
def usom_mail(mail_id: int, _: Principal = Depends(require("reviews:write")),
              session: Session = Depends(get_session)) -> UsomMailOut:
    from database.models import UsomMail

    mail = session.get(UsomMail, mail_id)
    if mail is None:
        raise ApiError(404, "not_found", "no such mail")
    return _mail_out(mail)
