"""Measure classification quality on a set of known sites (``evaluation_set.yaml``).

    python -m classification.evaluation add [--yes]     # add the sites and queue scans (dry run without --yes)
    python -m classification.evaluation add --failed --yes   # re-queue only sites without a valid answer
    python -m classification.evaluation report [--provider evren]
    ... --set evaluation_holdout.yaml   # bundled set of sites never used to tune the prompt

``report`` reads the latest prediction of the given provider for every site, compares its primary category with
the accepted answers and prints a Markdown report: crawl failures, valid-answer rate, strict / lenient accuracy,
accuracy per confidence band, wrong answers that were published automatically, and latency.
Nothing here is shown publicly; it is an operator tool.
"""
from __future__ import annotations

import argparse
import statistics
import sys
from dataclasses import dataclass, field
from pathlib import Path

import yaml
from sqlalchemy import select
from sqlalchemy.orm import Session

from database.models import AIPrediction, Domain, ScanJob, ScanResult

SET_PATH = Path(__file__).with_name("evaluation_set.yaml")
BANDS = ((0.90, 1.01, ">= 0.90 (auto-publish)"), (0.70, 0.90, "0.70-0.89 (second model)"),
         (0.50, 0.70, "0.50-0.69 (human review)"), (0.0, 0.50, "< 0.50 (re-analyse)"))


@dataclass
class Site:
    domain: str
    accept: list[str]
    reject: list[str] = field(default_factory=list)


@dataclass
class Row:
    site: Site
    crawl: str = "not added"  # not added | not scanned | queued | running | succeeded | failed | blocked | ...
    crawl_error: str | None = None
    title: str | None = None
    validation: str | None = None
    primary: str | None = None
    secondary: list[str] = field(default_factory=list)
    confidence: float | None = None
    status: str | None = None  # domain classification status
    latency_ms: int | None = None
    output_tokens: int | None = None
    errors: list[str] = field(default_factory=list)

    @property
    def answered(self) -> bool:
        return self.validation == "valid" and self.primary is not None

    @property
    def strict(self) -> bool:
        return self.answered and self.primary == self.site.accept[0]

    @property
    def lenient(self) -> bool:
        return self.answered and self.primary in self.site.accept

    @property
    def rejected_secondary(self) -> list[str]:
        return [c for c in self.secondary if c in self.site.reject]

    @property
    def published_wrong(self) -> bool:
        return self.answered and not self.lenient and self.status == "classified"


def resolve_set(path: Path | str) -> Path:
    """A path, or just the file name of a set bundled with the package (``evaluation_holdout.yaml``)."""
    path = Path(path)
    if not path.exists() and (SET_PATH.parent / path.name).exists():
        return SET_PATH.parent / path.name
    return path


def load_set(path: Path = SET_PATH, taxonomy=None) -> list[Site]:
    from common.domains import normalize_domain

    from .taxonomy import default_taxonomy

    path = resolve_set(path)

    known = set((taxonomy or default_taxonomy()).assignable_ids("content"))
    with open(path, encoding="utf-8") as fh:
        raw = yaml.safe_load(fh)["sites"]
    sites = []
    for entry in raw:
        site = Site(normalize_domain(entry["domain"]).ascii, list(entry["accept"]), list(entry.get("reject", [])))
        unknown = [c for c in site.accept + site.reject if c not in known]
        if unknown or not site.accept:
            raise ValueError(f"{site.domain}: unknown or missing categories {unknown}")
        sites.append(site)
    return sites


def add(session: Session, sites: list[Site], *, dry_run: bool = False) -> tuple[int, int]:
    """Add the sites through the normal admin path (source ``manual``) and queue a scan for each.
    Returns (new domains, queued scans)."""
    from backend.app import audit
    from backend.app.services.domains import add_domains
    from database.models import DataSource, ScanReason

    if dry_run:
        existing = set(session.scalars(select(Domain.normalized_domain)
                                       .where(Domain.normalized_domain.in_([s.domain for s in sites]))))
        return len([s for s in sites if s.domain not in existing]), len(sites)
    source = session.scalar(select(DataSource).where(DataSource.slug == "manual"))
    # an evaluation site that was hidden earlier (e.g. by deactivate-source-domains) is in use again
    session.execute(Domain.__table__.update().where(Domain.normalized_domain.in_([s.domain for s in sites]))
                    .values(is_active=True))
    result = add_domains(session, list(enumerate((s.domain for s in sites), 1)), source=source, scan="all",
                         reason=ScanReason.MANUAL, priority=4)
    audit.record(session, action="evaluation.add", target_type="evaluation_set", target_id=SET_PATH.name,
                 after={"sites": len(sites), "created": result.created, "scans_queued": result.scans_queued})
    return result.created, result.scans_queued


