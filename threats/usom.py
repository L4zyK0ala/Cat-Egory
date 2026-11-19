"""Reporting our confirmed threats to the national CERT (USOM, T.C. Siber Güvenlik Başkanlığı) by e-mail.

The daily cross-check (threats/sgb.py) tells which of our confirmed threats the national malicious-address list does
not have. Those wait here until a person picks some, reads the e-mail and presses send. The worker hands the
e-mail to the mail service (Resend); the API container has no internet and no mail key. After a report, the same
daily check notices when the list adds the domain, which gives the time from report to listing.

Only our own verdicts are reported (a reviewer, the page-proof detector, Octryx's page evidence), and only the
types USOM lists: phishing, scams, fraud, malware and its downloads, command-and-control. A look-alike name or a
"suspicious" flag alone is not reported, nor is anything that is only on someone else's list. Nothing from the
national list is published (its terms forbid it); this module only reads its yes/no answer.
"""
from __future__ import annotations

import logging
from datetime import UTC, datetime
from email.utils import parseaddr
from typing import Any

import httpx
from sqlalchemy import delete, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from classification.engine import OWN_THREAT_SOURCES, REVIEW_SOURCE
from common.config import Settings
from database.models import (
    DataSource,
    DetectorVerdict,
    Domain,
    ExternalListing,
    ThreatIntelligence,
    ThreatType,
    UsomMail,
    UsomReport,
)
from integrations.brand_watch import default_watchlist, global_watchlist

from . import listings, sgb
from .records import domain_scoped

log = logging.getLogger("category.threats.usom")

REPORTABLE = {ThreatType.PHISHING, ThreatType.SCAM, ThreatType.FRAUD, ThreatType.MALWARE,
              ThreatType.MALICIOUS_DOWNLOADS, ThreatType.COMMAND_AND_CONTROL}
REPORTED, DISMISSED = "reported", "dismissed"
VIEWS = ("pending", "reported", "listed", "dismissed")
RESEND_URL = "https://api.resend.com/emails"
MAX_DOMAINS_PER_MAIL = 100


class MailError(RuntimeError):
    """The e-mail could not be handed to the mail service. The message never contains the key."""


def defang(name: str) -> str:
    return name.replace(".", "[.]")


def brand_of(name: str, claimed: str | None = None) -> str | None:
    """The brand a name imitates (Turkish, then worldwide watchlist), else the one its page claimed."""
    match = default_watchlist().match(name) or global_watchlist().match(name)
    return match.brand.name if match else claimed


def mail_ready(settings: Settings) -> bool:
    """A sender is set. The key lives only in the worker; a missing key shows up as a failed send."""
    return bool(settings.usom_mail_from.strip())


def _confirmed(session: Session, domain_ids: set[int] | None = None) -> dict[int, dict[str, Any]]:
    """Active domain-scoped threats of our own, grouped by domain."""
    stmt = (select(Domain.id, Domain.normalized_domain, ThreatIntelligence.threat_type, ThreatIntelligence.first_seen,
                   DataSource.slug)
            .join(ThreatIntelligence, ThreatIntelligence.domain_id == Domain.id)
            .join(DataSource, DataSource.id == ThreatIntelligence.source_id)
            .where(ThreatIntelligence.is_active, domain_scoped(), DataSource.slug.in_(OWN_THREAT_SOURCES)))
    if domain_ids is not None:
        stmt = stmt.where(Domain.id.in_(domain_ids))
    grouped: dict[int, dict[str, Any]] = {}
    for did, name, threat, first, slug in session.execute(stmt).all():
        g = grouped.setdefault(did, {"domain": name, "types": set(), "first_seen": first, "by": set()})
        g["types"].add(ThreatType(threat))
        g["first_seen"] = min(g["first_seen"], first)
        g["by"].add("review" if slug == REVIEW_SOURCE else "detector" if "detector" in slug else "octryx")
    return grouped


