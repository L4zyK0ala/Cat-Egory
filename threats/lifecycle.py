"""Keeping confirmed threats honest over time, using the scans that already run for them.

Applies to our own domain-scoped records (a reviewer's or Octryx's). A third-party feed's records are the feed's business, and one
page on a live site cannot be judged from a scan of its home page.

* The site is up: ``last_seen`` moves on.
* The site is gone (no DNS, no connection, 404/410), or taken down: the host's "account suspended" page, an expired or
  for-sale domain, a parking page, a bare web-server default page. After ``threat_expire_days`` without being seen
  the record is deactivated, so it leaves the public pages and the block lists. It is kept, marked ``raw.expired_at``
  and ``raw.expired_reason`` (``unreachable`` or ``taken_down``).
* The site is up but its page changed clearly since the previous scan (other text, or it now lands on another
  host): it may have been cleaned. A person decides (review reason ``threat_page_changed``), at most once per
  ``threat_change_cooldown_days``, because phishing kits also show harmless pages to some visitors.
* An expired site comes back: a person decides. Nothing is flagged again automatically, because the name may have a
  new, innocent owner.

Anything else says nothing about the site (timeouts, robots.txt, our own failures, 403 and 5xx pages without a
takedown notice) and changes nothing.

Takedown notices are matched on the hosting companies' own wording, on short pages only. A phishing page's own
"your account has been suspended" lure must never count, so generic phrases are deliberately not in the list.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from datetime import datetime, timedelta
from urllib.parse import urlsplit

from sqlalchemy import select
from sqlalchemy.orm import Session

from classification.engine import OCTRYX_SOURCE, OWN_THREAT_SOURCES, THREAT_PAGE_CHANGED, THREAT_RETURNED, open_review
from crawler.result import CrawlResult
from database.models import AIPrediction, DataSource, Domain, ExtractedContent, ScanResult, ThreatIntelligence

from .records import domain_scoped

DEAD_ERRORS = frozenset({"dns_nxdomain", "dns_no_address", "connection_failed"})
GONE_STATUSES = frozenset({404, 410})

# Lower case, accents folded, "ı" -> "i" (see _fold). Each is a host's, registrar's or parking service's own wording.
TAKEDOWN_PHRASES = (
    # hosting suspensions (cPanel, Plesk, DirectAdmin and Turkish hosts)
    "this account has been suspended", "contact your hosting provider", "hosting account has been suspended",
    "this website has been suspended", "this site has been suspended", "this domain has been suspended",
    "this website is currently unavailable due to", "site has been disabled by", "hosting saglayiciniz",
    "bu hesap askiya alin", "bu site askiya alin", "bu web sitesi askiya alin", "barindirma hesabi askiya",
    # registrations that ran out
    "this domain has expired", "domain has expired and is", "this domain name expired", "alan adinin suresi dol",
    # for sale / parked
    "this domain is for sale", "this domain may be for sale", "buy this domain", "this domain is parked",
    "parked free, courtesy of", "bu alan adi satilik",
    # a provider's own warning in front of the site
    "suspected phishing site", "this site has been reported as unsafe",
)
# Exact titles of web servers' and panels' placeholder pages. Not "Account Suspended": that is also a lure's title.
DEFAULT_PAGE_TITLES = frozenset({
    "welcome to nginx!", "it works!", "apache2 ubuntu default page: it works", "apache2 debian default page: it works",
    "test page for the apache http server", "iis windows server", "default web site page", "domain default page",
    "plesk default page"})
TAKEDOWN_URL_PARTS = ("suspendedpage", "/suspended.page", "account-suspended")
PARKING_HOSTS = ("sedo.com", "dan.com", "afternic.com", "hugedomains.com", "bodis.com", "parkingcrew.net",
                 "above.com", "sedoparking.com", "godaddy.com/domainsearch")
TAKEDOWN_MAX_WORDS = 400  # a takedown notice is short; a full phishing page is not
SIMILAR = 0.4             # word-set overlap (Jaccard) below this = a different page
MIN_WORDS = 15            # fewer words than this on either side: compare title and size instead


def _fold(text: str | None) -> str:
    text = (text or "").replace("İ", "i").replace("ı", "i").lower()
    return "".join(c for c in unicodedata.normalize("NFKD", text) if not unicodedata.combining(c))


def _host(url: str | None) -> str:
    return (urlsplit(url).hostname or "") if url else ""


def taken_down(result: CrawlResult) -> bool:
    """Does this page say the site was suspended, expired, parked or never set up?"""
    if result.error_code or result.http_status is None:
        return False
    url = _fold(result.final_url)
    host = _host(result.final_url)
    if any(part in url for part in TAKEDOWN_URL_PARTS) or any(host == h or host.endswith("." + h) for h in PARKING_HOSTS):
        return True
    if (result.word_count or 0) > TAKEDOWN_MAX_WORDS:
        return False
    title = _fold(result.page_title).strip()
    if title in DEFAULT_PAGE_TITLES:
        return True
    head = f"{title} {_fold(result.text_excerpt)[:2000]}"
    return any(phrase in head for phrase in TAKEDOWN_PHRASES)


def _words(text: str | None) -> set[str]:
    return set(re.findall(r"[a-z0-9]{3,}", _fold(text)[:4000]))


def page_changed(before_title: str | None, before_text: str | None, before_url: str | None,
                 result: CrawlResult) -> bool:
    """Is this clearly another page than last time? Lands on another host, or shares little text."""
    if _host(before_url) and _host(result.final_url) and _host(before_url) != _host(result.final_url):
        return True
    old, new = _words(before_text), _words(result.text_excerpt)
    if min(len(old), len(new)) >= MIN_WORDS:
        return len(old & new) / len(old | new) < SIMILAR
    return _fold(before_title).strip() != _fold(result.page_title).strip() and abs(len(old) - len(new)) >= MIN_WORDS


def ai_answer_needed(session: Session, domain_id: int, *, every_days: int, now: datetime) -> bool:
    """A domain with a confirmed threat is scanned daily to see whether it is still there. That does not need a
    model each time: with an active domain-scoped threat and a valid answer within ``every_days``, skip the call.
    A page that just changed is the exception: the person deciding about it sees what the model makes of it."""
    flagged = list(session.scalars(select(ThreatIntelligence.raw).where(
        ThreatIntelligence.domain_id == domain_id, ThreatIntelligence.is_active, domain_scoped())))
    if not flagged:
        return True
    if any((raw or {}).get("page_changed_at", "") >= (now - timedelta(days=1)).isoformat() for raw in flagged):
        return True
    automatic = session.scalar(
        select(ThreatIntelligence.id).join(DataSource, DataSource.id == ThreatIntelligence.source_id)
        .where(ThreatIntelligence.domain_id == domain_id, ThreatIntelligence.is_active, domain_scoped(),
               DataSource.slug == OCTRYX_SOURCE).limit(1))
    if automatic is not None:
        return False  # a copy of a bank's login page would come back as "banking"; the flag says what it is
    recent = session.scalar(select(AIPrediction.id).where(
        AIPrediction.domain_id == domain_id, AIPrediction.primary_category_id.is_not(None),
        AIPrediction.created_at >= now - timedelta(days=every_days)).limit(1))
    return recent is None


@dataclass(frozen=True)
class Change:
    action: str  # "expire", "return" or "changed"
    types: list[str]
    last_seen: str | None = None
    reason: str | None = None  # expire: "unreachable" or "taken_down"


def observe(result: CrawlResult) -> str | None:
    """"alive", "dead", "taken_down", or None when this scan says nothing about the site."""
    if result.error_code in DEAD_ERRORS:
        return "dead"
    if not result.ok or result.http_status is None:
        return None
    if taken_down(result):
        return "taken_down"
    if result.http_status in GONE_STATUSES:
        return "dead"
    return "alive" if result.http_status < 400 else None


def _previous_page(session: Session, domain: Domain, scan_id: int | None) -> tuple | None:
    stmt = (select(ScanResult.page_title, ExtractedContent.text_excerpt, ScanResult.final_url)
            .outerjoin(ExtractedContent, ExtractedContent.scan_result_id == ScanResult.id)
            .where(ScanResult.domain_id == domain.id, ScanResult.http_status < 400, ScanResult.blocked_reason.is_(None))
            .order_by(ScanResult.id.desc()).limit(1))
    if scan_id is not None:
        stmt = stmt.where(ScanResult.id < scan_id)
    return session.execute(stmt).first()


def after_scan(session: Session, domain: Domain, result: CrawlResult, *, expire_days: int, now: datetime,
               scan_id: int | None = None, change_cooldown_days: int = 7) -> Change | None:
    """``scan_id``: this scan's row, already stored; the page is compared with the scan before it."""
    state = observe(result)
    if state is None:
        return None
    rows = list(session.scalars(
        select(ThreatIntelligence).join(DataSource, DataSource.id == ThreatIntelligence.source_id)
        .where(ThreatIntelligence.domain_id == domain.id, DataSource.slug.in_(OWN_THREAT_SOURCES),
               domain_scoped())))
    active = [r for r in rows if r.is_active]
    if state == "alive":
        for row in active:
            row.last_seen = now
        back = [r for r in rows if not r.is_active and (r.raw or {}).get("expired_at")]
        if back:
            for row in back:
                row.raw = {k: v for k, v in row.raw.items() if k != "expired_at"} | {"returned_at": now.isoformat()}
            open_review(session, domain, None, THREAT_RETURNED)
            return Change("return", sorted(str(r.threat_type) for r in back))
        return _check_changed(session, domain, result, active, now, scan_id, change_cooldown_days)
    limit = now - timedelta(days=expire_days)
    stale = [r for r in active if r.last_seen <= limit]
    if not stale:
        return None
    reason = "taken_down" if state == "taken_down" else "unreachable"
    for row in stale:
        row.is_active, row.deactivated_at = False, now
        row.raw = (row.raw or {}) | {"expired_at": now.isoformat(), "expired_reason": reason}
    return Change("expire", sorted(str(r.threat_type) for r in stale), min(r.last_seen for r in stale).isoformat(),
                  reason)


def _check_changed(session: Session, domain: Domain, result: CrawlResult, active: list[ThreatIntelligence],
                   now: datetime, scan_id: int | None, cooldown_days: int) -> Change | None:
    if not active:
        return None
    since = (now - timedelta(days=cooldown_days)).isoformat()
    if any((r.raw or {}).get("page_changed_at", "") >= since for r in active):
        return None  # asked recently; a cloaking kit would otherwise flip a review open every day
    before = _previous_page(session, domain, scan_id)
    if before is None or not page_changed(*before, result):
        return None
    for row in active:
        row.raw = (row.raw or {}) | {"page_changed_at": now.isoformat()}
    open_review(session, domain, None, THREAT_PAGE_CHANGED)
    return Change("changed", sorted(str(r.threat_type) for r in active))