def collect(session: Session, sites: list[Site], provider: str) -> list[Row]:
    rows = []
    for site in sites:
        row = Row(site)
        rows.append(row)
        domain = session.scalar(select(Domain).where(Domain.normalized_domain == site.domain))
        if domain is None:
            continue
        row.status = str(domain.classification_status)
        job = session.scalar(select(ScanJob).where(ScanJob.domain_id == domain.id)
                             .order_by(ScanJob.queued_at.desc()).limit(1))
        row.crawl = str(job.status) if job else "not scanned"
        row.crawl_error = job.error_code if job else None
        scan = session.scalar(select(ScanResult).where(ScanResult.domain_id == domain.id)
                              .order_by(ScanResult.created_at.desc()).limit(1))
        row.title = scan.page_title if scan else None
        pred = session.scalar(select(AIPrediction).where(AIPrediction.domain_id == domain.id,
                                                         AIPrediction.provider == provider)
                              .order_by(AIPrediction.created_at.desc(), AIPrediction.id.desc()).limit(1))
        if pred is not None:
            row.validation = str(pred.validation_status)
            row.primary, row.secondary = pred.primary_category_id, list(pred.secondary_category_ids or [])
            row.confidence = float(pred.confidence) if pred.confidence is not None else None
            row.latency_ms, row.output_tokens = pred.latency_ms, pred.output_tokens
            row.errors = [str(e) for e in (pred.validation_errors or [])]
    return rows


def _pct(n: int, d: int) -> str:
    return f"{n}/{d} ({100 * n / d:.0f} %)" if d else "-"


def render(rows: list[Row], provider: str) -> str:
    answered = [r for r in rows if r.answered]
    predicted = [r for r in rows if r.validation is not None]
    crawl_problems = [r for r in rows if r.crawl not in ("succeeded",)]
    out = [f"# Classification evaluation ({provider})", ""]
    out += ["| | |", "|---|---|",
            f"| sites | {len(rows)} |",
            f"| crawled successfully | {_pct(len(rows) - len(crawl_problems), len(rows))} |",
            f"| model answers | {len(predicted)} |",
            f"| valid answers | {_pct(len(answered), len(predicted))} |",
            f"| primary correct, strict (best answer) | {_pct(sum(r.strict for r in answered), len(answered))} |",
            f"| primary correct, lenient (acceptable) | {_pct(sum(r.lenient for r in answered), len(answered))} |",
            f"| wrong and published automatically | {sum(r.published_wrong for r in rows)} |",
            f"| wrong secondary categories (from `reject`) | {sum(len(r.rejected_secondary) for r in rows)} |"]
    latencies = sorted(r.latency_ms for r in predicted if r.latency_ms)
    if latencies:
        p95 = latencies[min(len(latencies) - 1, int(round(0.95 * (len(latencies) - 1))))]
        out.append(f"| latency median / p95 / max | {statistics.median(latencies) / 1000:.0f} s / "
                   f"{p95 / 1000:.0f} s / {latencies[-1] / 1000:.0f} s |")
    out += ["", "## Accuracy per stated confidence", "", "| band | answers | lenient correct |", "|---|---|---|"]
    for low, high, label in BANDS:
        band = [r for r in answered if r.confidence is not None and low <= r.confidence < high]
        out.append(f"| {label} | {len(band)} | {_pct(sum(r.lenient for r in band), len(band))} |")
    out += ["", "## Sites", "", "| domain | crawl | primary | secondary | conf. | status | verdict |",
            "|---|---|---|---|---|---|---|"]
    for r in rows:
        if not r.answered:
            verdict = r.validation or "no answer"
            if r.errors:
                verdict += ": " + "; ".join(r.errors)[:120].replace("|", "/")
        elif r.strict:
            verdict = "correct"
        elif r.lenient:
            verdict = f"acceptable (best: {r.site.accept[0]})"
        else:
            verdict = f"**wrong** (expected {', '.join(r.site.accept)})"
        if r.rejected_secondary:
            verdict += f"; wrong secondary {', '.join(r.rejected_secondary)}"
        crawl = r.crawl + (f" ({r.crawl_error})" if r.crawl_error else "")
        conf = f"{r.confidence:.2f}" if r.confidence is not None else ""
        out.append(f"| {r.site.domain} | {crawl} | {r.primary or ''} | {', '.join(r.secondary)} | {conf} | "
                   f"{r.status or ''} | {verdict} |")
    return "\n".join(out) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m classification.evaluation", description=__doc__.split("\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("add", help="add the evaluation sites and queue scans")
    p.add_argument("--yes", action="store_true", help="really do it (default is a dry run)")
    p.add_argument("--failed", action="store_true", help="only sites whose latest answer is missing or invalid")
    p.add_argument("--provider", default="evren", help="provider whose answers --failed looks at")
    p.add_argument("--set", type=Path, default=SET_PATH, help="evaluation set (YAML)")
    p = sub.add_parser("report", help="print a Markdown report")
    p.add_argument("--provider", default="evren")
    p.add_argument("--set", type=Path, default=SET_PATH, help="evaluation set (YAML)")
    args = parser.parse_args(argv)

    from database.session import session_factory

    try:
        sites = load_set(getattr(args, "set", SET_PATH))
    except (OSError, ValueError, KeyError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    with session_factory()() as session, session.begin():
        if args.command == "add":
            if args.failed:
                sites = [r.site for r in collect(session, sites, args.provider) if not r.answered]
            new, queued = add(session, sites, dry_run=not args.yes)
            if args.yes:
                print(f"{len(sites)} sites: {new} new domains, {queued} scans queued")
            else:
                print(f"dry run: {len(sites)} sites, {new} new domains, up to {queued} scans (add --yes)")
        else:
            print(render(collect(session, sites, args.provider), args.provider), end="")
    return 0


if __name__ == "__main__":
    sys.exit(main())
