"""Our own page-proof threat detector (THREATS.md, phases 2 and 3).

A deterministic rule, not a model's opinion. A scanned page is a phishing page when all of this holds:

* it asks for a secret: a password, a card number or CVV, an SMS / one-time code, or a Turkish ID number;
* it claims a watched Turkish brand (integrations/ct_watchlist.yaml) in its title, description or first lines;
* it is not that brand's official site, nor any popular, official or restricted-suffix host (threats/guard.py);
* and the host is suspicious by itself: its name imitates a brand or a top site, a threat list names it
  (threats/feeds.py), or its certificate is under 30 days old.

**Phase 2 (shadow):** every verdict on a threat candidate, and every positive one, is stored in
``detector_verdicts`` and later compared with what a person decided (``measure``). Nothing is flagged.

**Phase 3 (on its own):** with ``CATEGORY_THREAT_DETECTOR_AUTO=true`` a positive verdict flags the domain as
phishing (and look-alike when the name imitates a brand) from the source ``cat-egory-detector``, but only while the
rule has earned it: at least ``threat_detector_min_decisions`` people's decisions on its positive verdicts, at least
``threat_detector_min_precision`` of them right, at most ``threat_detector_max_per_day`` flags a day. Every
``threat_detector_sample_every``-th flag still goes to a person, so the measurement goes on; any flag can be
withdrawn like a reviewer's (it is one of our own sources).
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from common.config import Settings
from common.timeutil import local_midnight
from database.models import (
    DataSource,
    DetectorVerdict,
    Domain,
    ReviewStatus,
    ReviewTask,
    ScanResult,
    ThreatFeedEntry,
    ThreatIntelligence,
)

log = logging.getLogger("category.threats.detector")
RULE = "page_proven_v1"
SOURCE = "cat-egory-detector"  # = classification.engine.DETECTOR_SOURCE
FRESH_CERT_DAYS = 30

_ATTR = r"""\b(?:name|id|placeholder|aria-label)\s*=\s*['"]?[^'">]*"""
_SECRETS = {
    "password": re.compile(r"""<input\b[^>]*\btype\s*=\s*['"]?password""", re.I),
    "card": re.compile(r"""<input\b[^>]*(?:autocomplete\s*=\s*['"]?cc-(?:number|csc|exp)|"""
                       + _ATTR + r"""(?:card[-_ ]?(?:no|num|number)|kart[-_ ]?(?:no|num|numara)|cvv|cvc|ccnum|cardnumber))""",
                       re.I),
    "sms_code": re.compile(r"""<input\b[^>]*""" + _ATTR + r"""(?:\botp\b|sms[-_ ]?(?:kod|code)|onay[-_ ]?kod|"""
                           r"""dogrulama[-_ ]?kod|doğrulama[-_ ]?kod)""", re.I),
    "tr_id": re.compile(r"""<input\b[^>]*""" + _ATTR + r"""(?:tckn|tc[-_ ]?kimlik|kimlik[-_ ]?no)""", re.I),
}


@dataclass
class Page:
    host: str
    title: str | None
    description: str | None
    text: str | None
    html: str | None
    cert_valid_from: float | None  # seconds since the epoch
    scanned_at: datetime


def secrets(html: str | None) -> list[str]:
    return [name for name, pattern in _SECRETS.items() if html and pattern.search(html)]


def claimed_brand(page: Page) -> tuple[str, tuple[str, ...]] | None:
    """(brand name, its official domains) a page names in its title, description or first lines, or None."""
    from integrations.brand_watch import default_watchlist, skeleton

    head = " ".join(x for x in (page.title, page.description, (page.text or "")[:1500]) if x)
    if not head:
        return None
    words = " " + re.sub(r"[^a-z0-9]+", " ", skeleton(head)) + " "
    joined = words.replace(" ", "")
    for brand in default_watchlist().brands:
        name = re.sub(r"[^a-z0-9]+", " ", skeleton(brand.name)).strip()
        if (name and f" {name} " in words) or any(skeleton(s).replace("-", "") in joined for s in brand.substrings) \
                or any(f" {skeleton(t)} " in words for t in brand.tokens):
            return brand.name, brand.official
    return None


def fresh_cert(page: Page) -> bool:
    if not page.cert_valid_from:
        return False
    issued = datetime.fromtimestamp(page.cert_valid_from, UTC)
    return timedelta(0) <= page.scanned_at - issued <= timedelta(days=FRESH_CERT_DAYS)


def judge(page: Page, *, suspicious_name: str | None, protected: str | None) -> tuple[bool, dict]:
    """(positive, signals). ``suspicious_name``: why the host itself is suspect (a look-alike or list reason), if
    any. ``protected``: the popular / official / restricted owner the host belongs to, if any."""
    found = secrets(page.html)
    brand = claimed_brand(page)
    official = bool(brand) and any(page.host == o or page.host.endswith("." + o) for o in brand[1])
    fresh = fresh_cert(page)
    signals = {"secrets": found, "brand": brand[0] if brand else None, "official": official,
               "protected": protected, "suspicious_name": suspicious_name, "fresh_cert": fresh}
    positive = bool(found) and brand is not None and not official and protected is None \
        and (suspicious_name is not None or fresh)
    return positive, signals


def page_of(session: Session, domain: Domain, scan: ScanResult, data) -> Page:
    tls = scan.tls or {}
    valid_from = tls.get("valid_from")
    return Page(host=domain.normalized_domain, title=data.title, description=data.meta_description, text=data.text,
                html=data.html, cert_valid_from=float(valid_from) if isinstance(valid_from, int | float) else None,
                scanned_at=scan.created_at or datetime.now(UTC))


# ----------------------------------------------------------------------------- measurement


def measure(session: Session, rule: str = RULE) -> dict:
    """The rule against people's decisions, one verdict per domain (its newest). A person decided when they closed
    a threat-candidate review of the domain after the verdict, or flagged it (reviewer record) at any time. Our own
    detector flags a person withdrew count as wrong."""
    from classification.engine import REVIEW_SOURCE, threat_candidate

    newest = (select(func.max(DetectorVerdict.id)).where(DetectorVerdict.rule == rule)
              .group_by(DetectorVerdict.domain_id))
    verdicts = list(session.scalars(select(DetectorVerdict).where(DetectorVerdict.id.in_(newest))))
    ids = {v.domain_id for v in verdicts}
    if not ids:
        return {"rule": rule, "verdicts": 0, "positive": 0, "tp": 0, "fp": 0, "fn": 0, "tn": 0, "pending": 0,
                "precision": None, "recall": None, "listed": 0, "flagged": 0, "withdrawn": 0}
    slugs = dict(session.execute(select(DataSource.slug, DataSource.id)
                                 .where(DataSource.slug.in_([REVIEW_SOURCE, SOURCE]))).all())
    person = set(session.scalars(select(ThreatIntelligence.domain_id).where(
        ThreatIntelligence.domain_id.in_(ids), ThreatIntelligence.source_id == slugs.get(REVIEW_SOURCE),
        ThreatIntelligence.url_id.is_(None))))
    withdrawn = set(session.scalars(select(ThreatIntelligence.domain_id).where(
        ThreatIntelligence.domain_id.in_(ids), ThreatIntelligence.source_id == slugs.get(SOURCE),
        ~ThreatIntelligence.is_active, ThreatIntelligence.deactivated_at.is_not(None),
        or_(ThreatIntelligence.raw.is_(None), ~ThreatIntelligence.raw.has_key("expired_at")))))  # taken back by a
    # person, not expired with its dead site (threats/lifecycle.py)
    decided_at: dict[int, datetime] = dict(session.execute(
        select(ReviewTask.domain_id, func.max(ReviewTask.decided_at))
        .where(ReviewTask.domain_id.in_(ids), ReviewTask.status == ReviewStatus.DONE, threat_candidate())
        .group_by(ReviewTask.domain_id)).all())
    hosts = dict(session.execute(select(Domain.id, Domain.normalized_domain).where(Domain.id.in_(ids))).all())
    listed = set(session.scalars(select(ThreatFeedEntry.host).where(
        ThreatFeedEntry.host.in_(list(hosts.values())), ThreatFeedEntry.is_listed)))
    c = dict(tp=0, fp=0, fn=0, tn=0, pending=0, listed=0, flagged=0)
    for v in verdicts:
        good = v.domain_id in person
        bad = v.domain_id in withdrawn
        decided = good or bad or (decided_at.get(v.domain_id) is not None and decided_at[v.domain_id] >= v.created_at)
        c["flagged"] += v.action == "flagged"
        if v.positive:
            c["listed"] += hosts.get(v.domain_id) in listed
        if not decided:
            c["pending"] += 1
        elif v.positive:
            c["tp" if good and not bad else "fp"] += 1
        else:
            c["fn" if good else "tn"] += 1
    judged = c["tp"] + c["fp"]
    return {"rule": rule, "verdicts": len(verdicts), "positive": sum(v.positive for v in verdicts), **c,
            "precision": round(c["tp"] / judged, 4) if judged else None,
            "recall": round(c["tp"] / (c["tp"] + c["fn"]), 4) if c["tp"] + c["fn"] else None,
            "withdrawn": len(withdrawn)}


def qualified(stats: dict, settings: Settings) -> bool:
    judged = stats["tp"] + stats["fp"]
    return judged >= settings.threat_detector_min_decisions and stats["precision"] is not None \
        and stats["precision"] >= settings.threat_detector_min_precision


def flagged_today(session: Session, settings: Settings, now: datetime | None = None) -> int:
    start = local_midnight(now or datetime.now(UTC), settings.app_utc_offset_hours)
    return session.scalar(select(func.count()).select_from(DetectorVerdict).where(
        DetectorVerdict.action == "flagged", DetectorVerdict.created_at >= start)) or 0


# ----------------------------------------------------------------------------- the check after a scan


def check(session: Session, domain: Domain, scan: ScanResult, data, settings: Settings) -> DetectorVerdict | None:
    """Judge a classified page. Stores the verdict when it is positive or the domain is a threat candidate (the
    negatives there are what recall is measured on); flags only in phase 3, within its limits."""
    from classification.engine import lookalike_reason

    from .guard import protected_owner

    suspicious = lookalike_reason(session, domain)
    page = page_of(session, domain, scan, data)
    if suspicious is None and not secrets(page.html):
        return None  # the common case: nothing asked for, nothing suspect
    positive, signals = judge(page, suspicious_name=suspicious, protected=protected_owner(session, domain.normalized_domain))
    if not positive and suspicious is None:
        return None
    verdict = DetectorVerdict(domain_id=domain.id, scan_result_id=scan.id, rule=RULE, positive=positive, signals=signals)
    session.add(verdict)
    session.flush()
    if positive and settings.threat_detector_auto:
        _flag(session, domain, verdict, settings)
    return verdict


def _flag(session: Session, domain: Domain, verdict: DetectorVerdict, settings: Settings) -> None:
    from classification.engine import is_threat_candidate, record_threats
    from integrations.brand_watch import default_watchlist

    if flagged_today(session, settings) >= settings.threat_detector_max_per_day:
        return
    stats = measure(session)
    if not qualified(stats, settings):
        return
    threats = ["phishing"] + (["lookalike-domain"] if default_watchlist().match(domain.normalized_domain) else [])
    record_threats(session, domain, threats, source_slug=SOURCE, reference=f"detector:{RULE}")
    session.flush()
    verdict.action = "flagged"
    if (stats["flagged"] + 1) % settings.threat_detector_sample_every == 0:
        return  # this one stays open for a person: the measurement goes on
    for task in session.scalars(select(ReviewTask).where(ReviewTask.domain_id == domain.id,
                                                         ReviewTask.status == ReviewStatus.OPEN)):
        if is_threat_candidate(task.reason):
            task.status, task.decided_at = ReviewStatus.SKIPPED, datetime.now(UTC)
            task.decision_note = f"detector: flagged by {RULE} (precision {stats['precision']:.1%})"
    log.info("detector flagged a domain", extra={"domain": domain.normalized_domain, "threats": threats})
