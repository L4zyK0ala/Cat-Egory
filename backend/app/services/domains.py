"""Adding domains: normalise → de-duplicate → upsert → record provenance → queue scans.

Scan jobs are rows in ``scan_jobs`` (status ``queued``); the database is the source of truth. From Phase 3 a
dispatcher moves queued jobs to the crawler queue, so nothing is lost if Redis restarts and no message is
published for a transaction that rolled back.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Literal

from sqlalchemy import func, literal_column, select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from common.domains import DomainError, NormalizedDomain, normalize_domain
from database.models import DataSource, Domain, DomainSourceRecord, JobStatus, ScanJob, ScanReason

ScanMode = Literal["new", "all", "none"]
_CHUNK = 1000
OPEN_JOB_WHERE = text("status IN ('queued', 'running')")


@dataclass(slots=True)
class RowError:
    row: int
    value: str
    code: str
    message: str


@dataclass(slots=True)
class AddResult:
    received: int = 0
    valid: int = 0
    duplicates_in_input: int = 0
    created: int = 0
    existing: int = 0
    scans_queued: int = 0
    invalid: list[RowError] = field(default_factory=list)
    created_domains: list[str] = field(default_factory=list)  # first 100, for display

    def summary(self) -> dict:
        return {"received": self.received, "valid": self.valid, "duplicates_in_input": self.duplicates_in_input,
                "created": self.created, "existing": self.existing, "invalid": len(self.invalid),
                "scans_queued": self.scans_queued}


def get_source(session: Session, slug: str) -> DataSource | None:
    return session.scalar(select(DataSource).where(DataSource.slug == slug, DataSource.enabled))


def normalize_rows(rows: list[tuple[int, str]]) -> tuple[list[NormalizedDomain], list[RowError], int]:
    seen: dict[str, NormalizedDomain] = {}
    errors: list[RowError] = []
    duplicates = 0
    for row, value in rows:
        try:
            nd = normalize_domain(value)
        except DomainError as exc:
            errors.append(RowError(row, value[:253], exc.code, str(exc)))
            continue
        if nd.ascii in seen:
            duplicates += 1
        else:
            seen[nd.ascii] = nd
    return list(seen.values()), errors, duplicates


def queue_scans(session: Session, domain_ids: list[int], *, reason: ScanReason, priority: int,
                requested_by_id: int | None) -> int:
    """Create queued jobs; domains that already have an open job are skipped (unique partial index)."""
    queued = 0
    for start in range(0, len(domain_ids), _CHUNK):
        chunk = domain_ids[start:start + _CHUNK]
        stmt = insert(ScanJob).values([
            {"id": uuid.uuid4(), "domain_id": d, "reason": reason, "priority": priority,
             "status": JobStatus.QUEUED, "requested_by_id": requested_by_id} for d in chunk
        ]).on_conflict_do_nothing(index_elements=["domain_id"], index_where=OPEN_JOB_WHERE).returning(ScanJob.id)
        queued += len(session.execute(stmt).all())
    return queued


def add_domains(session: Session, rows: list[tuple[int, str]], *, source: DataSource, scan: ScanMode = "new",
                reason: ScanReason = ScanReason.MANUAL, priority: int = 5,
                requested_by_id: int | None = None) -> AddResult:
    result = AddResult(received=len(rows))
    domains, result.invalid, result.duplicates_in_input = normalize_rows(rows)
    result.valid = len(domains)
    license_slug = source.license.slug if source.license else None

    new_ids: list[int] = []
    all_ids: list[int] = []
    for start in range(0, len(domains), _CHUNK):
        chunk = domains[start:start + _CHUNK]
        stmt = insert(Domain).values([
            {"domain": d.display, "normalized_domain": d.ascii, "registrable_domain": d.registrable,
             "public_suffix": d.public_suffix, "source_id": source.id, "source_license": license_slug}
            for d in chunk
        ])
        stmt = stmt.on_conflict_do_update(index_elements=[Domain.normalized_domain],
                                          set_={"last_seen": func.now()})
        # xmax = 0 only for freshly inserted rows (PostgreSQL MVCC detail; standard upsert idiom).
        returned = session.execute(stmt.returning(Domain.id, Domain.normalized_domain,
                                                  literal_column("(xmax = 0)").label("inserted"))).all()
        for domain_id, name, inserted in returned:
            all_ids.append(domain_id)
            if inserted:
                new_ids.append(domain_id)
                if len(result.created_domains) < 100:
                    result.created_domains.append(name)

        prov = insert(DomainSourceRecord).values([{"domain_id": r[0], "source_id": source.id} for r in returned])
        session.execute(prov.on_conflict_do_update(index_elements=["domain_id", "source_id"],
                                                   set_={"last_seen": func.now(), "is_listed": True}))

    result.created, result.existing = len(new_ids), len(all_ids) - len(new_ids)
    targets = {"new": new_ids, "all": all_ids, "none": []}[scan]
    if targets:
        result.scans_queued = queue_scans(session, targets, reason=reason, priority=priority,
                                          requested_by_id=requested_by_id)
    return result
