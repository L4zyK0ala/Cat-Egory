"""Admin API: domain management and scan queue. Every endpoint needs an API key with the right scope;
every write is audit-logged in the same transaction."""
from __future__ import annotations

import hashlib
import uuid
from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Depends, File, Form, Query, Request, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from common.config import get_settings
from common.domains import DomainError, normalize_domain
from database.models import Domain, JobStatus, ScanJob, ScanReason
from database.session import get_session

from .. import audit
from ..deps import Principal, get_principal, rate_limit, require
from ..errors import ApiError
from ..security import SCOPES
from ..services import scans as scan_service
from ..services.domains import AddResult, add_domains, get_source, queue_scans
from ..services.imports import ImportFileError, parse_upload

router = APIRouter(prefix="/api/v1/admin", tags=["admin"], dependencies=[Depends(rate_limit("write"))])

ScanMode = Literal["new", "all", "none"]


# ----------------------------------------------------------------------------- schemas


class RowErrorOut(BaseModel):
    row: int
    value: str
    code: str
    message: str


class AddResultOut(BaseModel):
    received: int
    valid: int
    duplicates_in_input: int
    created: int
    existing: int
    scans_queued: int
    invalid: list[RowErrorOut]
    created_domains: list[str] = Field(description="up to the first 100 newly created domains")

    @classmethod
    def of(cls, r: AddResult) -> AddResultOut:
        return cls(received=r.received, valid=r.valid, duplicates_in_input=r.duplicates_in_input, created=r.created,
                   existing=r.existing, scans_queued=r.scans_queued, created_domains=r.created_domains,
                   invalid=[RowErrorOut(row=e.row, value=e.value, code=e.code, message=e.message)
                            for e in r.invalid[:1000]])


class AddDomainsIn(BaseModel):
    domains: list[str] = Field(min_length=1, description="domains or URLs; normalised server-side")
    source: str = Field(default="manual", description="slug of an enabled data source")
    scan: ScanMode = Field(default="new", description="queue scans for new domains, all given domains, or none")
    priority: int = Field(default=5, ge=0, le=9)


class ScanIn(BaseModel):
    domain: str
    priority: int = Field(default=5, ge=0, le=9)


class ScanOut(BaseModel):
    id: uuid.UUID
    domain: str
    status: str
    reason: str
    priority: int
    attempts: int
    error_code: str | None
    queued_at: datetime
    started_at: datetime | None
    finished_at: datetime | None

    @classmethod
    def of(cls, job: ScanJob, domain: str) -> ScanOut:
        return cls(id=job.id, domain=domain, status=str(job.status), reason=str(job.reason), priority=job.priority,
                   attempts=job.attempts, error_code=job.error_code, queued_at=job.queued_at,
                   started_at=job.started_at, finished_at=job.finished_at)


class ScanPage(BaseModel):
    items: list[ScanOut]
    next_cursor: str | None


class WhoAmI(BaseModel):
    user_id: int
    role: str
    api_key_id: int
    scopes: list[str]


# ----------------------------------------------------------------------------- helpers


def _request_id(request: Request) -> str | None:
    return getattr(request.state, "request_id", None)


def _source(session: Session, slug: str):
    source = get_source(session, slug)
    if source is None:
        raise ApiError(422, "unknown_source", f"no enabled data source named {slug!r}")
    return source


def _check_size(n: int) -> None:
    limit = get_settings().max_import_rows
    if n > limit:
        raise ApiError(413, "too_many_rows", f"at most {limit} domains per request")


# ----------------------------------------------------------------------------- endpoints


@router.get("/me", response_model=WhoAmI, summary="Who am I (API key check)")
def whoami(principal: Principal = Depends(get_principal)) -> WhoAmI:
    if not principal.authenticated:
        raise ApiError(401, "authentication_required", "an API key is required", headers={"WWW-Authenticate": "Bearer"})
    return WhoAmI(user_id=principal.user_id, role=str(principal.role), api_key_id=principal.api_key_id,
                  scopes=sorted(principal.scopes))


@router.get("/scopes", summary="Available API key scopes")
def list_scopes(_: Principal = Depends(require("scans:read"))) -> dict[str, str]:
    return SCOPES


@router.post("/domains", response_model=AddResultOut, summary="Add domains (single or bulk)")
def add(body: AddDomainsIn, request: Request, principal: Principal = Depends(require("domains:write")),
        session: Session = Depends(get_session)) -> AddResultOut:
    _check_size(len(body.domains))
    source = _source(session, body.source)
    result = add_domains(session, list(enumerate(body.domains, start=1)), source=source, scan=body.scan,
                         reason=ScanReason.MANUAL, priority=body.priority, requested_by_id=principal.user_id)
    audit.record(session, action="domains.add", target_type="data_source", target_id=source.slug,
                 after=result.summary() | {"scan": body.scan}, principal=principal, request_id=_request_id(request))
    session.commit()
    return AddResultOut.of(result)


