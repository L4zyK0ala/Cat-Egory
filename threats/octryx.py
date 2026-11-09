"""Findings from Octryx, the sister project that hunts brand look-alikes.

Octryx proves it on the page (a login form naming the brand, the brand's icon on a foreign host) and Cat-Egory lists
it without a person, as the operator decided. Everything else goes to a person, through the threat-candidate queue:

* a finding public only because of a third-party list: that list's verdict must not be republished as ours,
* anything under a popular or official domain or a restricted suffix (threats/guard.py),
* anything beyond ``octryx_auto_flag_max_per_run``, so a fault in Octryx cannot flood the public lists.

A domain with an Octryx record is never flagged again by this sync: a withdrawn or expired record stays that way.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field

import httpx
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from classification.engine import BRAND_WATCH_SOURCE, OCTRYX_SOURCE, record_threats
from common.domains import DomainError, normalize_domain
from database.models import DataSource, Domain, DomainSourceRecord, ScanReason, ThreatIntelligence

from .guard import protected_owner
from .records import domain_scoped

log = logging.getLogger("category.threats.octryx")
THREATS = ["phishing", "lookalike-domain"]
PRIORITY = 3


@dataclass
class Result:
    flagged: list[dict] = field(default_factory=list)  # {"domain", "brand"}
    candidates: int = 0
    known: int = 0
    invalid: int = 0


def fetch(client: httpx.Client, base_url: str) -> list[dict]:
    response = client.get(f"{base_url.rstrip('/')}/api/export/detections")
    response.raise_for_status()
    items = response.json()["items"]
    if not isinstance(items, list):
        raise ValueError("unexpected answer")
    return items


def client(token: str) -> httpx.Client:
    return httpx.Client(timeout=30, follow_redirects=False,
                        headers={"Authorization": f"Bearer {token}", "User-Agent": "Cat-Egory Octryx sync"})


def _source(session: Session, slug: str) -> DataSource:
    source = session.scalar(select(DataSource).where(DataSource.slug == slug))
    if source is None:
        raise RuntimeError(f"data source {slug} missing: run the seed (migrate container)")
    return source


def _has_record(session: Session, name: str, source_id: int) -> bool:
    return session.scalar(
        select(ThreatIntelligence.id).join(Domain, Domain.id == ThreatIntelligence.domain_id)
        .where(Domain.normalized_domain == name, ThreatIntelligence.source_id == source_id, domain_scoped()).limit(1)
    ) is not None


def _add(session: Session, name: str, source: DataSource) -> Domain:
    from backend.app.services.domains import add_domains

    add_domains(session, [(1, name)], source=source, scan="new", reason=ScanReason.DISCOVERY, priority=PRIORITY)
    return session.scalar(select(Domain).where(Domain.normalized_domain == name))


def ingest(session: Session, items: list[dict], *, max_auto: int) -> Result:
    result = Result()
    octryx, watch = _source(session, OCTRYX_SOURCE), _source(session, BRAND_WATCH_SOURCE)
    for item in items:
        try:
            name = normalize_domain(str(item["domain"])).ascii
            brand = str(item["brand"])[:64]
        except (KeyError, DomainError):
            result.invalid += 1
            continue
        if _has_record(session, name, octryx.id):
            result.known += 1
            continue
        if item.get("own_evidence") is True and len(result.flagged) < max_auto and protected_owner(session, name) is None:
            domain = _add(session, name, octryx)
            record_threats(session, domain, THREATS, source_slug=OCTRYX_SOURCE, reference=f"octryx:{item.get('id')}")
            session.flush()
            session.execute(update(ThreatIntelligence)
                            .where(ThreatIntelligence.domain_id == domain.id, ThreatIntelligence.source_id == octryx.id)
                            .values(raw={"evidence": f"Octryx saw a page imitating {brand}"}))
            result.flagged.append({"domain": name, "brand": brand})
            continue
        domain = _add(session, name, watch)  # the look-alike path: scanned, then always a person
        session.execute(update(DomainSourceRecord)
                        .where(DomainSourceRecord.source_id == watch.id, DomainSourceRecord.domain_id == domain.id)
                        .values(source_category=f"look-alike: {brand} (Octryx)"[:200]))
        result.candidates += 1
    return result
