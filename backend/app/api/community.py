"""Community (Phase 6): reports, domain suggestions, votes and moderation.

Threat flags (phishing, malware, ...) can be reported the same way: ``false_positive`` with a risk category
disputes a flag on the domain (accepting it withdraws our own reviewer record; a third-party feed's record stays
and is counted in the result), ``false_negative`` with a risk category reports a threat (accepting it records
one). Threat reports never mark the content classification as ``disputed``.

Reputation: an account (not an anonymous visitor) whose moderated submissions were accepted often enough
(``community_trust_min_accepted``, ``community_trust_min_share``, never spam) is *trusted*: its category corrections
apply at once, recorded as accepted with no moderator. Never for threats (those always go to a person), new
domains, popular or official sites (threats/guard.py), or a domain a reviewer decided. Only moderator decisions
count towards trust, so automatic acceptances cannot build it up.

Abuse controls: anonymous writes share the strict ``write`` rate limit (per hashed IP); one pending report per
reporter and domain; a domain only shows as ``disputed`` once ``dispute_threshold`` distinct reporters have
open reports; nothing a contributor sends changes public data until a moderator accepts it. Free text is stored
and returned as plain JSON strings only (never rendered as HTML by the API), and never written into feeds.
"""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import String, cast, func, select
from sqlalchemy.orm import Session

from classification.engine import REVIEW_SOURCE, apply_categories, current_assignments, record_threats
from classification.taxonomy import default_taxonomy
from common.config import get_settings
from common.domains import DomainError, normalize_domain, normalize_url
from database.models import (
    AssignmentOrigin,
    ClassificationStatus,
    CommunitySubmission,
    CommunityVote,
    Domain,
    Evidence,
    EvidenceType,
    FalsePositiveReport,
    ModerationStatus,
    ScanReason,
    SubmissionType,
    ThreatIntelligence,
    ThreatType,
)
from database.publication import publishable
from database.session import get_session
from threats.guard import protected_owner
from threats.records import domain_scoped, withdraw

from .. import audit
from ..deps import Principal, get_principal, rate_limit, require
from ..errors import ApiError
from ..services.domains import add_domains, get_source
from .reviews import guard_protected

public = APIRouter(prefix="/api/v1/community", tags=["community"], dependencies=[Depends(rate_limit("write"))])
moderation = APIRouter(prefix="/api/v1/admin/submissions", tags=["moderation"],
                       dependencies=[Depends(rate_limit("write"))])

TAX = default_taxonomy()
RISK_IDS = frozenset(TAX.assignable_ids("risk"))


# ----------------------------------------------------------------------------- schemas


class ReportIn(BaseModel):
    domain: str
    type: Literal["false_positive", "false_negative", "category_correction", "verification"]
    category: str | None = Field(default=None, description="the category that is wrong (false_positive) or "
                                                           "missing (false_negative)")
    proposed_categories: list[str] = Field(default_factory=list, max_length=6,
                                           description="for category_correction: primary first")
    reason: str = Field(min_length=10, max_length=2000)
    evidence_urls: list[str] = Field(default_factory=list, max_length=5)


class SuggestIn(BaseModel):
    domain: str
    proposed_categories: list[str] = Field(default_factory=list, max_length=6)
    comment: str | None = Field(default=None, max_length=2000)


class SubmissionOut(BaseModel):
    id: int
    type: str
    domain: str
    proposed_categories: list[str]
    status: str
    score: int
    created_at: datetime


class SubmissionDetail(SubmissionOut):
    submitter: Reputation | None = None
    comment: str | None
    category: str | None
    evidence: list[str]
    reporters_for_domain: int
    moderation_note: str | None


class VoteIn(BaseModel):
    vote: Literal[1, -1]


class Reputation(BaseModel):
    account: bool = Field(description="false: an anonymous visitor (never trusted)")
    accepted: int = 0
    rejected: int = 0
    spam: int = 0
    trusted: bool = False


class ModerateIn(BaseModel):
    action: Literal["accept", "reject", "spam"]
    primary: str | None = Field(default=None, description="override / required when the primary category is removed")
    secondary: list[str] | None = Field(default=None, max_length=5)
    note: str | None = Field(default=None, max_length=2000)
    confirm_protected: bool = Field(default=False, description="flag a threat on a popular or official site "
                                                               "anyway (otherwise 409 protected_domain)")


# ----------------------------------------------------------------------------- helpers