def queue(session: Session, view: str, *, limit: int = 500) -> dict[str, Any]:
    """The domains in one view, newest first, and how many each view holds."""
    confirmed = _confirmed(session)
    reports = {r.domain_id: r for r in session.scalars(select(UsomReport))}
    names = dict(session.execute(select(Domain.id, Domain.normalized_domain)
                                 .where(Domain.id.in_(set(reports)))).all()) if reports else {}
    checked = listings.latest(session, sgb.SOURCE, set(confirmed) | set(reports))
    sending = {d for (ds,) in session.execute(select(UsomMail.domains).where(UsomMail.status == "queued")) for d in ds}

    def state(did: int) -> str | None:
        report = reports.get(did)
        if report is not None:
            return report.status if report.status == DISMISSED else ("listed" if report.listed_at else REPORTED)
        g = confirmed.get(did)
        if g is None or not g["types"] & REPORTABLE:
            return None
        row = checked.get(did)
        if row is None:
            return "unchecked"
        return None if row.listed else "pending"  # already on the list: nothing to report

    counts = {v: 0 for v in (*VIEWS, "unchecked")}
    chosen: list[int] = []
    for did in set(confirmed) | set(reports):
        s = state(did)
        if s:
            counts[s] += 1
            if s == view:
                chosen.append(did)
    claimed = dict(session.execute(
        select(DetectorVerdict.domain_id, DetectorVerdict.signals["brand"].astext)
        .where(DetectorVerdict.domain_id.in_(chosen), DetectorVerdict.positive)
        .order_by(DetectorVerdict.id)).all()) if chosen else {}
    items = []
    for did in chosen:
        g, report, row = confirmed.get(did), reports.get(did), checked.get(did)
        name = g["domain"] if g else names[did]
        days = (report.listed_at - report.status_at).total_seconds() / 86400 if report and report.listed_at else None
        items.append({
            "domain": name, "defanged": defang(name),
            "types": sorted(str(t) for t in g["types"]) if g else [],
            "brand": brand_of(name, claimed.get(did)),
            "confirmed_by": sorted(g["by"]) if g else [],
            "first_seen": g["first_seen"].isoformat() if g else None,
            "checked_at": row.checked_at.isoformat() if row else None,
            "status_at": report.status_at.isoformat() if report else None,
            "listed_at": report.listed_at.isoformat() if report and report.listed_at else None,
            "days_to_list": round(days, 1) if days is not None else None,
            "mail_id": report.mail_id if report else None,
            "sending": name in sending,
            "still_flagged": g is not None,
        })
    items.sort(key=lambda i: i["status_at"] or i["first_seen"] or "", reverse=True)
    listed_days = [(r.listed_at - r.status_at).total_seconds() / 86400 for r in reports.values()
                   if r.status == REPORTED and r.listed_at]
    return {"items": items[:limit], "counts": counts,
            "median_days_to_list": round(sorted(listed_days)[len(listed_days) // 2], 1) if listed_days else None}


def _ids(session: Session, names: list[str]) -> dict[str, int]:
    wanted = {n.strip().lower().rstrip(".") for n in names if n.strip()}
    return dict(session.execute(select(Domain.normalized_domain, Domain.id)
                                .where(Domain.normalized_domain.in_(wanted))).all())


def set_status(session: Session, names: list[str], action: str, user_id: int | None) -> list[str]:
    """``dismissed`` sets a domain aside; ``pending`` puts it back in the queue (also undoes a report)."""
    ids = _ids(session, names)
    if not ids:
        return []
    if action == "pending":
        session.execute(delete(UsomReport).where(UsomReport.domain_id.in_(ids.values())))
    else:
        stmt = insert(UsomReport).values([{"domain_id": i, "status": DISMISSED, "decided_by_id": user_id}
                                          for i in ids.values()])
        session.execute(stmt.on_conflict_do_update(index_elements=["domain_id"], set_={
            "status": DISMISSED, "status_at": datetime.now(UTC), "decided_by_id": user_id, "listed_at": None}))
    return sorted(ids)


def queue_mail(session: Session, names: list[str], subject: str, body: str, *, recipient: str,
               user_id: int | None) -> UsomMail:
    """A mail row for the worker. Only domains waiting (or already reported, to remind), none in another queued
    mail. Raises ValueError with a reason a person can act on."""
    if not names or len(names) > MAX_DOMAINS_PER_MAIL:
        raise ValueError(f"choose between 1 and {MAX_DOMAINS_PER_MAIL} domains")
    subject = " ".join(subject.split())
    if not subject or not body.strip():
        raise ValueError("the subject and the text must not be empty")
    if "@" not in parseaddr(recipient)[1]:
        raise ValueError("no valid recipient is set (CATEGORY_USOM_MAIL_TO)")
    ids = _ids(session, names)
    unknown = sorted({n.strip().lower().rstrip(".") for n in names} - set(ids))
    if unknown:
        raise ValueError(f"unknown domains: {', '.join(unknown[:5])}")
    confirmed = _confirmed(session, set(ids.values()))
    not_ours = sorted(n for n, i in ids.items() if i not in confirmed or not confirmed[i]["types"] & REPORTABLE)
    if not_ours:
        raise ValueError(f"no active confirmed threat to report on: {', '.join(not_ours[:5])}")
    dismissed = set(session.scalars(select(Domain.normalized_domain).join(UsomReport)
                                    .where(UsomReport.domain_id.in_(ids.values()), UsomReport.status == DISMISSED)))
    if dismissed:
        raise ValueError(f"set aside, put back first: {', '.join(sorted(dismissed)[:5])}")
    busy = {d for (ds,) in session.execute(select(UsomMail.domains).where(UsomMail.status == "queued")) for d in ds}
    if busy & set(ids):
        raise ValueError(f"already being sent: {', '.join(sorted(busy & set(ids))[:5])}")
    mail = UsomMail(status="queued", recipient=parseaddr(recipient)[1], subject=subject, body=body.strip() + "\n",
                    domains=sorted(ids), sent_by_id=user_id)
    session.add(mail)
    session.flush()
    return mail


def send_resend(settings: Settings, mail: UsomMail, http: httpx.Client) -> str | None:
    key = settings.resend_api_key.get_secret_value().strip() if settings.resend_api_key else ""
    sender = settings.usom_mail_from.strip()
    if not sender or not key:
        raise MailError("mail is not set up (CATEGORY_USOM_MAIL_FROM and CATEGORY_RESEND_API_KEY on the worker)")
    payload: dict[str, Any] = {"from": sender, "to": [mail.recipient], "subject": mail.subject, "text": mail.body}
    if "@" in parseaddr(settings.usom_mail_reply_to)[1]:
        payload["reply_to"] = parseaddr(settings.usom_mail_reply_to)[1]
    try:
        r = http.post(RESEND_URL, json=payload, headers={"Authorization": f"Bearer {key}"}, timeout=20)
    except httpx.HTTPError as exc:
        raise MailError(f"could not reach the mail service ({type(exc).__name__})") from None
    if r.status_code >= 400:
        try:
            detail = str(r.json().get("message") or "")[:200]
        except ValueError:
            detail = ""
        raise MailError(f"the mail service refused it (HTTP {r.status_code}{': ' + detail if detail else ''})")
    try:
        return str(r.json().get("id") or "")[:200] or None
    except ValueError:
        return None


def deliver(session: Session, mail_id: int, http: httpx.Client, settings: Settings) -> str:
    """Send one queued mail; on success its domains become ``reported``. Returns the mail's new status."""
    mail = session.get(UsomMail, mail_id, with_for_update=True)
    if mail is None or mail.status != "queued":
        return mail.status if mail else "missing"
    try:
        mail.provider_id = send_resend(settings, mail, http)
    except MailError as exc:
        mail.status, mail.error = "failed", str(exc)
        session.commit()
        log.warning("usom mail failed", extra={"mail_id": mail.id, "error": str(exc)})
        return mail.status
    now = datetime.now(UTC)
    mail.status, mail.provider, mail.sent_at, mail.error = "sent", "resend", now, None
    ids = _ids(session, mail.domains)
    if ids:
        stmt = insert(UsomReport).values([{"domain_id": i, "status": REPORTED, "status_at": now, "mail_id": mail.id,
                                           "decided_by_id": mail.sent_by_id} for i in ids.values()])
        session.execute(stmt.on_conflict_do_update(index_elements=["domain_id"], set_={
            "status": REPORTED, "status_at": now, "mail_id": mail.id, "decided_by_id": mail.sent_by_id,
            "listed_at": None}))
    session.commit()
    log.info("usom mail sent", extra={"mail_id": mail.id, "domains": len(mail.domains)})
    return mail.status


def mark_listed(session: Session) -> int:
    """Reported domains the national list has had since the report (from the daily cross-check)."""
    listed = (select(ExternalListing.checked_at)
              .where(ExternalListing.domain_id == UsomReport.domain_id, ExternalListing.source == sgb.SOURCE,
                     ExternalListing.listed, ExternalListing.checked_at >= UsomReport.status_at)
              .scalar_subquery())
    n = session.execute(update(UsomReport).where(UsomReport.status == REPORTED, UsomReport.listed_at.is_(None),
                                                 listed.is_not(None))
                        .values(listed_at=listed).execution_options(synchronize_session=False)).rowcount
    return n or 0
