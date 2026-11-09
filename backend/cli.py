"""Administrative command line.

    python -m backend.cli create-admin --email you@example.org --name "Your Name"
    python -m backend.cli create-user  --email mod@example.org --name Mod --role moderator [--password]
    python -m backend.cli create-key   --email mod@example.org --name laptop --scopes scans:read,scans:write
    python -m backend.cli list-keys    [--email mod@example.org]
    python -m backend.cli revoke-key   --prefix abcdefghij
    python -m backend.cli reclassify-provider --provider mock [--yes]
    python -m backend.cli apply-registry-rules [--yes]

In Docker: ``docker compose ... exec api python -m backend.cli ...`` (add ``-it`` for --password prompts).
API keys are printed exactly once and stored only as hashes.
"""
from __future__ import annotations

import argparse
import getpass
import sys
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app import audit
from backend.app.security import ROLE_SCOPES, generate_api_key, hash_password, validate_scopes
from common.config import get_settings
from database.models import ApiKey, Role, User
from database.session import session_factory


class CliError(Exception):
    pass


def _user(session: Session, email: str) -> User:
    user = session.scalar(select(User).where(User.email == email.strip().lower()))
    if user is None:
        raise CliError(f"no user with email {email}")
    return user


def create_user(session: Session, *, email: str, name: str, role: Role, password: str | None = None) -> User:
    email = email.strip().lower()
    if "@" not in email or len(email) > 320:
        raise CliError("invalid email address")
    if session.scalar(select(User).where(User.email == email)) is not None:
        raise CliError(f"user {email} already exists")
    user = User(email=email, display_name=name.strip()[:100] or email, role=role,
                password_hash=hash_password(password) if password else None)
    session.add(user)
    session.flush()
    audit.record(session, action="user.create", target_type="user", target_id=user.id,
                 after={"email": email, "role": role.value, "password": bool(password)})
    return user


def create_key(session: Session, user: User, *, name: str, scopes: list[str] | None = None,
               rate_limit: int | None = None, expires_days: int | None = None) -> tuple[ApiKey, str]:
    role = Role(user.role)
    scopes = validate_scopes(role, scopes if scopes is not None else sorted(ROLE_SCOPES[role]))
    new = generate_api_key()
    key = ApiKey(user_id=user.id, name=name[:100], prefix=new.prefix, key_hash=new.key_hash, scopes=scopes,
                 rate_limit_per_minute=rate_limit,
                 expires_at=datetime.now(UTC) + timedelta(days=expires_days) if expires_days else None)
    session.add(key)
    session.flush()
    audit.record(session, action="api_key.create", target_type="api_key", target_id=key.id,
                 after={"user_id": user.id, "prefix": new.prefix, "scopes": scopes, "expires_days": expires_days})
    return key, new.key


def revoke_key(session: Session, prefix: str) -> ApiKey:
    key = session.scalar(select(ApiKey).where(ApiKey.prefix == prefix))
    if key is None:
        raise CliError(f"no key with prefix {prefix}")
    if key.revoked_at is None:
        key.revoked_at = datetime.now(UTC)
        audit.record(session, action="api_key.revoke", target_type="api_key", target_id=key.id,
                     after={"prefix": prefix})
    return key


def deactivate_source_domains(session: Session, slug: str, *, dry_run: bool = False) -> tuple[int, int]:
    """Hide domains whose ONLY source is ``slug`` (e.g. a test import) and cancel their queued scans.
    Domains also reported by any other source are left alone. Returns (domains, cancelled_scans)."""
    from sqlalchemy import exists, update

    from database.models import DataSource, Domain, DomainSourceRecord, JobStatus, ScanJob

    source = session.scalar(select(DataSource).where(DataSource.slug == slug))
    if source is None:
        raise CliError(f"no data source {slug}")
    other = exists(select(DomainSourceRecord.id).where(DomainSourceRecord.domain_id == Domain.id,
                                                       DomainSourceRecord.source_id != source.id))
    mine = exists(select(DomainSourceRecord.id).where(DomainSourceRecord.domain_id == Domain.id,
                                                      DomainSourceRecord.source_id == source.id))
    ids = list(session.scalars(select(Domain.id).where(Domain.is_active, mine, ~other)))
    if dry_run or not ids:
        return len(ids), 0
    session.execute(update(Domain).where(Domain.id.in_(ids)).values(is_active=False))
    cancelled = session.execute(update(ScanJob).where(ScanJob.domain_id.in_(ids), ScanJob.status == JobStatus.QUEUED)
                                .values(status=JobStatus.CANCELLED, error_code="source_deactivated")).rowcount
    audit.record(session, action="domains.deactivate_source", target_type="data_source", target_id=slug,
                 after={"domains": len(ids), "cancelled_scans": cancelled})
    return len(ids), cancelled