def reputation(session: Session, user_id: int | None) -> Reputation:
    """Moderator decisions on this account's earlier submissions (automatic acceptances do not count)."""
    if user_id is None:
        return Reputation(account=False)
    counts = dict(session.execute(
        select(CommunitySubmission.status, func.count())
        .where(CommunitySubmission.submitter_id == user_id, CommunitySubmission.moderator_id.is_not(None))
        .group_by(CommunitySubmission.status)).all())
    accepted = counts.get(ModerationStatus.ACCEPTED, 0)
    rejected = counts.get(ModerationStatus.REJECTED, 0)
    spam = counts.get(ModerationStatus.SPAM, 0)
    settings = get_settings()
    decided = accepted + rejected + spam
    trusted = (spam == 0 and accepted >= settings.community_trust_min_accepted
               and accepted / decided >= settings.community_trust_min_share)
    return Reputation(account=True, accepted=accepted, rejected=rejected, spam=spam, trusted=trusted)


def _normalize(value: str) -> str:
    try:
        return normalize_domain(value).ascii
    except DomainError as exc:
        raise ApiError(422, "invalid_domain", str(exc), details={"code": exc.code}) from exc


def _check_categories(ids: list[str], *, threats: bool = False) -> None:
    """Content categories; with ``threats`` also the security-threat (risk) categories."""
    allowed = set(TAX.assignable_ids("content")) | (RISK_IDS if threats else set())
    bad = [c for c in ids if c not in allowed]
    if bad:
        raise ApiError(422, "invalid_category", f"unknown or non-assignable categories: {bad}")


def _reporter_filter(principal: Principal):
    if principal.user_id is not None:
        return CommunitySubmission.submitter_id == principal.user_id
    if principal.fingerprint:
        return CommunitySubmission.submitter_fingerprint == principal.fingerprint
    return None


def _score(session: Session, submission_id: int) -> int:
    return session.scalar(select(func.coalesce(func.sum(CommunityVote.vote), 0))
                          .where(CommunityVote.submission_id == submission_id)) or 0


def _reporters(session: Session, domain_id: int) -> int:
    return session.scalar(
        select(func.count(func.distinct(func.coalesce(cast(CommunitySubmission.submitter_id, String),
                                                      CommunitySubmission.submitter_fingerprint))))
        .where(CommunitySubmission.domain_id == domain_id, CommunitySubmission.status == ModerationStatus.PENDING,
               CommunitySubmission.type.in_([SubmissionType.FALSE_POSITIVE, SubmissionType.FALSE_NEGATIVE,
                                             SubmissionType.CATEGORY_CORRECTION]),
               ~CommunitySubmission.proposed_category_ids.overlap(sorted(RISK_IDS)))) or 0  # threat reports apart


def refresh_dispute(session: Session, domain: Domain) -> None:
    """Show ``disputed`` only while enough distinct reporters have open reports; otherwise restore."""
    threshold = get_settings().dispute_threshold
    if _reporters(session, domain.id) >= threshold and domain.primary_category_id is not None:
        domain.classification_status = ClassificationStatus.DISPUTED
    elif domain.classification_status == ClassificationStatus.DISPUTED:
        human = any(r.origin in (AssignmentOrigin.HUMAN, AssignmentOrigin.COMMUNITY)
                    for r in current_assignments(session, domain.id))
        domain.classification_status = ClassificationStatus.VERIFIED if human else ClassificationStatus.CLASSIFIED


def _out(session: Session, s: CommunitySubmission) -> SubmissionOut:
    return SubmissionOut(id=s.id, type=str(s.type), domain=s.submitted_domain, proposed_categories=s.proposed_category_ids,
                         status=str(s.status), score=_score(session, s.id), created_at=s.created_at)


# ----------------------------------------------------------------------------- public endpoints


@public.post("/report", response_model=SubmissionOut, status_code=202,
             summary="Report a wrong, missing or correct classification")
