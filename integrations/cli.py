"""Source administration.

    python -m integrations.cli list
    python -m integrations.cli register urlhaus
    python -m integrations.cli approve urlhaus --license-slug CC0-1.0 --license-name "CC0 1.0" \
        --license-url https://... --redistribution yes --commercial yes --attribution-required no \
        --reviewer you@example.org --notes "read terms on 2026-11-07"
    python -m integrations.cli sync urlhaus
"""
from __future__ import annotations

import argparse
import sys

from sqlalchemy import select

from database.models import DataSource, User
from database.session import session_factory

from .engine import SourceError, approve, catalogue, register, sync


def _yes(value: str) -> bool:
    if value.lower() in ("yes", "true", "1"):
        return True
    if value.lower() in ("no", "false", "0"):
        return False
    raise argparse.ArgumentTypeError("use yes or no")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m integrations.cli")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("list")
    p = sub.add_parser("register")
    p.add_argument("slug")
    p = sub.add_parser("approve", help="record the licence you have read and enable the source")
    p.add_argument("slug")
    p.add_argument("--license-slug", required=True)
    p.add_argument("--license-name", required=True)
    p.add_argument("--license-url")
    p.add_argument("--redistribution", type=_yes, required=True)
    p.add_argument("--commercial", type=_yes, required=True)
    p.add_argument("--attribution-required", type=_yes, required=True)
    p.add_argument("--attribution")
    p.add_argument("--reviewer", required=True, help="email of the maintainer who read the licence")
    p.add_argument("--notes")
    p = sub.add_parser("sync")
    p.add_argument("slug")
    args = parser.parse_args(argv)

    entries = catalogue()
    try:
        with session_factory()() as session, session.begin():
            if args.command == "list":
                db = {s.slug: s for s in session.scalars(select(DataSource))}
                for slug, entry in entries.items():
                    s = db.get(slug)
                    state = "not registered" if s is None else ("enabled" if s.enabled else "disabled")
                    print(f"{slug:28} {entry['kind']:12} {state:15} last_success={getattr(s, 'last_success_at', None)}"
                          f"  believed: {entry.get('believed_license')}")
            elif args.command == "register":
                if args.slug not in entries:
                    raise SourceError("unknown_source", f"{args.slug} is not in sources.yaml")
                register(session, args.slug, entries[args.slug])
                print(f"registered {args.slug} (disabled until approved)")
            elif args.command == "approve":
                reviewer = session.scalar(select(User).where(User.email == args.reviewer.strip().lower()))
                if reviewer is None:
                    raise SourceError("unknown_user", f"no user {args.reviewer}")
                approve(session, args.slug, license_slug=args.license_slug, license_name=args.license_name,
                        license_url=args.license_url, redistribution_allowed=args.redistribution,
                        commercial_use_allowed=args.commercial, attribution_required=args.attribution_required,
                        attribution=args.attribution, reviewer_id=reviewer.id, notes=args.notes)
                print(f"approved and enabled {args.slug}")
            elif args.command == "sync":
                result = sync(session, args.slug, entry=entries.get(args.slug, {}))
                print(result)
                if result.errors:
                    return 1
    except SourceError as exc:
        print(f"error: {exc.code}: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