def reclassify_provider(session: Session, provider: str, *, dry_run: bool = False) -> tuple[int, int]:
    """Re-scan (and so re-classify) active domains whose latest AI prediction came from ``provider``, e.g. after
    switching from the offline mock to a real model. Their open review tasks that carry such a prediction are
    closed first, so nobody approves the old answer. Human decisions are kept. Returns (domains, closed_tasks)."""
    from datetime import UTC, datetime

    from sqlalchemy import func

    from backend.app.services.domains import queue_scans
    from database.models import AIPrediction, ClassificationStatus, Domain, ReviewStatus, ReviewTask, ScanReason

    latest = (select(AIPrediction.domain_id, AIPrediction.provider,
                     func.row_number().over(partition_by=AIPrediction.domain_id,
                                            order_by=(AIPrediction.created_at.desc(), AIPrediction.id.desc()))
                     .label("rn")).subquery())
    ids = list(session.scalars(select(Domain.id).join(latest, latest.c.domain_id == Domain.id)
                               .where(Domain.is_active, latest.c.rn == 1, latest.c.provider == provider)
                               .order_by(Domain.id)))
    tasks = list(session.scalars(select(ReviewTask).join(AIPrediction, AIPrediction.id == ReviewTask.ai_prediction_id)
                                 .where(ReviewTask.domain_id.in_(ids), ReviewTask.status == ReviewStatus.OPEN,
                                        AIPrediction.provider == provider))) if ids else []
    if dry_run or not ids:
        return len(ids), len(tasks)
    now = datetime.now(UTC)
    for task in tasks:
        task.status, task.decided_at = ReviewStatus.SKIPPED, now
        task.decision_note = f"prediction from provider {provider!r}; domain queued for re-classification"
    for domain in session.scalars(select(Domain).where(Domain.id.in_(ids),
                                                       Domain.classification_status == ClassificationStatus.NEEDS_REVIEW)):
        domain.classification_status = ClassificationStatus.REANALYZE
    queued = queue_scans(session, ids, reason=ScanReason.RESCAN, priority=3, requested_by_id=None)
    audit.record(session, action="domains.reclassify_provider", target_type="ai_provider", target_id=provider,
                 after={"domains": len(ids), "closed_review_tasks": len(tasks), "queued_scans": queued})
    return len(ids), len(tasks)


def apply_registry_rules(session: Session, *, dry_run: bool = False) -> list[str]:
    """Apply registry allocation rules (classification/registry_rules.py) to live domains whose latest scan failed
    or was refused and that nothing else classified, e.g. public sites that drop bot traffic. New scans do this
    automatically; this catches up domains scanned before the rules existed. Returns the domain names."""
    from classification.registry_rules import apply_rule, rule_for
    from database.models import Domain, JobStatus, ScanJob, ScanResult

    done: list[str] = []
    for domain in session.scalars(select(Domain).where(Domain.is_active, Domain.normalized_domain.like("%.tr"))
                                  .order_by(Domain.id)):
        if rule_for(domain.normalized_domain) is None:
            continue
        job = session.scalar(select(ScanJob).where(ScanJob.domain_id == domain.id)
                             .order_by(ScanJob.queued_at.desc()).limit(1))
        if job is None or job.status not in (JobStatus.FAILED, JobStatus.BLOCKED) \
                or job.error_code == "resolves_to_blocked_ip":
            continue
        scan = session.scalar(select(ScanResult).where(ScanResult.domain_id == domain.id)
                              .order_by(ScanResult.created_at.desc()).limit(1))
        if scan is None or not scan.resolved_ips:  # never resolved: maybe not registered at all
            continue
        if dry_run:
            from classification.engine import current_assignments
            from database.models import AssignmentOrigin

            if all(r.origin == AssignmentOrigin.RULE for r in current_assignments(session, domain.id)):
                done.append(domain.normalized_domain)
        elif apply_rule(session, domain):
            done.append(domain.normalized_domain)
    if done and not dry_run:
        audit.record(session, action="domains.apply_registry_rules", target_type="registry_rules",
                     target_id="tr", after={"domains": done[:200], "count": len(done)})
    return done


