"""Abuse-report drafts (threats/abuse.py): read them, then mark each one sent or dismissed. The API never sends
mail; the reviewer sends the draft from their own mail program."""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from classification.engine import OWN_THREAT_SOURCES
from database.models import AbuseReport, DataSource, Domain, ThreatIntelligence
from database.session import get_session
from threats.records import domain_scoped

from .. import audit
from ..deps import Principal, rate_limit, require
from ..errors import ApiError

router = APIRouter(prefix="/api/v1/admin/abuse-reports", tags=["threats"], dependencies=[Depends(rate_limit("write"))])
Status = Literal["draft", "sent", "dismissed", "no_contact"]


class AbuseReportOut(BaseModel):
    id: int
    domain: str
    status: Status
    kind: str
    recipient: str | None
    organisation: str | None
    types: list[str]
    subject: str
    body: str
    created_at: datetime
    decided_at: datetime | None
    threat_active: bool  # still flagged by us; false = expired or withdrawn since
    web_form: str | None = None  # the provider prefers this form to mail


class AbuseReportPage(BaseModel):
    counts: dict[str, int]
    items: list[AbuseReportOut]


def _active(session: Session, domain_ids: set[int]) -> set[int]:
    if not domain_ids:
        return set()
    return set(session.scalars(
        select(ThreatIntelligence.domain_id).join(DataSource, DataSource.id == ThreatIntelligence.source_id)
        .where(ThreatIntelligence.domain_id.in_(domain_ids), ThreatIntelligence.is_active, domain_scoped(),
               DataSource.slug.in_(OWN_THREAT_SOURCES))))


def _out(report: AbuseReport, domain: str, active: set[int]) -> AbuseReportOut:
    return AbuseReportOut(id=report.id, domain=domain, status=report.status, kind=report.kind, recipient=report.recipient,
                          organisation=report.organisation, types=report.threat_types, subject=report.subject,
                          body=report.body, created_at=report.created_at, decided_at=report.decided_at,
                          threat_active=report.domain_id in active, web_form=(report.lookup or {}).get("form"))


@router.get("", response_model=AbuseReportPage, summary="Abuse-report drafts and what became of them")
def list_reports(status: Status | Literal["all"] = "draft", limit: int = Query(200, ge=1, le=1000),
                 _: Principal = Depends(require("reviews:write")),
                 session: Session = Depends(get_session)) -> AbuseReportPage:
    counts = dict(session.execute(select(AbuseReport.status, func.count()).group_by(AbuseReport.status)).all())
    stmt = (select(AbuseReport, Domain.normalized_domain).join(Domain, Domain.id == AbuseReport.domain_id)
            .order_by(AbuseReport.created_at.desc(), AbuseReport.id).limit(limit))
    if status != "all":
        stmt = stmt.where(AbuseReport.status == status)
    rows = session.execute(stmt).all()
    active = _active(session, {r.domain_id for r, _ in rows})
    return AbuseReportPage(counts={s: counts.get(s, 0) for s in ("draft", "sent", "dismissed", "no_contact")},
                           items=[_out(r, d, active) for r, d in rows])


def _decide(report_id: int, new: Status, allowed: tuple[str, ...], request: Request, principal: Principal,
            session: Session) -> AbuseReportOut:
    report = session.get(AbuseReport, report_id, with_for_update=True)
    if report is None:
        raise ApiError(404, "not_found", "abuse report not found")
    if report.status not in allowed:
        raise ApiError(409, "already_decided", f"report is {report.status}")
    before = report.status
    report.status, report.decided_at, report.decided_by_id = new, datetime.now(UTC), principal.user_id
    domain = session.get(Domain, report.domain_id).normalized_domain
    audit.record(session, action=f"abuse.{new}", target_type="domain", target_id=domain,
                 before={"status": before}, after={"status": new, "report": report.id, "recipient": report.recipient},
                 principal=principal, request_id=getattr(request.state, "request_id", None))
    session.commit()
    return _out(report, domain, _active(session, {report.domain_id}))


@router.post("/{report_id}/sent", response_model=AbuseReportOut, summary="A person sent this draft")
def mark_sent(report_id: int, request: Request, principal: Principal = Depends(require("reviews:write")),
              session: Session = Depends(get_session)) -> AbuseReportOut:
    return _decide(report_id, "sent", ("draft",), request, principal, session)


@router.post("/{report_id}/dismiss", response_model=AbuseReportOut, summary="Do not send this draft")
def dismiss(report_id: int, request: Request, principal: Principal = Depends(require("reviews:write")),
            session: Session = Depends(get_session)) -> AbuseReportOut:
    return _decide(report_id, "dismissed", ("draft", "no_contact"), request, principal, session)


@router.post("/run", status_code=202, summary="Prepare drafts now (normally hourly)")
def run_now(_: Principal = Depends(require("reviews:write"))) -> dict:
    from ..worker import celery_app

    celery_app.send_task("category.threats.abuse_drafts")
    return {"queued": True}
