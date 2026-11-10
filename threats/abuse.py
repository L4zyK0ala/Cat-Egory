"""Abuse-report drafts for our confirmed threats. **Nothing is sent from here.**

For each domain with an active threat of our own (a reviewer's, or one Octryx proved on the page), this finds
who can take it down and writes them a short report:

* the **registrar**: its abuse address from the domain's RDAP record (the registrar entity's ``abuse`` contact);
* the **hosting network**: the abuse address from the RDAP record of the IP address the site was served from.

RDAP servers come from IANA's bootstrap files (data.iana.org/rdap), so only registry and RIR servers are asked,
over HTTPS. Some TLDs (``.tr`` among them) publish no RDAP: their reports have no registrar contact.

A person reads each draft in the admin panel, sends it from their own mail program and marks it sent. Third-party
list hits (USOM, VirusTotal, feeds) are never the basis of a report, and nothing internal goes into one: the
report says only what we saw ourselves and links the public domain page.
"""
from __future__ import annotations

import ipaddress
import logging
import re
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from urllib.parse import quote, urlsplit

import httpx
from sqlalchemy import exists, func, select
from sqlalchemy.orm import Session

from classification.engine import OWN_THREAT_SOURCES, REVIEW_SOURCE
from database.models import AbuseReport, DataSource, Domain, ScanResult, ThreatIntelligence

from .records import domain_scoped

log = logging.getLogger("category.threats.abuse")

BOOTSTRAP = {"dns": "https://data.iana.org/rdap/dns.json", "ipv4": "https://data.iana.org/rdap/ipv4.json",
             "ipv6": "https://data.iana.org/rdap/ipv6.json"}
USER_AGENT = "Cat-Egory abuse contact lookup (https://cat-egory.org)"
# Worth a takedown request. A look-alike name alone (often parked) or "suspicious" is not.
REPORTABLE = {"phishing": "a phishing website", "scam": "a scam website", "fraud": "a fraudulent website",
              "malware": "malware distribution", "malicious-downloads": "malicious downloads",
              "command-and-control": "a malware command-and-control server"}
# Providers that answer abuse mail with "use our form": the panel links the form next to the draft.
WEB_FORMS = {"cloudflare.com": "https://abuse.cloudflare.com/", "google.com": "https://support.google.com/legal/troubleshooter/1114905",
             "amazonaws.com": "https://support.aws.amazon.com/#/contacts/report-abuse",
             "amazon.com": "https://support.aws.amazon.com/#/contacts/report-abuse"}
EMAIL = re.compile(r"^[A-Za-z0-9._%+-]{1,64}@[A-Za-z0-9.-]{1,190}\.[A-Za-z]{2,24}$")

Fetch = Callable[[str], dict | None]  # GET a JSON document; None when the server says 404


@dataclass(frozen=True)
class Contact:
    kind: str  # "registrar" or "hosting"
    email: str
    organisation: str | None
    server: str


def http_fetch(client: httpx.Client) -> Fetch:
    def fetch(url: str) -> dict | None:
        if urlsplit(url).scheme != "https":
            raise ValueError("RDAP over HTTPS only")
        r = client.get(url)
        if r.status_code == 404:
            return None
        r.raise_for_status()
        return r.json()
    return fetch


def client() -> httpx.Client:
    # Registry servers redirect between their own hosts; the scheme is checked again in http_fetch.
    return httpx.Client(timeout=20, follow_redirects=True, max_redirects=3,
                        headers={"User-Agent": USER_AGENT, "Accept": "application/rdap+json, application/json"})


class Rdap:
    def __init__(self, fetch: Fetch) -> None:
        self.fetch = fetch
        self._services: dict[str, list] = {}

    def _bootstrap(self, kind: str) -> list:
        if kind not in self._services:
            self._services[kind] = (self.fetch(BOOTSTRAP[kind]) or {}).get("services") or []
        return self._services[kind]

    @staticmethod
    def _base(urls: list[str]) -> str | None:
        https = [u for u in urls if u.startswith("https://")]
        return (https[0].rstrip("/") + "/") if https else None

    def domain(self, name: str) -> tuple[dict | None, str | None]:
        tld = name.rsplit(".", 1)[-1]
        for keys, urls in self._bootstrap("dns"):
            if tld in keys and (base := self._base(urls)):
                return self.fetch(f"{base}domain/{quote(name)}"), base
        return None, None

    def ip(self, address: str) -> tuple[dict | None, str | None]:
        ip = ipaddress.ip_address(address)
        best, best_len = None, -1
        for prefixes, urls in self._bootstrap("ipv4" if ip.version == 4 else "ipv6"):
            for prefix in prefixes:
                net = ipaddress.ip_network(prefix, strict=False)
                if ip in net and net.prefixlen > best_len and (base := self._base(urls)):
                    best, best_len = base, net.prefixlen
        return (self.fetch(f"{best}ip/{address}"), best) if best else (None, None)


