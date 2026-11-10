"""Reading and writing threat records under the module's two rules:

1. **Visibility.** A record is shown publicly only when it is active and its source's licence allows
   redistribution (``public``). Internal-use-only data (e.g. a feed we may read but not republish) stays inside.
2. **Scope.** A URL-scoped record never flags the whole domain (``domain_scoped`` / ``url_scoped``).

URL texts are never published by the API: phishing links often carry the victim's e-mail address or a token in
the query string. The API answers "is this exact URL reported?" and "how many URLs on this host are reported?".
"""
from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import ColumnElement, Select, select, update
from sqlalchemy.orm import Session

from classification.engine import OWN_THREAT_SOURCES, REVIEW_SOURCE, source_id
from common.domains import normalize_url
from database.models import DataSource, Domain, SourceLicense, ThreatIntelligence, ThreatType, Url


def public(stmt: Select) -> Select:
    """Limit a select over ThreatIntelligence to records that may be shown publicly. Adds the joins to
    DataSource and SourceLicense, so DataSource columns can be selected without joining again."""
    return (stmt.join(DataSource, DataSource.id == ThreatIntelligence.source_id)
            .join(SourceLicense, SourceLicense.id == DataSource.license_id)
            .where(ThreatIntelligence.is_active, SourceLicense.redistribution_allowed))


def domain_scoped() -> ColumnElement[bool]:
    return ThreatIntelligence.url_id.is_(None)


def url_scoped() -> ColumnElement[bool]:
    return ThreatIntelligence.url_id.is_not(None)


class UnknownDomain(LookupError):
    """The URL's host is not in the database yet; add the domain first (it then gets scanned and categorised)."""


def url_row(session: Session, raw_url: str, *, create: bool = True) -> Url | None:
    """The Url row for a URL (normalised), created on demand. The host must already exist as a domain."""
    normalized = normalize_url(raw_url)
    row = session.scalar(select(Url).where(Url.url_hash == normalized.sha256))
    if row is not None or not create:
        return row
    domain_id = session.scalar(select(Domain.id).where(Domain.normalized_domain == normalized.domain.ascii))
    if domain_id is None:
        raise UnknownDomain(normalized.domain.ascii)
    row = Url(domain_id=domain_id, url=normalized.url, normalized_url=normalized.url, url_hash=normalized.sha256)
    session.add(row)
    session.flush()
    return row


def record_url_threats(session: Session, raw_url: str, risk_ids: list[str], *, source_slug: str = REVIEW_SOURCE,
                       reference: str | None = None) -> tuple[Url, int]:
    """Confirm threats on one URL only. Returns the Url row and how many new records were added (existing ones
    are re-activated)."""
    url = url_row(session, raw_url)
    if url is None:  # create=True always returns a row
        raise RuntimeError("URL record could not be created")
    src = source_id(session, source_slug)
    now = datetime.now(UTC)
    added = 0
    for rid in risk_ids:
        threat = ThreatType(rid)
        row = session.scalar(select(ThreatIntelligence).where(
            ThreatIntelligence.url_id == url.id, ThreatIntelligence.threat_type == threat,
            ThreatIntelligence.source_id == src))
        if row is None:
            session.add(ThreatIntelligence(domain_id=url.domain_id, url_id=url.id, threat_type=threat,
                                           source_id=src, source_reference=reference))
            added += 1
        else:
            row.is_active, row.last_seen = True, now
    url.last_seen = now
    return url, added


def withdraw(session: Session, *, domain: Domain, threat: ThreatType | None = None, url: Url | None = None,
             source_slugs: tuple[str, ...] = OWN_THREAT_SOURCES) -> int:
    """Deactivate our own records (a reviewer's, or Octryx's): after a false-positive report is accepted, or when a reviewer
    takes a flag back. Records stay for the history (``is_active`` false). Third-party feed records are not
    touched: the feed decides, and the next sync would bring them back anyway. Returns the number withdrawn.

    ``url`` None withdraws only the domain-scoped records; pass a Url to withdraw that URL's records."""
    own = select(DataSource.id).where(DataSource.slug.in_(source_slugs))
    stmt = update(ThreatIntelligence).where(
        ThreatIntelligence.domain_id == domain.id, ThreatIntelligence.source_id.in_(own), ThreatIntelligence.is_active,
        domain_scoped() if url is None else ThreatIntelligence.url_id == url.id)
    if threat is not None:
        stmt = stmt.where(ThreatIntelligence.threat_type == threat)
    return session.execute(stmt.values(is_active=False)).rowcount or 0