def report(body: ReportIn, request: Request, principal: Principal = Depends(get_principal),
           session: Session = Depends(get_session)) -> SubmissionOut:
    if principal.authenticated and "community:write" not in principal.scopes:
        raise ApiError(403, "insufficient_scope", "this API key lacks the 'community:write' scope")
    name = _normalize(body.domain)
    domain = session.scalar(select(Domain).where(Domain.normalized_domain == name, Domain.is_active, publishable()))
    if domain is None:
        raise ApiError(404, "not_found", "unknown domain; suggest it with POST /api/v1/community/submit")
    if body.type in ("false_positive", "false_negative"):
        if not body.category:
            raise ApiError(422, "validation_error", "category is required for this report type")
        _check_categories([body.category], threats=True)  # a content category or a threat flag
    if body.type == "category_correction":
        if not body.proposed_categories:
            raise ApiError(422, "validation_error", "proposed_categories is required for category_correction")
        _check_categories(body.proposed_categories)
    urls = []
    for raw in body.evidence_urls:
        try:
            urls.append(normalize_url(raw).url)
        except DomainError as exc:
            raise ApiError(422, "invalid_url", f"evidence URL rejected: {exc}") from exc
    who = _reporter_filter(principal)
    if who is not None and session.scalar(select(CommunitySubmission.id).where(
            who, CommunitySubmission.domain_id == domain.id, CommunitySubmission.status == ModerationStatus.PENDING)):
        raise ApiError(409, "duplicate_report", "you already have an open report for this domain")

    sub = CommunitySubmission(type=SubmissionType(body.type), domain_id=domain.id, submitted_domain=name,
                              proposed_category_ids=body.proposed_categories or ([body.category] if body.category else []),
                              comment=body.reason, submitter_id=principal.user_id,
                              submitter_fingerprint=principal.fingerprint)
    session.add(sub)
    session.flush()
    if body.type in ("false_positive", "false_negative"):
        session.add(FalsePositiveReport(domain_id=domain.id, category_id=body.category,
                                        is_false_negative=body.type == "false_negative", community_submission_id=sub.id,
                                        reporter_id=principal.user_id, reporter_fingerprint=principal.fingerprint,
                                        reason=body.reason))
    for url in urls:
        session.add(Evidence(community_submission_id=sub.id, type=EvidenceType.HUMAN, observation="evidence URL",
                             reference=url))
    applied = _auto_accept(session, sub, principal)
    if applied is None:
        refresh_dispute(session, domain)
    audit.record(session, action="community.auto_accept" if applied is not None else "community.report",
                 target_type="domain", target_id=name,
                 after={"submission_id": sub.id, "type": body.type, "category": body.category} | (applied or {}),
                 principal=principal, request_id=getattr(request.state, "request_id", None))
    session.commit()
    return _out(session, sub)


@public.post("/submit", response_model=SubmissionOut, status_code=202, summary="Suggest a new domain")
def suggest(body: SuggestIn, request: Request, principal: Principal = Depends(get_principal),
            session: Session = Depends(get_session)) -> SubmissionOut:
    if principal.authenticated and "community:write" not in principal.scopes:
        raise ApiError(403, "insufficient_scope", "this API key lacks the 'community:write' scope")
    name = _normalize(body.domain)
    _check_categories(body.proposed_categories)
    if session.scalar(select(Domain.id).where(Domain.normalized_domain == name, publishable())):
        raise ApiError(409, "already_known", "this domain is already in the database",
                       details={"url": f"/api/v1/domain/{name}"})
    if session.scalar(select(CommunitySubmission.id).where(
            CommunitySubmission.submitted_domain == name, CommunitySubmission.type == SubmissionType.NEW_DOMAIN,
            CommunitySubmission.status == ModerationStatus.PENDING)):
        raise ApiError(409, "already_suggested", "this domain is already waiting for moderation")
    sub = CommunitySubmission(type=SubmissionType.NEW_DOMAIN, submitted_domain=name,
                              proposed_category_ids=body.proposed_categories, comment=body.comment,
                              submitter_id=principal.user_id, submitter_fingerprint=principal.fingerprint)
    session.add(sub)
    session.flush()
    audit.record(session, action="community.submit", target_type="domain", target_id=name,
                 after={"submission_id": sub.id}, principal=principal, request_id=getattr(request.state, "request_id", None))
    session.commit()
    return _out(session, sub)


@public.get("/submissions", response_model=list[SubmissionOut], summary="Open community submissions")
def list_public(status: Literal["pending", "accepted", "rejected"] = "pending", limit: int = Query(50, ge=1, le=200),
                session: Session = Depends(get_session)) -> list[SubmissionOut]:
    rows = session.scalars(select(CommunitySubmission).where(CommunitySubmission.status == status)
                           .order_by(CommunitySubmission.id.desc()).limit(limit))
    return [_out(session, s) for s in rows]


