"""Cut a release: raise the version everywhere and close the changelog's Unreleased section.

    python deployment/scripts/release.py 0.3.0           # edit the files; commit, tag and push stay with you
    python deployment/scripts/release.py --notes 0.3.0   # print that version's changelog section (GitHub release)

Which number goes up (Semantic Versioning; CONTRIBUTING.md has the rule):
    patch  0.3.0 -> 0.3.1   fixes, security and image updates, docs: nothing new for users, nothing breaks
    minor  0.3.0 -> 0.4.0   something new (a list, a page, an API field or endpoint, a DNS server guide); before
                            1.0 also a breaking change, written under "Breaking" in the changelog
    major  0.x   -> 1.0.0   the API and the list formats are declared stable; after that, breaking = major

Files it changes: pyproject.toml, frontend/package.json, frontend/package-lock.json and CHANGELOG.md (the
Unreleased section becomes "## [X.Y.Z] – date" under a new, empty Unreleased, and the compare links follow).
Standard library only: the release workflow runs it on a bare runner.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
REPO = "https://github.com/L4zyK0ala/Cat-Egory"
SEMVER = re.compile(r"^(\d+)\.(\d+)\.(\d+)$")


def parse(version: str) -> tuple[int, int, int]:
    m = SEMVER.match(version)
    if not m:
        raise ValueError(f"not a version: {version!r} (X.Y.Z)")
    return int(m[1]), int(m[2]), int(m[3])


def current(root: Path = ROOT) -> str:
    m = re.search(r'^version = "([^"]+)"$', (root / "pyproject.toml").read_text(), re.M)
    if not m:
        raise ValueError("no version in pyproject.toml")
    return m[1]


def notes(version: str, root: Path = ROOT) -> str:
    """The changelog section of one version, without its heading."""
    text = (root / "CHANGELOG.md").read_text()
    m = re.search(rf"^## \[{re.escape(version)}\][^\n]*\n(.*?)(?=^## \[|^\[Unreleased\]:)", text, re.M | re.S)
    if not m or not m[1].strip():
        raise ValueError(f"CHANGELOG.md has no section for {version}")
    return m[1].strip() + "\n"


def changelog(text: str, old: str, new: str, day: date) -> str:
    head = "## [Unreleased]\n"
    if head not in text:
        raise ValueError("CHANGELOG.md has no '## [Unreleased]' section")
    body = text.split(head, 1)[1].split("\n## [", 1)[0]
    if not re.search(r"^- ", body, re.M):
        raise ValueError("the Unreleased section is empty: nothing to release")
    text = text.replace(head, f"{head}\n## [{new}] – {day.isoformat()}\n", 1)
    link = f"[Unreleased]: {REPO}/compare/v{old}...HEAD"
    if link not in text:
        raise ValueError(f"CHANGELOG.md has no line {link!r}")
    return text.replace(link, f"[Unreleased]: {REPO}/compare/v{new}...HEAD\n"
                              f"[{new}]: {REPO}/compare/v{old}...v{new}", 1)


def bump(new: str, root: Path = ROOT, day: date | None = None) -> str:
    """Write the new version into every file; returns the old one. Checks everything before writing anything."""
    old = current(root)
    if parse(new) <= parse(old):
        raise ValueError(f"{new} is not above the current {old}")
    py = root / "pyproject.toml"
    pkg, lock, log = root / "frontend/package.json", root / "frontend/package-lock.json", root / "CHANGELOG.md"
    new_log = changelog(log.read_text(), old, new, day or date.today())
    package = json.loads(pkg.read_text())
    locked = json.loads(lock.read_text())
    if package.get("version") != old or locked.get("version") != old:
        raise ValueError(f"frontend/package.json or its lock file is not at {old}")
    package["version"] = new
    locked["version"] = new
    locked.get("packages", {}).get("", {})["version"] = new
    py.write_text(re.sub(r'^version = "[^"]+"$', f'version = "{new}"', py.read_text(), count=1, flags=re.M))
    pkg.write_text(json.dumps(package, indent=2, ensure_ascii=False) + "\n")
    lock.write_text(json.dumps(locked, indent=2, ensure_ascii=False) + "\n")
    log.write_text(new_log)
    return old


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("version", help="the new version, X.Y.Z")
    ap.add_argument("--notes", action="store_true", help="print the version's changelog section and change nothing")
    args = ap.parse_args(argv)
    try:
        if args.notes:
            sys.stdout.write(notes(args.version))
            return 0
        old = bump(args.version)
    except ValueError as exc:
        print(f"release: {exc}", file=sys.stderr)
        return 1
    print(f"{old} -> {args.version}: pyproject.toml, frontend/package.json, package-lock.json, CHANGELOG.md\n"
          f"next: git commit -am 'Release {args.version}' && git tag -a v{args.version} -m v{args.version} && "
          f"git push origin main v{args.version}  (the Release workflow publishes the GitHub release)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
