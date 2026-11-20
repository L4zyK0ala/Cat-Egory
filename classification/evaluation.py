"""Measure classification quality on a set of known sites (``evaluation_set.yaml``).

    python -m classification.evaluation add [--yes]     # add the sites and queue scans (dry run without --yes)
    python -m classification.evaluation add --failed --yes   # re-queue only sites without a valid answer
    python -m classification.evaluation report [--provider evren]
    ... --set evaluation_holdout.yaml   # bundled set of sites never used to tune the prompt
    python -m classification.evaluation compare --models qwen3.8-flash-next,glm-5.3 [--yes] [--again]
    python -m classification.evaluation compare-report --models qwen3.8-flash-next,glm-5.3
    ... --set verified:150   # 150 random sites a person checked (reproducible: --seed), from the database
    ... --set hard:80        # 80 sites where a person changed the AI's main category

``report`` reads the latest prediction of the given provider for every site, compares its primary category with
the accepted answers and prints a Markdown report: crawl failures, valid-answer rate, strict / lenient accuracy,
accuracy per confidence band, wrong answers that were published automatically, and latency.
``compare`` asks several EVREN models about the same scanned sites and stores their answers apart from the
production predictions (see ``classification/model_compare.py``); ``compare-report`` prints the side-by-side report
from what is stored, without calling any model.
Nothing here is shown publicly; it is an operator tool.

Sets from the database (``verified:N``, ``hard:N``) take a person's decision as the answer: the site's current main
category first, then its other current categories. They are drawn in a fixed order from ``--seed``, so the same
command picks the same sites again (as long as no site enters or leaves the pool). Caveat for ``verified``: the
person saw the production model's answer first, which can favour that model; ``hard`` is where it was overruled.
"""
from __future__ import annotations

import argparse
import statistics
import sys
from dataclasses import dataclass, field
from pathlib import Path

import yaml
from sqlalchemy import func, literal, select
from sqlalchemy.orm import Session

from database.models import AIPrediction, Domain, ScanJob, ScanResult

DB_SETS = ("verified", "hard")

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


def db_set(spec: str) -> tuple[str, int] | None:
    """``verified:150`` -> ("verified", 150); a file name -> None."""
    kind, _, size = str(spec).partition(":")
    if kind not in DB_SETS:
        return None
    if not size.isdigit() or not 1 <= int(size) <= 2000:
        raise ValueError(f"{spec}: expected {kind}:N with N from 1 to 2000")
    return kind, int(size)