@router.post("/domains/import", response_model=AddResultOut, summary="Import a CSV / TSV / TXT / hosts / JSON / XLSX file")
async def import_file(request: Request, file: UploadFile = File(...), source: str = Form("file-import"),
                      scan: ScanMode = Form("new"), priority: int = Form(5, ge=0, le=9),
                      principal: Principal = Depends(require("domains:write")),
                      session: Session = Depends(get_session)) -> AddResultOut:
    settings = get_settings()
    content = await file.read(settings.max_upload_bytes + 1)
    if len(content) > settings.max_upload_bytes:
        raise ApiError(413, "payload_too_large", f"file is larger than {settings.max_upload_bytes} bytes")
    try:
        rows = parse_upload(file.filename or "", content, settings.max_import_rows)
    except ImportFileError as exc:
        status = 413 if exc.code in ("too_many_rows", "too_large") else 415 if exc.code == "unsupported_format" else 422
        raise ApiError(status, exc.code, str(exc)) from exc
    src = _source(session, source)
    result = add_domains(session, rows, source=src, scan=scan, reason=ScanReason.IMPORT, priority=priority,
                         requested_by_id=principal.user_id)
    audit.record(session, action="domains.import", target_type="data_source", target_id=src.slug,
                 after=result.summary() | {"filename": (file.filename or "")[:200], "bytes": len(content),
                                           "sha256": hashlib.sha256(content).hexdigest(), "scan": scan},
                 principal=principal, request_id=_request_id(request))
    session.commit()
    return AddResultOut.of(result)


@router.get("/scans", response_model=ScanPage, summary="List scan jobs (newest first)")
def list_scans(status: JobStatus | None = None, limit: int = Query(50, ge=1, le=500), cursor: str | None = None,
               _: Principal = Depends(require("scans:read")), session: Session = Depends(get_session)) -> ScanPage:
    try:
        rows, next_cursor = scan_service.list_jobs(session, status=status, limit=limit, cursor=cursor)
    except scan_service.CursorError as exc:
        raise ApiError(422, "invalid_cursor", "invalid cursor") from exc
    return ScanPage(items=[ScanOut.of(job, name) for job, name in rows], next_cursor=next_cursor)


@router.get("/scans/stats", summary="Scan job counts by status")
def scan_stats(_: Principal = Depends(require("scans:read")), session: Session = Depends(get_session)) -> dict[str, int]:
    return scan_service.status_counts(session)


@router.post("/scans", response_model=ScanOut, status_code=201, summary="Queue a scan for a known domain")
def create_scan(body: ScanIn, request: Request, principal: Principal = Depends(require("scans:write")),
                session: Session = Depends(get_session)) -> ScanOut:
    try:
        name = normalize_domain(body.domain).ascii
    except DomainError as exc:
        raise ApiError(422, "invalid_domain", str(exc), details={"code": exc.code}) from exc
    domain = session.query(Domain).filter_by(normalized_domain=name).one_or_none()
    if domain is None:
        raise ApiError(404, "not_found", "unknown domain; add it first with POST /api/v1/admin/domains")
    if not queue_scans(session, [domain.id], reason=ScanReason.RESCAN, priority=body.priority,
                       requested_by_id=principal.user_id):
        existing = scan_service.open_job_for(session, domain.id)
        raise ApiError(409, "scan_already_open", "this domain already has a queued or running scan",
                       details={"scan_id": str(existing.id) if existing else None})
    job = scan_service.open_job_for(session, domain.id)
    audit.record(session, action="scan.create", target_type="scan_job", target_id=job.id,
                 after={"domain": name, "priority": body.priority}, principal=principal,
                 request_id=_request_id(request))
    session.commit()
    session.refresh(job)
    return ScanOut.of(job, name)


@router.post("/scans/{scan_id}/cancel", response_model=ScanOut, summary="Cancel a queued scan")
def cancel_scan(scan_id: uuid.UUID, request: Request, principal: Principal = Depends(require("scans:write")),
                session: Session = Depends(get_session)) -> ScanOut:
    job = session.get(ScanJob, scan_id, with_for_update=True)
    if job is None:
        raise ApiError(404, "not_found", "scan not found")
    if job.status != JobStatus.QUEUED:
        raise ApiError(409, "not_cancellable", f"only queued scans can be cancelled (status: {job.status})")
    scan_service.cancel(session, job)
    audit.record(session, action="scan.cancel", target_type="scan_job", target_id=job.id,
                 before={"status": "queued"}, after={"status": "cancelled"}, principal=principal,
                 request_id=_request_id(request))
    session.commit()
    session.refresh(job)
    return ScanOut.of(job, session.get(Domain, job.domain_id).normalized_domain)