@public.post("/submissions/{submission_id}/vote", response_model=SubmissionOut, summary="Up/down-vote a submission")
def vote(submission_id: int, body: VoteIn, principal: Principal = Depends(require("community:write")),
         session: Session = Depends(get_session)) -> SubmissionOut:
    sub = session.get(CommunitySubmission, submission_id)
    if sub is None or sub.status != ModerationStatus.PENDING:
        raise ApiError(404, "not_found", "no pending submission with this id")
    if sub.submitter_id == principal.user_id:
        raise ApiError(409, "own_submission", "you cannot vote on your own submission")
    existing = session.scalar(select(CommunityVote).where(CommunityVote.submission_id == sub.id,
                                                          CommunityVote.user_id == principal.user_id))
    if existing:
        existing.vote = body.vote
    else:
        session.add(CommunityVote(submission_id=sub.id, user_id=principal.user_id, vote=body.vote))
    session.commit()
    return _out(session, sub)


# ----------------------------------------------------------------------------- moderation


def _detail(session: Session, s: CommunitySubmission) -> SubmissionDetail:
    fp = session.scalar(select(FalsePositiveReport).where(FalsePositiveReport.community_submission_id == s.id))
    urls = session.scalars(select(Evidence.reference).where(Evidence.community_submission_id == s.id))
    return SubmissionDetail(**_out(session, s).model_dump(), comment=s.comment, category=fp.category_id if fp else None,
                            evidence=[u for u in urls if u], moderation_note=s.moderation_note,
                            reporters_for_domain=_reporters(session, s.domain_id) if s.domain_id else 0,
                            submitter=reputation(session, s.submitter_id))


@moderation.get("", response_model=list[SubmissionDetail], summary="Moderation queue (highest score first)")
def queue(limit: int = Query(50, ge=1, le=200), _: Principal = Depends(require("reviews:write")),
          session: Session = Depends(get_session)) -> list[SubmissionDetail]:
    score = (select(func.coalesce(func.sum(CommunityVote.vote), 0))
             .where(CommunityVote.submission_id == CommunitySubmission.id).scalar_subquery())
    rows = session.scalars(select(CommunitySubmission).where(CommunitySubmission.status == ModerationStatus.PENDING)
                           .order_by(score.desc(), CommunitySubmission.id).limit(limit))
    return [_detail(session, s) for s in rows]


@moderation.post("/{submission_id}/moderate", response_model=SubmissionDetail, summary="Accept, reject or mark spam")
def moderate(submission_id: int, body: ModerateIn, request: Request,
             principal: Principal = Depends(require("reviews:write")),
             session: Session = Depends(get_session)) -> SubmissionDetail:
    sub = session.get(CommunitySubmission, submission_id, with_for_update=True)
    if sub is None:
        raise ApiError(404, "not_found", "submission not found")
    if sub.status != ModerationStatus.PENDING:
        raise ApiError(409, "already_moderated", f"submission is {sub.status}")
    if body.primary:
        _check_categories([body.primary])
    if body.secondary:
        _check_categories(body.secondary)
    result: dict = {"action": body.action}
    if body.action == "accept":
        result |= _accept(session, sub, body, principal.user_id)
    _resolve(session, sub, {"accept": ModerationStatus.ACCEPTED, "reject": ModerationStatus.REJECTED,
                            "spam": ModerationStatus.SPAM}[body.action], principal.user_id, body.note)
    audit.record(session, action="community.moderate", target_type="community_submission", target_id=sub.id,
                 before={"status": "pending"}, after=result, principal=principal,
                 request_id=getattr(request.state, "request_id", None))
    session.commit()
    return _detail(session, sub)


def _accept(session: Session, sub: CommunitySubmission, body: ModerateIn, user_id: int | None) -> dict:
    """Apply an accepted submission. Raises ApiError when it cannot be applied as it stands."""
    result: dict = {}
    if sub.type == SubmissionType.NEW_DOMAIN:
        source = get_source(session, "community")
        added = add_domains(session, [(1, sub.submitted_domain)], source=source, scan="new",
                            reason=ScanReason.COMMUNITY, requested_by_id=user_id)
        sub.domain_id = session.scalar(select(Domain.id).where(Domain.normalized_domain == sub.submitted_domain))
        result |= {"created": added.created, "scans_queued": added.scans_queued}
    elif _threat_report(sub):
        domain = session.get(Domain, sub.domain_id, with_for_update=True)
        if sub.type == SubmissionType.FALSE_NEGATIVE and not body.confirm_protected:
            guard_protected(session, domain.normalized_domain)  # "this is phishing" about a popular site
        result |= _accept_threat_report(session, sub, domain)
    else:
        domain = session.get(Domain, sub.domain_id, with_for_update=True)
        primary, secondary = _accepted_categories(sub, domain, body)
        if primary is not None:
            apply_categories(session, domain, primary=primary, secondary=secondary, confidence=None,
                             origin=AssignmentOrigin.COMMUNITY, taxonomy=TAX,
                             status=ClassificationStatus.VERIFIED, user_id=user_id,
                             source_slug=REVIEW_SOURCE)
            result |= {"primary": primary, "secondary": secondary}
    return result