def load_db_set(session: Session, kind: str, size: int, *, seed: int = 2026, taxonomy=None) -> list[Site]:
    """Person-checked sites with a successful whole-site scan, in a fixed pseudo-random order."""
    from database.models import ClassificationStatus, DomainCategory, JobStatus, ReviewStatus, ReviewTask

    from .accuracy import HARD_REASONS
    from .taxonomy import default_taxonomy

    content = set((taxonomy or default_taxonomy()).assignable_ids("content"))
    scanned = (select(ScanResult.id).join(ScanJob, ScanJob.id == ScanResult.scan_job_id)
               .where(ScanResult.domain_id == Domain.id, ScanJob.status == JobStatus.SUCCEEDED,
                      ScanResult.url_id.is_(None)).exists())
    stmt = select(Domain.id, Domain.normalized_domain, Domain.primary_category_id).where(
        Domain.is_active, Domain.classification_status == ClassificationStatus.VERIFIED,
        Domain.primary_category_id.in_(content), scanned)
    if kind == "hard":
        overruled = (select(ReviewTask.id).join(AIPrediction, AIPrediction.id == ReviewTask.ai_prediction_id)
                     .where(ReviewTask.domain_id == Domain.id, ReviewTask.status == ReviewStatus.DONE,
                            ReviewTask.reason.in_(HARD_REASONS), ReviewTask.decision_primary_id == Domain.primary_category_id,
                            AIPrediction.primary_category_id.is_not(None),
                            AIPrediction.primary_category_id != ReviewTask.decision_primary_id).exists())
        stmt = stmt.where(overruled)
    order = func.md5(func.concat(Domain.normalized_domain, literal(f":{seed}")))
    rows = session.execute(stmt.order_by(order).limit(size)).all()
    others: dict[int, list[str]] = {}
    for domain_id, category in session.execute(
            select(DomainCategory.domain_id, DomainCategory.category_id)
            .where(DomainCategory.domain_id.in_([r.id for r in rows]), DomainCategory.valid_to.is_(None),
                   DomainCategory.is_primary.is_(False))
            .order_by(DomainCategory.domain_id, DomainCategory.category_id)):
        if category in content:
            others.setdefault(domain_id, []).append(category)
    return [Site(r.normalized_domain, [r.primary_category_id, *others.get(r.id, [])]) for r in rows]


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
        scan = session.scalar(select(ScanResult).where(ScanResult.domain_id == domain.id, ScanResult.url_id.is_(None))
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


def p95(values: list[int]) -> int:
    """Nearest-rank 95th percentile of a non-empty list."""
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int(round(0.95 * (len(ordered) - 1))))]


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
        out.append(f"| latency median / p95 / max | {statistics.median(latencies) / 1000:.0f} s / "
                   f"{p95(latencies) / 1000:.0f} s / {latencies[-1] / 1000:.0f} s |")
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
    p.add_argument("--set", default=str(SET_PATH), help="evaluation set: a YAML file, verified:N or hard:N")
    p = sub.add_parser("report", help="print a Markdown report")
    p.add_argument("--provider", default="evren")
    p.add_argument("--set", default=str(SET_PATH), help="evaluation set: a YAML file, verified:N or hard:N")
    p = sub.add_parser("compare", help="ask several EVREN models about the same scanned sites (dry run without --yes)")
    p.add_argument("--models", required=True, help="comma-separated EVREN model names")
    p.add_argument("--set", default=str(SET_PATH), help="evaluation set: a YAML file, verified:N or hard:N")
    p.add_argument("--yes", action="store_true", help="really call the models (default is a dry run)")
    p.add_argument("--again", action="store_true", help="ask again where a valid answer for this prompt exists")
    p.add_argument("--pause", type=float, default=2.0, help="seconds between two calls (default 2)")
    p.add_argument("--seed", type=int, default=2026, help="which sites verified:N / hard:N pick (default 2026)")
    p = sub.add_parser("compare-report", help="Markdown report of stored comparison answers (no model calls)")
    p.add_argument("--models", required=True, help="comma-separated EVREN model names")
    p.add_argument("--set", default=str(SET_PATH), help="evaluation set: a YAML file, verified:N or hard:N")
    p.add_argument("--seed", type=int, default=2026, help="which sites verified:N / hard:N pick (default 2026)")
    args = parser.parse_args(argv)

    from database.session import session_factory

    spec = str(getattr(args, "set", SET_PATH))
    try:
        from_db = db_set(spec)
        if from_db and args.command not in ("compare", "compare-report"):
            raise ValueError("verified:N / hard:N work with compare and compare-report only")
        sites = [] if from_db else load_set(spec)
        if args.command in ("compare", "compare-report"):
            from .model_compare import check_models

            args.models = check_models(args.models)
    except (OSError, ValueError, KeyError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    if args.command in ("compare", "compare-report"):
        with session_factory()() as session:
            if from_db:
                sites = load_db_set(session, *from_db, seed=args.seed)
                if not sites:
                    print(f"error: no person-checked sites for {spec}", file=sys.stderr)
                    return 1
            return compare_command(session, sites, args)
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


def compare_command(session: Session, sites: list[Site], args: argparse.Namespace) -> int:
    from ai_providers.prompting import PROMPT_VERSION
    from common.config import get_settings

    from . import model_compare
    from .taxonomy import default_taxonomy

    set_name = (f"{args.set}, seed {args.seed}, {len(sites)} sites, answer: a person's decision" if db_set(args.set)
                else resolve_set(args.set).name)
    code = 0
    if args.command == "compare":
        p = model_compare.plan(session, sites, args.models, prompt_version=PROMPT_VERSION,
                               taxonomy_version=default_taxonomy().version, again=args.again)
        if not args.yes:
            print(model_compare.describe(p, set_name))
            session.rollback()
            return 0
        settings = get_settings()
        if p.todo:
            if not settings.evren_api_key:
                print("error: CATEGORY_EVREN_API_KEY is not set here; run this in the worker container",
                      file=sys.stderr)
                return 1
            summary = model_compare.run(session, p, lambda model: model_compare.make_provider(settings, model),
                                        store=model_compare.make_store(settings),
                                        use_screenshot=settings.classify_use_screenshot, pause=max(args.pause, 0.0))
            print(f"asked {summary.asked}: {summary.valid} valid, {summary.failed} failed; "
                  f"{p.skipped} skipped (already answered)", file=sys.stderr)
            if summary.stopped:
                print(f"stopped early ({summary.stopped}); run the same command again to resume", file=sys.stderr)
                code = 2
        else:
            print(f"nothing to ask: {p.skipped} pairs already answered (--again asks anew)", file=sys.stderr)
    report = model_compare.render(sites, args.models, model_compare.collect(session, sites, args.models),
                                  set_name=set_name, prompt_version=PROMPT_VERSION)
    session.rollback()
    print(report, end="")
    return code


if __name__ == "__main__":
    sys.exit(main())