def _vcard(entity: dict) -> tuple[str | None, str | None]:
    name = email = None
    for item in (entity.get("vcardArray") or [None, []])[1] or []:
        if not isinstance(item, list) or len(item) < 4:
            continue
        if item[0] == "fn" and isinstance(item[3], str):
            name = item[3].strip()[:200] or None
        elif item[0] == "email" and isinstance(item[3], str) and EMAIL.match(item[3].strip()):
            email = email or item[3].strip().lower()
    return name, email


def _entities(obj: dict, role: str) -> list[dict]:
    """Entities with this role, at any depth (RIRs nest the abuse contact inside the organisation)."""
    found = []
    for entity in obj.get("entities") or []:
        if role in (entity.get("roles") or []):
            found.append(entity)
        found.extend(_entities(entity, role))
    return found


def registrar_contact(doc: dict, server: str) -> Contact | None:
    for registrar in _entities(doc, "registrar"):
        name = _vcard(registrar)[0]
        for abuse in _entities(registrar, "abuse"):
            if email := _vcard(abuse)[1]:
                return Contact("registrar", email, name, server)
    return None


def hosting_contact(doc: dict, server: str) -> Contact | None:
    org = None
    for role in ("registrant", "administrative"):
        for entity in _entities(doc, role):
            org = org or _vcard(entity)[0]
    org = org or (str(doc["name"])[:200] if doc.get("name") else None)
    for abuse in _entities(doc, "abuse"):
        if email := _vcard(abuse)[1]:
            return Contact("hosting", email, org, server)
    return None


def defang(text: str) -> str:
    return text.replace("https://", "hxxps://").replace("http://", "hxxp://").replace(".", "[.]")


def compose(*, domain: str, types: list[str], first_seen: datetime, url: str | None, ip: str | None,
            reviewed: bool, evidence: str | None, contact: Contact, site_url: str) -> tuple[str, str]:
    what = [REPORTABLE[t] for t in types if t in REPORTABLE]
    headline = what[0] if what else "a malicious website"
    subject = f"Abuse report: {headline} at {domain}"
    how = ("checked and confirmed by a person on our review team" if reviewed
           else "found by automated analysis of the page" + (f" ({evidence[:200]})" if evidence else ""))
    ask = ("Please suspend the domain name if it breaks your terms of service."
           if contact.kind == "registrar" else
           "Please review the content hosted at this address and take it down if it breaks your terms of service.")
    where = "a domain registered with you" if contact.kind == "registrar" else "your network"
    lines = [
        "Hello,",
        "",
        f"We would like to report {headline} on {where}.",
        "",
        f"  Domain:          {defang(domain)}",
        *([f"  Address:         {defang(url)}"] if url else []),
        *([f"  IP address:      {ip}"] if ip and contact.kind == "hosting" else []),
        f"  Type:            {', '.join(types)}",
        f"  First confirmed: {first_seen.astimezone(UTC):%Y-%m-%d %H:%M} UTC",
        f"  How:             {how}",
        f"  Public record:   {site_url.rstrip('/')}/domain/{domain}",
        "",
        "(Addresses are defanged: hxxps = https, [.] = .)",
        "",
        ask,
        "",
        "This report comes from Cat-Egory, an open website-categorisation project that publishes block lists of "
        "phishing and malware sites. Reply to this message if you need a screenshot or more detail.",
        "",
        "Kind regards,",
        "Cat-Egory",
    ]
    return subject, "\n".join(lines)


# ----------------------------------------------------------------------------- drafting


