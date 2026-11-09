"""Feed generation.

Outputs (per content category and per security-threat category, plus combined files):

* ``<category>.txt``    one domain per line
* ``<category>.hosts``  ``0.0.0.0 domain`` (hosts-file blockers, Pi-hole)
* ``<category>.rpz``    DNS Response Policy Zone (BIND/Unbound/PowerDNS); answers NXDOMAIN for domain + subdomains
* ``all.csv`` / ``all.json``  every published domain with its categories
* ``rpz``               (served name) = ``security.rpz``: every domain with an active domain-scoped threat
  record (single reported URLs are not in domain feeds: blocking them would block the whole host)

Every file carries title, version, last-modified, taxonomy version, licence and attribution. Only published
domains (see ``backend.app.publication``) are included. Content categories and our own threat confirmations
are CC BY 4.0; third-party threat data is not mixed in.

A build writes a new release only for files whose content changed (SHA-256 compared with the latest release).
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
import logging
import re
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import Integer, cast, func, select
from sqlalchemy.orm import Session

from classification.engine import OWN_THREAT_SOURCES
from classification.taxonomy import Taxonomy
from database.models import DataSource, Domain, DomainCategory, FeedRelease, ThreatIntelligence
from database.publication import OWN_DATA_LICENSE, publishable, published
from threats.records import domain_scoped

log = logging.getLogger("category.feeds")
LICENSE_URL = "https://creativecommons.org/licenses/by/4.0/"
ATTRIBUTION = "Cat-egory contributors"
FEED_NAME_RE = re.compile(r"^[a-z0-9-]+\.(txt|hosts|rpz|csv|json)$")
MEDIA_TYPES = {"txt": "text/plain; charset=utf-8", "hosts": "text/plain; charset=utf-8",
               "rpz": "text/dns; charset=utf-8", "csv": "text/csv; charset=utf-8", "json": "application/json"}
RPZ_ORIGIN = "rpz.cat-egory."
THREAT_SOURCES = OWN_THREAT_SOURCES  # threat records we may redistribute under our own licence


@dataclass
class FeedFile:
    name: str
    body: bytes
    records: int

    @property
    def sha256(self) -> str:
        return hashlib.sha256(self.body).hexdigest()


@dataclass
class Meta:
    version: str
    generated_at: datetime
    taxonomy_version: str

    def comments(self, title: str, records: int, prefix: str = "#") -> str:
        return "\n".join(f"{prefix} {line}" for line in (
            f"Title: Cat-egory - {title}", f"Version: {self.version}",
            f"Last-Modified: {self.generated_at.isoformat(timespec='seconds')}",
            f"Taxonomy: {self.taxonomy_version}", f"License: {OWN_DATA_LICENSE} ({LICENSE_URL})",
            f"Attribution: {ATTRIBUTION}", f"Entries: {records}",
        )) + "\n"


# ----------------------------------------------------------------------------- data


def published_rows(session: Session) -> list[tuple[str, str, list[str], float | None, str, datetime]]:
    rows = session.execute(
        select(Domain.normalized_domain, Domain.primary_category_id, Domain.secondary_categories, Domain.confidence,
               Domain.classification_status, Domain.updated_at)
        .where(published(), Domain.primary_category_id.is_not(None)).order_by(Domain.normalized_domain)
    ).all()
    return [(d, p, list(s or []), float(c) if c is not None else None, str(st), u) for d, p, s, c, st, u in rows]


def category_members(session: Session) -> dict[str, list[str]]:
    members: dict[str, list[str]] = {}
    for category_id, domain in session.execute(
        select(DomainCategory.category_id, Domain.normalized_domain)
        .join(Domain, Domain.id == DomainCategory.domain_id)
        .where(DomainCategory.valid_to.is_(None), published()).order_by(Domain.normalized_domain)
    ):
        members.setdefault(category_id, []).append(domain)
    return members


def threat_members(session: Session) -> dict[str, list[str]]:
    members: dict[str, list[str]] = {}
    for threat, domain in session.execute(
        select(ThreatIntelligence.threat_type, Domain.normalized_domain)
        .join(Domain, Domain.id == ThreatIntelligence.domain_id)
        .join(DataSource, DataSource.id == ThreatIntelligence.source_id)
        .where(ThreatIntelligence.is_active, Domain.is_active, publishable(), DataSource.slug.in_(THREAT_SOURCES),
               domain_scoped())  # a phishing page on a hacked site must not block the whole site
        .distinct().order_by(Domain.normalized_domain)
    ):
        members.setdefault(str(threat), []).append(domain)
    return members


# ----------------------------------------------------------------------------- formats
# Domains are normalised ASCII (a-z 0-9 - _ .), so they cannot inject CSV formulas, zone directives or
# extra lines. Free text (descriptions, model claims) is never written to feeds.


def txt(meta: Meta, title: str, domains: list[str]) -> bytes:
    return (meta.comments(title, len(domains)) + "".join(f"{d}\n" for d in domains)).encode()


def hosts(meta: Meta, title: str, domains: list[str]) -> bytes:
    return (meta.comments(title, len(domains)) + "".join(f"0.0.0.0 {d}\n" for d in domains)).encode()


def rpz(meta: Meta, title: str, domains: list[str]) -> bytes:
    serial = meta.generated_at.strftime("%Y%m%d") + meta.version.rsplit(".", 1)[-1].zfill(2)[-2:]
    head = (f"$TTL 300\n$ORIGIN {RPZ_ORIGIN}\n"
            f"@ SOA localhost. hostmaster.localhost. ({serial} 3600 600 86400 300)\n@ NS localhost.\n")
    body = "".join(f"{d} CNAME .\n*.{d} CNAME .\n" for d in domains)
    return (meta.comments(title, len(domains), prefix=";") + head + body).encode()


def csv_all(meta: Meta, rows) -> bytes:
    buf = io.StringIO()
    buf.write(meta.comments("all domains", len(rows)))
    writer = csv.writer(buf, lineterminator="\n")
    writer.writerow(["domain", "primary_category", "secondary_categories", "confidence", "status", "last_updated"])
    for d, primary, secondary, confidence, status, updated in rows:
        writer.writerow([d, primary, "|".join(secondary), "" if confidence is None else f"{confidence:.3f}", status,
                         updated.isoformat(timespec="seconds")])
    return buf.getvalue().encode()


def json_all(meta: Meta, rows) -> bytes:
    doc = {
        "meta": {"title": "Cat-egory - all domains", "version": meta.version,
                 "last_modified": meta.generated_at.isoformat(timespec="seconds"),
                 "taxonomy_version": meta.taxonomy_version, "license": OWN_DATA_LICENSE, "license_url": LICENSE_URL,
                 "attribution": ATTRIBUTION, "entries": len(rows)},
        "domains": [{"domain": d, "primary_category": p, "secondary_categories": s, "confidence": c, "status": st,
                     "last_updated": u.isoformat(timespec="seconds")} for d, p, s, c, st, u in rows],
    }
    return json.dumps(doc, ensure_ascii=False, separators=(",", ":")).encode()


# ----------------------------------------------------------------------------- build


def next_version(session: Session, now: datetime) -> str:
    prefix = now.strftime("%Y.%m.%d")
    # as numbers: compared as text, ".9" sorts after ".10" and the eleventh release of a day collides with the tenth
    latest = session.scalar(select(func.max(cast(func.split_part(FeedRelease.version, ".", 4), Integer)))
                            .where(FeedRelease.version.like(f"{prefix}.%")))
    return f"{prefix}.{(latest or 0) + 1}"


def render(session: Session, taxonomy: Taxonomy, meta: Meta) -> list[FeedFile]:
    rows = published_rows(session)
    files = [FeedFile("all.csv", csv_all(meta, rows), len(rows)), FeedFile("all.json", json_all(meta, rows), len(rows))]
    members = category_members(session)
    for cid in taxonomy.assignable_ids("content"):
        domains = members.get(cid, [])
        title = taxonomy.get(cid).name
        files += [FeedFile(f"{cid}.txt", txt(meta, title, domains), len(domains)),
                  FeedFile(f"{cid}.hosts", hosts(meta, title, domains), len(domains)),
                  FeedFile(f"{cid}.rpz", rpz(meta, title, domains), len(domains))]
    threats = threat_members(session)
    every_threat = sorted({d for ds in threats.values() for d in ds})
    for cid in taxonomy.assignable_ids("risk"):
        domains = threats.get(cid, [])
        title = taxonomy.get(cid).name
        files += [FeedFile(f"{cid}.txt", txt(meta, title, domains), len(domains)),
                  FeedFile(f"{cid}.hosts", hosts(meta, title, domains), len(domains)),
                  FeedFile(f"{cid}.rpz", rpz(meta, title, domains), len(domains))]
    files.append(FeedFile("security.rpz", rpz(meta, "security threats", every_threat), len(every_threat)))
    return files


def _content_hash(body: bytes) -> str:
    """Hash without the metadata header, so an unchanged domain list does not produce a new release."""
    lines = [ln for ln in body.split(b"\n") if not ln.startswith((b"#", b";", b"@ SOA"))]
    if body.startswith(b"{"):
        doc = json.loads(body)
        doc.pop("meta", None)
        return hashlib.sha256(json.dumps(doc, sort_keys=True).encode()).hexdigest()
    return hashlib.sha256(b"\n".join(lines)).hexdigest()


def build(session: Session, store, taxonomy: Taxonomy, *, now: datetime | None = None) -> list[FeedRelease]:
    """Render all feeds and store the ones whose domain content changed. Returns the new releases."""
    now = now or datetime.now(UTC)
    meta = Meta(version=next_version(session, now), generated_at=now, taxonomy_version=taxonomy.version)
    latest = {r.feed: r for r in session.scalars(
        select(FeedRelease).where(FeedRelease.id.in_(select(func.max(FeedRelease.id)).group_by(FeedRelease.feed))))}
    previous_hashes: dict[str, str] = {}
    for name, release in latest.items():
        try:
            previous_hashes[name] = _content_hash(store.get(release.object_key))
        except Exception:  # noqa: BLE001 - missing object -> rebuild that feed
            log.warning("previous feed object unreadable", extra={"feed": name, "key": release.object_key})
    created = []
    for f in render(session, taxonomy, meta):
        if previous_hashes.get(f.name) == _content_hash(f.body):
            continue
        key = f"feeds/{meta.version}/{f.name}"
        store.put(key, f.body, MEDIA_TYPES[f.name.rsplit(".", 1)[-1]])
        release = FeedRelease(feed=f.name, version=meta.version, license=OWN_DATA_LICENSE,
                              taxonomy_version=taxonomy.version, record_count=f.records, sha256=f.sha256,
                              object_key=key, generated_at=now)
        session.add(release)
        created.append(release)
    session.flush()
    return created
