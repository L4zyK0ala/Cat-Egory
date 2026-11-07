"""Administrative command line.

    python -m backend.cli create-admin --email you@example.org --name "Your Name"
    python -m backend.cli create-user  --email mod@example.org --name Mod --role moderator [--password]
    python -m backend.cli create-key   --email mod@example.org --name laptop --scopes scans:read,scans:write
    python -m backend.cli list-keys    [--email mod@example.org]
    python -m backend.cli revoke-key   --prefix abcdefghij

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

    args = parser.parse_args(argv)
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
            elif args.command == "revoke-key":
                key = revoke_key(session, args.prefix)
                print(f"revoked {key.prefix}")
            elif args.command == "deactivate-source-domains":
                domains, cancelled = deactivate_source_domains(session, args.source, dry_run=not args.yes)
                if args.yes:
                    print(f"deactivated {domains} domains, cancelled {cancelled} queued scans")
                else:
                    print(f"dry run: {domains} domains would be deactivated (add --yes to do it)")
    except (CliError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
