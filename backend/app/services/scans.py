"""Scan queue management (database side)."""
from __future__ import annotations

import base64
import json
import uuid
from datetime import datetime

from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import Session

from database.models import Domain, JobStatus, ScanJob


class CursorError(ValueError):
    pass


def encode_cursor(job: ScanJob) -> str:
    raw = json.dumps([job.queued_at.isoformat(), str(job.id)]).encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def decode_cursor(cursor: str) -> tuple[datetime, uuid.UUID]:
    try:
        raw = base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4))
        queued_at, job_id = json.loads(raw)
        return datetime.fromisoformat(queued_at), uuid.UUID(job_id)
    except (ValueError, TypeError) as exc:
        raise CursorError("invalid cursor") from exc


def list_jobs(session: Session, *, status: JobStatus | None, limit: int, cursor: str | None
              ) -> tuple[list[tuple[ScanJob, str]], str | None]:
    """Newest first, keyset-paginated on (queued_at, id)."""
    stmt = (select(ScanJob, Domain.normalized_domain).join(Domain, Domain.id == ScanJob.domain_id)
            .order_by(ScanJob.queued_at.desc(), ScanJob.id.desc()).limit(limit + 1))
    if status is not None:
        stmt = stmt.where(ScanJob.status == status)
    if cursor:
        queued_at, job_id = decode_cursor(cursor)
        stmt = stmt.where(or_(ScanJob.queued_at < queued_at,
                              and_(ScanJob.queued_at == queued_at, ScanJob.id < job_id)))
    rows = [(job, name) for job, name in session.execute(stmt).all()]
    next_cursor = encode_cursor(rows[limit - 1][0]) if len(rows) > limit else None
    return rows[:limit], next_cursor


def status_counts(session: Session) -> dict[str, int]:
    counts = {s.value: 0 for s in JobStatus}
    for status, n in session.execute(select(ScanJob.status, func.count()).group_by(ScanJob.status)):
        counts[JobStatus(status).value] = n
    return counts


def open_job_for(session: Session, domain_id: int) -> ScanJob | None:
    return session.scalar(select(ScanJob).where(ScanJob.domain_id == domain_id,
                                                ScanJob.status.in_([JobStatus.QUEUED, JobStatus.RUNNING])))


def cancel(session: Session, job: ScanJob) -> None:
    job.status = JobStatus.CANCELLED
    job.finished_at = func.now()