def due_domains(session: Session, *, limit: int, repeat_days: int, now: datetime) -> list[int]:
    """Domains with an active reportable threat of ours and no report in the last ``repeat_days``."""
    recent = exists(select(AbuseReport.id).where(AbuseReport.domain_id == Domain.id,
                                                 AbuseReport.created_at >= now - timedelta(days=repeat_days)))
    return list(session.scalars(
        select(Domain.id).join(ThreatIntelligence, ThreatIntelligence.domain_id == Domain.id)
        .join(DataSource, DataSource.id == ThreatIntelligence.source_id)
        .where(ThreatIntelligence.is_active, domain_scoped(), DataSource.slug.in_(OWN_THREAT_SOURCES),
               ThreatIntelligence.threat_type.in_(list(REPORTABLE)), Domain.is_active, ~recent)
        .group_by(Domain.id).order_by(func.max(ThreatIntelligence.first_seen).desc())  # newest first
        .limit(limit)))


def draft_domain(session: Session, domain_id: int, rdap: Rdap, *, site_url: str) -> list[AbuseReport]:
    """Look up the contacts and add the drafts (or one ``no_contact`` row). Raises on a network failure, so the
    domain is tried again next run instead of being marked as having no contact."""
    domain = session.get(Domain, domain_id)
    rows = session.execute(
        select(ThreatIntelligence.threat_type, ThreatIntelligence.first_seen, ThreatIntelligence.raw, DataSource.slug)
        .join(DataSource, DataSource.id == ThreatIntelligence.source_id)
        .where(ThreatIntelligence.domain_id == domain_id, ThreatIntelligence.is_active, domain_scoped(),
               DataSource.slug.in_(OWN_THREAT_SOURCES))).all()
    types = sorted({str(r.threat_type) for r in rows})
    first_seen = min(r.first_seen for r in rows)
    reviewed = any(r.slug == REVIEW_SOURCE for r in rows)
    evidence = next(((r.raw or {}).get("evidence") for r in rows if (r.raw or {}).get("evidence")), None)
    scan = session.execute(
        select(ScanResult.final_url, ScanResult.resolved_ips).where(
            ScanResult.domain_id == domain_id, ScanResult.http_status.is_not(None))
        .order_by(ScanResult.id.desc()).limit(1)).first()
    url = scan.final_url if scan else None
    ip = next((str(a) for a in (scan.resolved_ips if scan else []) or []), None)

    contacts: list[Contact] = []
    doc, server = rdap.domain(domain.normalized_domain)
    if doc and (c := registrar_contact(doc, server)):
        contacts.append(c)
    if ip and not ipaddress.ip_address(ip).is_private:
        doc, server = rdap.ip(ip)
        if doc and (c := hosting_contact(doc, server)) and c.email not in {x.email for x in contacts}:
            contacts.append(c)

    created = []
    for contact in contacts or [None]:
        kind = contact.kind if contact else "none"
        subject, body = compose(domain=domain.normalized_domain, types=types, first_seen=first_seen, url=url, ip=ip,
                                reviewed=reviewed, evidence=evidence, site_url=site_url,
                                contact=contact or Contact("hosting", "", None, ""))
        report = AbuseReport(domain_id=domain_id, status="draft" if contact else "no_contact", kind=kind,
                             recipient=contact.email if contact else None,
                             organisation=contact.organisation if contact else None, threat_types=types,
                             subject=subject, body=body,
                             lookup={"server": contact.server if contact else None, "ip": ip,
                                     "form": WEB_FORMS.get(contact.email.rsplit("@", 1)[-1]) if contact else None})
        session.add(report)
        created.append(report)
    session.flush()
    return created


def draft_due(session: Session, rdap: Rdap, *, limit: int, repeat_days: int, site_url: str,
              now: datetime | None = None, pause: float = 1.0) -> dict:
    """Draft reports for the domains that are due; each domain is committed on its own."""
    now = now or datetime.now(UTC)
    counts = {"domains": 0, "drafts": 0, "no_contact": 0, "errors": 0}
    for i, domain_id in enumerate(due_domains(session, limit=limit, repeat_days=repeat_days, now=now)):
        if i and pause:
            time.sleep(pause)
        try:
            created = draft_domain(session, domain_id, rdap, site_url=site_url)
            session.commit()
        except (httpx.HTTPError, ValueError, KeyError, TypeError) as exc:
            session.rollback()
            counts["errors"] += 1
            log.warning("abuse contact lookup failed", extra={"domain_id": domain_id, "error": type(exc).__name__})
            continue
        counts["domains"] += 1
        counts["drafts"] += sum(r.status == "draft" for r in created)
        counts["no_contact"] += sum(r.status == "no_contact" for r in created)
    return counts
