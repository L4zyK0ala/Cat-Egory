"""Cross-check with the national malicious-address list of T.C. Siber Güvenlik Başkanlığı (formerly USOM).

API: https://siberguvenlik.gov.tr/api/openapi.yaml (public, no key; published "so that firewalls, SIEM, URL
filtering and DNS systems can be integrated"). The old url-list.txt was retired on 2026-06-01.

**Internal use only.** The site's terms (siberguvenlik.gov.tr/yasal-uyarilar) forbid copying, republishing or
distributing its content without prior written permission and attribution. So results go to the
``external_listings`` table for reviewers and for one purpose above all: finding threats *we* confirmed that the
national list does not have yet, so they can be reported to USOM. Nothing from the list is shown on the public
site, in the public API or in feeds.

Politeness: one query per domain (``q`` is a case-insensitive substring search, exact matches are picked here),
at most ``sgb_check_batch`` domains per hourly run, ``sgb_check_pause_seconds`` between requests, and a domain is
asked again only after ``sgb_check_max_age_hours``.
"""
from __future__ import annotations

import logging
import time
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from urllib.parse import urlsplit

import httpx
from sqlalchemy import exists, or_, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from classification.engine import threat_candidate
from common.domains import DomainError, normalize_domain
from database.models import Domain, ExternalListing, ReviewStatus, ReviewTask, ThreatIntelligence

from .records import domain_scoped

log = logging.getLogger("category.threats.sgb")

SOURCE = "sgb"
API = "https://siberguvenlik.gov.tr/api/address/index"
DETAIL = "https://siberguvenlik.gov.tr/zararli-baglantilar/detay/{id}"
USER_AGENT = "Cat-Egory threat cross-check (internal; https://github.com/L4zyK0ala/Cat-Egory)"
# The list's own categories (GET /api/address-description/index, 2026-11-09).
CATEGORIES = {
    "PH": "Oltalama", "BP": "Bankacılık - Oltalama", "MD": "Zararlı yazılım yayan alan adı",
    "MU": "Zararlı yazılım yayan URL", "MI": "Zararlı yazılım yayan IP", "MC": "Zararlı yazılım komuta merkezi",
    "CA": "Siber saldırı",
}
MAX_PAGES = 5  # a substring query for a short brand name can match many entries

Fetch = Callable[[str], list[dict]]


def _host(value: str, kind: str) -> str | None:
    raw = value.strip()
    if kind == "url":
        raw = urlsplit(raw if "://" in raw else f"http://{raw}").hostname or ""
    elif kind != "domain":
        return None  # IP entries are not matched to domains
    try:
        return normalize_domain(raw).ascii
    except DomainError:
        return None


def matches_for(domain: str, models: list[dict]) -> list[dict]:
    """Entries about ``domain`` itself, one of its subdomains, or a URL on it (the search also returns unrelated
    names that merely contain the text)."""
    out = []
    for m in models:
        kind = str(m.get("type", ""))
        host = _host(str(m.get("url", "")), kind)
        if host and (host == domain or host.endswith("." + domain)):
            out.append({"id": m.get("id"), "value": str(m.get("url", ""))[:500], "type": kind,
                        "category": m.get("desc"), "criticality": m.get("criticality_level"),
                        "date": m.get("date"), "scope": "exact" if host == domain and kind == "domain" else
                        ("subdomain" if kind == "domain" else "url")})
    return out


def http_fetch(client: httpx.Client) -> Fetch:
    def fetch(domain: str) -> list[dict]:
        models: list[dict] = []
        for page in range(MAX_PAGES):
            r = client.get(API, params={"q": domain, "per-page": 200, "page": page})
            r.raise_for_status()
            data = r.json()
            models += data.get("models") or []
            if page + 1 >= int(data.get("pageCount") or 0):
                break
        return matches_for(domain, models)
    return fetch


def client() -> httpx.Client:
    return httpx.Client(timeout=30, headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
                        follow_redirects=False)


def due(session: Session, *, max_age_hours: int, limit: int) -> list[Domain]:
    """Domains worth asking about: an active domain-scoped threat record (any source) or an open
    threat-candidate review; not checked within ``max_age_hours``. Never checked first."""
    has_threat = exists(select(ThreatIntelligence.id).where(ThreatIntelligence.domain_id == Domain.id,
                                                           ThreatIntelligence.is_active, domain_scoped()))
    candidate = exists(select(ReviewTask.id).where(ReviewTask.domain_id == Domain.id,
                                                   ReviewTask.status == ReviewStatus.OPEN, threat_candidate()))
    stale = datetime.now(UTC) - timedelta(hours=max_age_hours)
    checked = (select(ExternalListing.domain_id, ExternalListing.checked_at)
               .where(ExternalListing.source == SOURCE).subquery())
    return list(session.scalars(
        select(Domain).outerjoin(checked, checked.c.domain_id == Domain.id)
        .where(Domain.is_active, or_(has_threat, candidate),
               or_(checked.c.checked_at.is_(None), checked.c.checked_at < stale))
        .order_by(checked.c.checked_at.asc().nulls_first(), Domain.id).limit(limit)))


def record(session: Session, domain: Domain, matches: list[dict]) -> None:
    stmt = insert(ExternalListing).values(domain_id=domain.id, source=SOURCE, listed=bool(matches), matches=matches,
                                          checked_at=datetime.now(UTC))
    session.execute(stmt.on_conflict_do_update(
        index_elements=["domain_id", "source"],
        set_={"listed": stmt.excluded.listed, "matches": stmt.excluded.matches, "checked_at": stmt.excluded.checked_at}))


def check(session: Session, fetch: Fetch, *, limit: int, max_age_hours: int = 24,
          pause: float = 1.0, sleep: Callable[[float], None] = time.sleep) -> dict:
    """Ask the list about due domains. Stops at the first network/API error (the rest waits for the next run)."""
    counts = {"checked": 0, "listed": 0, "errors": 0}
    for i, domain in enumerate(due(session, max_age_hours=max_age_hours, limit=limit)):
        if i:
            sleep(pause)
        try:
            matches = fetch(domain.normalized_domain)
        except (httpx.HTTPError, ValueError) as exc:
            log.warning("sgb lookup failed", extra={"domain": domain.normalized_domain, "error": type(exc).__name__})
            counts["errors"] += 1
            break
        record(session, domain, matches)
        counts["checked"] += 1
        counts["listed"] += bool(matches)
    return counts