def _resolve(session: Session, sub: CommunitySubmission, status: ModerationStatus, moderator_id: int | None,
             note: str | None) -> None:
    sub.status, sub.moderator_id, sub.moderation_note, sub.resolved_at = status, moderator_id, note, datetime.now(UTC)
    session.execute(FalsePositiveReport.__table__.update()
                    .where(FalsePositiveReport.community_submission_id == sub.id)
                    .values(status=sub.status, resolution=note, resolved_at=sub.resolved_at))
    if sub.domain_id:
        session.flush()
        refresh_dispute(session, session.get(Domain, sub.domain_id))


AUTO_NOTE = "applied at once: trusted contributor"


def _auto_accept(session: Session, sub: CommunitySubmission, principal: Principal) -> dict | None:
    """A trusted account's category correction applies without waiting; anything else waits for a moderator."""
    if sub.type == SubmissionType.NEW_DOMAIN or _threat_report(sub) or not reputation(session, principal.user_id).trusted:
        return None
    domain = session.get(Domain, sub.domain_id, with_for_update=True)
    if protected_owner(session, domain.normalized_domain) is not None:
        return None
    if any(r.origin == AssignmentOrigin.HUMAN for r in current_assignments(session, domain.id)):
        return None  # a reviewer decided this domain: a person looks at the change
    try:
        result = _accept(session, sub, ModerateIn(action="accept", note=AUTO_NOTE), principal.user_id)
    except ApiError:
        return None  # e.g. the main category would go and no new one was given
    _resolve(session, sub, ModerationStatus.ACCEPTED, None, AUTO_NOTE)
    return result


def _threat_report(sub: CommunitySubmission) -> bool:
    return (sub.type in (SubmissionType.FALSE_POSITIVE, SubmissionType.FALSE_NEGATIVE)
            and bool(sub.proposed_category_ids) and sub.proposed_category_ids[0] in RISK_IDS)


def _accept_threat_report(session: Session, sub: CommunitySubmission, domain: Domain) -> dict:
    threat = ThreatType(sub.proposed_category_ids[0])
    if sub.type == SubmissionType.FALSE_NEGATIVE:
        record_threats(session, domain, [threat.value], reference=f"community:{sub.id}")
        return {"threat_recorded": threat.value}
    withdrawn = withdraw(session, domain=domain, threat=threat)
    remaining = session.scalar(select(func.count()).select_from(ThreatIntelligence).where(
        ThreatIntelligence.domain_id == domain.id, ThreatIntelligence.threat_type == threat,
        ThreatIntelligence.is_active, domain_scoped())) or 0
    # remaining > 0: a third-party feed still lists it; the moderator should report the mistake to that feed.
    return {"threat_withdrawn": threat.value, "withdrawn": withdrawn, "third_party_remaining": remaining}


def _accepted_categories(sub: CommunitySubmission, domain: Domain, body: ModerateIn) -> tuple[str | None, list[str]]:
    current_primary = domain.primary_category_id
    current_secondary = list(domain.secondary_categories or [])
    if body.primary:
        return body.primary, body.secondary if body.secondary is not None else \
            [c for c in current_secondary if c != body.primary]
    proposed = sub.proposed_category_ids
    if sub.type == SubmissionType.CATEGORY_CORRECTION:
        return proposed[0], body.secondary if body.secondary is not None else proposed[1:]
    if sub.type == SubmissionType.FALSE_POSITIVE:
        wrong = proposed[0] if proposed else None
        if wrong == current_primary:
            raise ApiError(422, "primary_required",
                           "the reported category is the primary one; give the new primary category")
        return current_primary, [c for c in current_secondary if c != wrong]
    if sub.type == SubmissionType.FALSE_NEGATIVE:
        missing = proposed[0] if proposed else None
        if current_primary is None:
            return missing, []
        return current_primary, list(dict.fromkeys([*current_secondary, missing]))[:5]
    if sub.type == SubmissionType.VERIFICATION and current_primary is not None:
        return current_primary, current_secondary
    return None, []

