"""Source Integration Engine.

A third-party source goes through three explicit steps:

1. ``register`` – copy its entry from ``sources.yaml`` into ``data_sources`` (disabled, no licence).
2. ``approve``  – a maintainer records the licence they actually read (SPDX id, redistribution, commercial
   use, attribution) and enables the source. This is a human decision and is audit-logged.
3. ``sync``     – download, normalise, merge, record provenance. Refused unless steps 1-2 happened.

What a sync writes
* blocklists: ``domains`` (new rows, never re-licensed) + ``domain_source_records`` (with the source's own label).
  The source's label is NOT copied into ``domain_categories``: our CC BY classification stays our own work;
  the label is a comparison signal for validation.
* threat feeds: ``threat_intelligence`` rows (deactivated again when an entry leaves the list).

Downloads use the same SSRF policy as the crawler (public addresses only, ports 80/443, no redirects to
elsewhere), a size cap and a timeout. Entries are parsed as untrusted text.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import yaml
from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from backend.app import audit
from backend.app.services.domains import normalize_rows
from backend.app.services.imports import parse_csv, parse_text
from crawler.ssrf import BlockedError, check_url, resolve_public
from database.models import (
    DataSource,
    Domain,
    DomainSourceRecord,
    SourceKind,
    SourceLicense,
    ThreatIntelligence,
    ThreatType,
)

log = logging.getLogger("category.integrations")
CATALOGUE = Path(__file__).with_name("sources.yaml")
MAX_DOWNLOAD_BYTES = 100 * 1024 * 1024
MAX_ENTRIES = 3_000_000
_CHUNK = 5000


class SourceError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def catalogue(path: Path = CATALOGUE) -> dict[str, dict[str, Any]]:
    with open(path, encoding="utf-8") as fh:
        return {s["slug"]: s for s in yaml.safe_load(fh)["sources"]}


# ----------------------------------------------------------------------------- register / approve


def register(session: Session, slug: str, entry: dict[str, Any]) -> DataSource:
    source = session.scalar(select(DataSource).where(DataSource.slug == slug))
    if source is not None:
        return source
    source = DataSource(slug=slug, name=entry["name"], kind=SourceKind(entry["kind"]), homepage_url=entry.get("homepage"),
                        feed_url=entry.get("feed_url"), data_format=entry.get("format"),
                        terms_url=entry.get("terms_url"), attribution=entry.get("attribution"),
                        update_interval_seconds=entry.get("update_interval_seconds", 86400), enabled=False)
    session.add(source)
    session.flush()
    audit.record(session, action="source.register", target_type="data_source", target_id=slug,
                 after={"kind": entry["kind"], "feed_url": entry.get("feed_url")})
    return source


def approve(session: Session, slug: str, *, license_slug: str, license_name: str, license_url: str | None,
            redistribution_allowed: bool, commercial_use_allowed: bool, attribution_required: bool,
            attribution: str | None, reviewer_id: int | None, notes: str | None = None) -> DataSource:
    source = session.scalar(select(DataSource).where(DataSource.slug == slug))
    if source is None:
        raise SourceError("unknown_source", f"source {slug!r} is not registered")
    if not source.feed_url:
        raise SourceError("no_feed_url", "set a feed URL before enabling the source")
    lic = session.scalar(select(SourceLicense).where(SourceLicense.slug == license_slug))
    if lic is None:
        lic = SourceLicense(slug=license_slug, name=license_name)
        session.add(lic)
    lic.name, lic.url = license_name, license_url
    lic.redistribution_allowed, lic.commercial_use_allowed = redistribution_allowed, commercial_use_allowed
    lic.attribution_required, lic.notes = attribution_required, notes
    lic.reviewed_at, lic.reviewed_by_id = datetime.now(UTC), reviewer_id
    session.flush()
    source.license_id, source.attribution, source.enabled = lic.id, attribution or source.attribution, True
    audit.record(session, action="source.approve", target_type="data_source", target_id=slug,
                 after={"license": license_slug, "redistribution": redistribution_allowed,
                        "commercial": commercial_use_allowed, "reviewer_id": reviewer_id})
    return source


# ----------------------------------------------------------------------------- download


def download(url: str, *, client: httpx.Client | None = None, resolver=None) -> bytes:
    try:
        target = check_url(url)
        resolve_public(target.host, target.port, **({"resolver": resolver} if resolver else {}))
    except BlockedError as exc:
        raise SourceError("blocked_url", f"feed URL refused by SSRF policy: {exc.code}") from exc
    owns = client is None
    client = client or httpx.Client(timeout=httpx.Timeout(60.0, connect=10.0), follow_redirects=False,
                                    headers={"User-Agent": "CategoryBot/0.1 (source sync)"})
    try:
        with client.stream("GET", url) as resp:
            if resp.status_code != 200:
                raise SourceError("http_status", f"feed answered HTTP {resp.status_code}")
            body = bytearray()
            for chunk in resp.iter_bytes():
                body += chunk
                if len(body) > MAX_DOWNLOAD_BYTES:
                    raise SourceError("too_large", "feed exceeds the download limit")
            return bytes(body)
    except httpx.HTTPError as exc:
        raise SourceError("download_failed", type(exc).__name__) from exc
    finally:
        if owns:
            client.close()


def parse(data: bytes, data_format: str | None) -> list[tuple[int, str]]:
    if data_format == "csv":
        rows = parse_csv(data, MAX_ENTRIES)
        return [(n, v) for n, v in rows]
    return parse_text(data, MAX_ENTRIES)  # hosts, domains, url-list (a URL's host is extracted when normalising)


# ----------------------------------------------------------------------------- sync


@dataclass
class SyncResult:
    slug: str
    entries: int = 0
    valid: int = 0
    invalid: int = 0
    new_domains: int = 0
    delisted: int = 0
    threats_active: int = 0
    errors: list[str] = field(default_factory=list)


def sync(session: Session, slug: str, *, data: bytes | None = None, client: httpx.Client | None = None,
         resolver=None, entry: dict[str, Any] | None = None) -> SyncResult:
    source = session.scalar(select(DataSource).where(DataSource.slug == slug).with_for_update())
    if source is None:
        raise SourceError("unknown_source", f"source {slug!r} is not registered")
    if not source.enabled or source.license_id is None:
        raise SourceError("not_approved", "source is disabled or has no reviewed licence; run approve first")
    entry = entry if entry is not None else catalogue().get(slug, {})
    now = datetime.now(UTC)
    source.last_sync_at = now
    result = SyncResult(slug=slug)
    try:
        raw = data if data is not None else download(source.feed_url, client=client, resolver=resolver)
        rows = parse(raw, source.data_format)
    except SourceError as exc:
        source.last_error = f"{exc.code}: {exc}"[:500]
        result.errors.append(source.last_error)
        return result

    domains, invalid, _ = normalize_rows(rows)
    result.entries, result.valid, result.invalid = len(rows), len(domains), len(invalid)
    label = entry.get("category")
    ids: list[int] = []
    for start in range(0, len(domains), _CHUNK):
        chunk = domains[start:start + _CHUNK]
        stmt = insert(Domain).values([
            {"domain": d.display, "normalized_domain": d.ascii, "registrable_domain": d.registrable,
             "public_suffix": d.public_suffix, "source_id": source.id,
             "source_license": source.license.slug if source.license else None} for d in chunk
        ]).on_conflict_do_nothing(index_elements=[Domain.normalized_domain]).returning(Domain.id)
        result.new_domains += len(session.execute(stmt).all())
        names = [d.ascii for d in chunk]
        ids += list(session.scalars(select(Domain.id).where(Domain.normalized_domain.in_(names))))

    for start in range(0, len(ids), _CHUNK):
        chunk = ids[start:start + _CHUNK]
        prov = insert(DomainSourceRecord).values([{"domain_id": i, "source_id": source.id, "source_category": label}
                                                  for i in chunk])
        session.execute(prov.on_conflict_do_update(index_elements=["domain_id", "source_id"],
                                                   set_={"last_seen": now, "is_listed": True, "source_category": label}))
    listed = set(ids)
    stale = [r for r in session.scalars(select(DomainSourceRecord.domain_id).where(
        DomainSourceRecord.source_id == source.id, DomainSourceRecord.is_listed)) if r not in listed]
    if stale:
        session.execute(update(DomainSourceRecord).where(DomainSourceRecord.source_id == source.id,
                                                         DomainSourceRecord.domain_id.in_(stale))
                        .values(is_listed=False))
    result.delisted = len(stale)

    if source.kind == SourceKind.THREAT_FEED:
        threat = ThreatType(entry.get("threat_type", "malware"))
        for start in range(0, len(ids), _CHUNK):
            chunk = ids[start:start + _CHUNK]
            stmt = insert(ThreatIntelligence).values([
                {"domain_id": i, "threat_type": threat, "source_id": source.id} for i in chunk
            ]).on_conflict_do_nothing()
            session.execute(stmt)
            session.execute(update(ThreatIntelligence).where(
                ThreatIntelligence.source_id == source.id, ThreatIntelligence.domain_id.in_(chunk))
                .values(is_active=True, last_seen=now))
        if stale:
            session.execute(update(ThreatIntelligence).where(ThreatIntelligence.source_id == source.id,
                                                             ThreatIntelligence.domain_id.in_(stale))
                            .values(is_active=False))
        result.threats_active = len(ids)

    source.last_success_at, source.last_error, source.record_count = now, None, len(ids)
    source.quality_score = round(result.valid / result.entries, 3) if result.entries else None
    audit.record(session, action="source.sync", target_type="data_source", target_id=slug,
                 after={k: v for k, v in result.__dict__.items() if k != "errors"})
    return result


def due_sources(session: Session) -> list[str]:
    now = datetime.now(UTC)
    rows = session.scalars(select(DataSource).where(DataSource.enabled, DataSource.license_id.is_not(None),
                                                    DataSource.feed_url.is_not(None)))
    return [s.slug for s in rows
            if s.last_sync_at is None or now - s.last_sync_at >= timedelta(seconds=s.update_interval_seconds or 86400)]