def second_opinion_tasks(session: Session) -> list[int]:
    """Open reviews that only wait because the first model was 0.70-0.89 sure (oldest first)."""
    from database.models import ReviewStatus, ReviewTask

    return list(session.scalars(select(ReviewTask.id).where(ReviewTask.status == ReviewStatus.OPEN,
                                                            ReviewTask.reason == "needs_extra_validation")
                                .order_by(ReviewTask.id)))


def triage_lookalikes(session: Session, *, dry_run: bool = False) -> list[str]:
    """Close open look-alike reviews whose page the model calls parked with auto-approve confidence and no risk
    (the rule new classifications follow, classification.engine.parked_answer). The domains stay watched:
    re-checked every ``lookalike_recheck_days``; a changed page comes back to a person. Returns the domains."""
    from backend.app.classify_tasks import policy_from
    from classification.engine import close_lookalike_reviews, is_lookalike_reason, parked_answer
    from database.models import AIPrediction, Domain, Evidence, EvidenceType, ReviewStatus, ReviewTask

    settings = get_settings()
    policy = policy_from(settings)
    names = []
    rows = session.execute(select(ReviewTask, AIPrediction).join(AIPrediction, AIPrediction.id == ReviewTask.ai_prediction_id)
                           .where(ReviewTask.status == ReviewStatus.OPEN).order_by(ReviewTask.id)).all()
    for task, pred in rows:
        if not is_lookalike_reason(task.reason):
            continue
        injection = session.scalar(select(Evidence.id).where(Evidence.ai_prediction_id == pred.id,
                                                             Evidence.type == EvidenceType.SYSTEM).limit(1)) is not None
        if not parked_answer(primary=pred.primary_category_id,
                             confidence=float(pred.confidence) if pred.confidence is not None else None,
                             risk=pred.risk_flags, injection=injection, wants_human=pred.requires_human_review,
                             policy=policy):
            continue
        domain = session.get(Domain, task.domain_id)
        names.append(domain.normalized_domain)
        if not dry_run:
            close_lookalike_reviews(session, domain, f"parked look-alike (prediction {pred.id}); watched")
            _watch(domain, settings.lookalike_recheck_days)
    if names and not dry_run:
        audit.record(session, action="reviews.triage_lookalikes", target_type="review",
                     after={"closed": len(names), "recheck_days": settings.lookalike_recheck_days})
    return names


def _watch(domain, days: int) -> None:
    from database.models import ClassificationStatus

    if domain.classification_status not in (ClassificationStatus.CLASSIFIED, ClassificationStatus.VERIFIED):
        domain.classification_status = ClassificationStatus.REANALYZE
    domain.next_scan = datetime.now(UTC) + timedelta(days=days)


def retry_infra_failures(session: Session, *, hours: int = 48, dry_run: bool = False) -> list[str]:
    """Re-queue domains whose latest scan failed on our side (crawler.result.INFRA_ERRORS: sandbox, crawler,
    storage, egress proxy) within the last ``hours``, e.g. after a reboot. Returns the domains."""
    from sqlalchemy import func

    from backend.app.services.domains import queue_scans
    from crawler.result import INFRA_ERRORS
    from database.models import Domain, JobStatus, ScanJob, ScanReason

    since = datetime.now(UTC) - timedelta(hours=hours)
    latest = (select(ScanJob.domain_id, ScanJob.status, ScanJob.error_code,
                     func.row_number().over(partition_by=ScanJob.domain_id,
                                            order_by=(ScanJob.queued_at.desc(), ScanJob.id.desc())).label("rn"))
              .where(ScanJob.queued_at >= since).subquery())
    rows = session.execute(select(Domain.id, Domain.normalized_domain).join(latest, latest.c.domain_id == Domain.id)
                           .where(Domain.is_active, latest.c.rn == 1, latest.c.status == JobStatus.FAILED,
                                  latest.c.error_code.in_(sorted(INFRA_ERRORS)))
                           .order_by(Domain.id)).all()
    if rows and not dry_run:
        queued = queue_scans(session, [r[0] for r in rows], reason=ScanReason.RESCAN, priority=5, requested_by_id=None)
        audit.record(session, action="scans.retry_infra_failures", target_type="scan_job",
                     after={"domains": len(rows), "queued": queued, "hours": hours})
    return [r[1] for r in rows]


def _password(args) -> str | None:
    if not getattr(args, "password", False):
        return None
    first = getpass.getpass("Password (min 12 chars): ")
    if first != getpass.getpass("Repeat: "):
        raise CliError("passwords do not match")
    return first


def _print_key(key: ApiKey, secret: str) -> None:
    print(f"API key (shown only once, store it now):\n\n  {secret}\n")
    print(f"prefix={key.prefix} scopes={','.join(key.scopes)} expires={key.expires_at or 'never'}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m backend.cli", description="Cat-egory administration")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("create-admin", help="create an admin user and an API key with all admin scopes")
    p.add_argument("--email", required=True)
    p.add_argument("--name", required=True)
    p.add_argument("--password", action="store_true", help="also set a password (prompted)")

    p = sub.add_parser("create-user", help="create a user")
    p.add_argument("--email", required=True)
    p.add_argument("--name", required=True)
    p.add_argument("--role", choices=[r.value for r in Role], default=Role.CONTRIBUTOR.value)
    p.add_argument("--password", action="store_true", help="set a password (prompted)")

    p = sub.add_parser("create-key", help="create an API key for an existing user")
    p.add_argument("--email", required=True)
    p.add_argument("--name", required=True, help="label, e.g. 'laptop' or 'import-script'")
    p.add_argument("--scopes", help="comma separated; default: everything the user's role allows")
    p.add_argument("--rate-limit", type=int, help="requests per minute (default: server setting)")
    p.add_argument("--expires-days", type=int)

    p = sub.add_parser("list-keys", help="list API keys (never shows secrets)")
    p.add_argument("--email")

    p = sub.add_parser("revoke-key", help="revoke an API key")
    p.add_argument("--prefix", required=True)

    p = sub.add_parser("deactivate-source-domains",
                       help="hide domains known ONLY from one source (e.g. a test import) and cancel their scans")
    p.add_argument("--source", required=True, help="data source slug, e.g. file-import")
    p.add_argument("--yes", action="store_true", help="really do it (default is a dry run)")

    p = sub.add_parser("reclassify-provider",
                       help="re-scan domains whose latest AI answer came from one provider (e.g. mock)")
    p.add_argument("--provider", required=True, help="provider name, e.g. mock")
    p.add_argument("--yes", action="store_true", help="really do it (default is a dry run)")

    p = sub.add_parser("apply-registry-rules",
                       help="classify unreadable .gov.tr/.edu.tr/... domains from their registry allocation rule")
    p.add_argument("--yes", action="store_true", help="really do it (default is a dry run)")

    p = sub.add_parser("second-opinion",
                       help="ask the second EVREN model about open mid-confidence reviews (run in the worker)")
    p.add_argument("--yes", action="store_true", help="really queue them (default is a dry run)")

    p = sub.add_parser("triage-lookalikes",
                       help="close open look-alike reviews of parked pages (kept under watch, re-checked weekly)")
    p.add_argument("--yes", action="store_true", help="really do it (default is a dry run)")

    p = sub.add_parser("retry-infra-failures",
                       help="re-queue domains whose last scan failed on our side (sandbox, crawler, storage)")
    p.add_argument("--hours", type=int, default=48, help="look back this many hours (default 48)")
    p.add_argument("--yes", action="store_true", help="really queue them (default is a dry run)")

    p = sub.add_parser("sgb-check",
                       help="check threat domains against the national SGB/USOM list now (run in the worker)")
    p.add_argument("--limit", type=int, default=30, help="domains to ask about (default 30, max 200)")

    p = sub.add_parser("set-password", help="set or change a user's password (admin UI sign-in)")
    p.add_argument("--email", required=True)

    args = parser.parse_args(argv)
    if args.command == "sgb-check":  # network lookups: outside the CLI transaction, committed as it goes
        from backend.app.threat_tasks import run_sgb_check

        def show(name: str, matches: list[dict]) -> None:
            cats = ", ".join(sorted({str(m.get("category")) for m in matches}))
            print(f"  {name}: {'on the list (' + cats + ')' if matches else 'not on the list'}", flush=True)

        print("asking the SGB list (one domain per second) ...", flush=True)
        counts = run_sgb_check(limit=max(1, min(args.limit, 200)), progress=show)
        if counts.get("busy"):
            print("another check is running (the hourly task); try again in a few minutes")
            return 0
        print(f"checked {counts['checked']} domains, {counts['listed']} on the SGB list"
              + (" (stopped after an API error; try again later)" if counts["errors"] else ""))
        return 0
    try:
        with session_factory()() as session, session.begin():
            if args.command == "create-admin":
                user = create_user(session, email=args.email, name=args.name, role=Role.ADMIN, password=_password(args))
                key, secret = create_key(session, user, name="initial admin key")
                print(f"created admin user {user.email} (id {user.id})")
                _print_key(key, secret)
            elif args.command == "create-user":
                user = create_user(session, email=args.email, name=args.name, role=Role(args.role),
                                   password=_password(args))
                print(f"created {user.role} user {user.email} (id {user.id})")
            elif args.command == "create-key":
                scopes = [s.strip() for s in args.scopes.split(",") if s.strip()] if args.scopes else None
                key, secret = create_key(session, _user(session, args.email), name=args.name, scopes=scopes,
                                         rate_limit=args.rate_limit, expires_days=args.expires_days)
                _print_key(key, secret)
            elif args.command == "list-keys":
                stmt = select(ApiKey, User.email).join(User, User.id == ApiKey.user_id).order_by(ApiKey.id)
                if args.email:
                    stmt = stmt.where(User.email == args.email.strip().lower())
                for key, email in session.execute(stmt):
                    state = "revoked" if key.revoked_at else "active"
                    print(f"{key.prefix}  {state:7}  {email:30}  {key.name:20}  {','.join(key.scopes)}  "
                          f"last_used={key.last_used_at or '-'}")
            elif args.command == "set-password":
                user = _user(session, args.email)
                user.password_hash = hash_password(_password(argparse.Namespace(password=True)) or "")
                audit.record(session, action="user.set_password", target_type="user", target_id=user.id)
                print(f"password set for {user.email}; sign in at the admin UI")
            elif args.command == "revoke-key":
                key = revoke_key(session, args.prefix)
                print(f"revoked {key.prefix}")
            elif args.command == "deactivate-source-domains":
                domains, cancelled = deactivate_source_domains(session, args.source, dry_run=not args.yes)
                if args.yes:
                    print(f"deactivated {domains} domains, cancelled {cancelled} queued scans")
                else:
                    print(f"dry run: {domains} domains would be deactivated (add --yes to do it)")
            elif args.command == "apply-registry-rules":
                names = apply_registry_rules(session, dry_run=not args.yes)
                verb = "classified" if args.yes else "dry run: would classify"
                print(f"{verb} {len(names)} domains" + (f": {', '.join(names[:20])}" if names else ""))
            elif args.command == "second-opinion":
                from ai_providers.registry import get_second_opinion

                ids = second_opinion_tasks(session)
                if get_second_opinion() is None:
                    raise CliError("no second model: set CATEGORY_EVREN_SECOND_MODEL (different from "
                                   "CATEGORY_EVREN_MODEL) and run this in the worker container")
                if not args.yes:
                    print(f"dry run: {len(ids)} open reviews would get a second opinion (add --yes to do it)")
                else:
                    from backend.app.worker import celery_app

                    for task_id in ids:
                        celery_app.send_task("category.classify.second_opinion", args=[task_id])
                    print(f"queued {len(ids)} reviews for a second opinion; agreeing answers are published, "
                          "the rest stay in the review queue as model_disagreement")
            elif args.command == "triage-lookalikes":
                names = triage_lookalikes(session, dry_run=not args.yes)
                verb = "closed" if args.yes else "dry run: would close"
                print(f"{verb} {len(names)} parked look-alike reviews" + (f": {', '.join(names[:10])} ..." if names else ""))
            elif args.command == "retry-infra-failures":
                names = retry_infra_failures(session, hours=args.hours, dry_run=not args.yes)
                verb = "queued" if args.yes else "dry run: would queue"
                print(f"{verb} {len(names)} domains for a new scan" + (f": {', '.join(names[:10])} ..." if names else ""))
            elif args.command == "reclassify-provider":
                domains, tasks = reclassify_provider(session, args.provider, dry_run=not args.yes)
                if args.yes:
                    print(f"queued {domains} domains for re-classification, closed {tasks} open review tasks")
                else:
                    print(f"dry run: {domains} domains, {tasks} open review tasks (add --yes to do it)")
    except (CliError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
