"""Ask several models about the same evaluation sites and compare them (``python -m classification.evaluation compare``).

For every site of an evaluation set that has a successful scan, the stored page (text, HTML excerpt and, for
models that take images, the screenshot) is shown to each listed EVREN model with the production prompt
(``AIProvider.classify``: same prompt builder, same parser and validation, same injection check). All models see
the same scan of a site.

Where the answers live: the ``model_comparisons`` table, not ``ai_predictions``. Nothing that publishes,
reviews or computes the public accuracy reads that table, so a comparison cannot change a domain's status or
categories, open a review task or trigger a second opinion, and ``evaluation report`` keeps measuring the
production pipeline only. A table rather than a result file because the worker container (the only one that
holds the EVREN key) is read-only, and because re-runs must know which answers were already paid for.

Calls are made one after another from this process, with a pause between them, on top of the worker's own two
classify slots; the provider's retry rules (same Idempotency-Key, back-off on 502/503/504, waiting while the
gateway is still working) apply unchanged. A site/model pair that already has a valid answer for the current
prompt and taxonomy versions is skipped unless ``again`` is set. The run stops at the first error that a later
call would only repeat (auth, credit, permission, rate limit, missing key); running it again resumes.
"""
from __future__ import annotations

import statistics
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from itertools import combinations

from sqlalchemy import select
from sqlalchemy.orm import Session

from ai_providers.base import AIProvider
from ai_providers.evren import KNOWN_MODELS
from ai_providers.schemas import ProviderResult
from database.models import Domain, JobStatus, ModelComparison, ScanJob, ScanResult

from .engine import build_input
from .evaluation import BANDS, Row, Site, _pct, p95

# Provider error kinds that every following call would hit as well: stop, the next run resumes.
STOP_KINDS = ("auth", "credit", "permission", "rate", "config")


def check_models(raw: str) -> list[str]:
    """Comma-separated model names -> list (order kept, duplicates removed). Only models known to work through the
    gateway (``ai_providers.evren.KNOWN_MODELS``) are accepted, so a typo cannot burn calls on errors."""
    models = list(dict.fromkeys(m.strip() for m in raw.split(",") if m.strip()))
    if not models:
        raise ValueError("no models given")
    unknown = [m for m in models if m not in KNOWN_MODELS]
    if unknown:
        raise ValueError(f"unknown model(s) {', '.join(unknown)}; known: {', '.join(KNOWN_MODELS)}")
    return models


def takes_images(model: str) -> bool | None:
    """True / False for models in the capability table, None for others (e.g. a test double)."""
    return KNOWN_MODELS[model]["images"] if model in KNOWN_MODELS else None


# ----------------------------------------------------------------------------- planning


@dataclass
class Target:
    site: Site
    domain_id: int
    scan_result_id: int


@dataclass
class Plan:
    models: list[str]
    targets: list[Target] = field(default_factory=list)  # sites with a successful scan
    unscanned: list[Site] = field(default_factory=list)  # not in the database, or no successful scan yet
    todo: list[tuple[Target, str]] = field(default_factory=list)  # (site, model) pairs to ask, in order
    skipped: int = 0  # pairs that already have a valid answer


def latest_scan(session: Session, domain_id: int) -> int | None:
    return session.scalar(select(ScanResult.id).join(ScanJob, ScanJob.id == ScanResult.scan_job_id)
                          .where(ScanResult.domain_id == domain_id, ScanJob.status == JobStatus.SUCCEEDED)
                          .order_by(ScanResult.created_at.desc(), ScanResult.id.desc()).limit(1))


def plan(session: Session, sites: list[Site], models: list[str], *, prompt_version: str, taxonomy_version: str,
         again: bool = False) -> Plan:
    out = Plan(models=models)
    for site in sites:
        domain_id = session.scalar(select(Domain.id).where(Domain.normalized_domain == site.domain))
        scan_id = latest_scan(session, domain_id) if domain_id is not None else None
        if scan_id is None:
            out.unscanned.append(site)
            continue
        target = Target(site, domain_id, scan_id)
        out.targets.append(target)
        done = set() if again else set(session.scalars(
            select(ModelComparison.model).where(ModelComparison.domain_id == domain_id,
                                                ModelComparison.model.in_(models),
                                                ModelComparison.validation_status == "valid",
                                                ModelComparison.prompt_version == prompt_version,
                                                ModelComparison.taxonomy_version == taxonomy_version)))
        for model in models:
            if model in done:
                out.skipped += 1
            else:
                out.todo.append((target, model))
    return out


def describe(p: Plan, set_name: str) -> str:
    """The dry-run summary: what a real run would ask."""
    lines = [f"dry run ({set_name}): {len(p.targets) + len(p.unscanned)} sites, {len(p.targets)} with a successful scan"]
    if p.unscanned:
        lines.append(f"  without a successful scan (skipped): {', '.join(s.domain for s in p.unscanned)}")
    lines.append(f"  {len(p.models)} models x {len(p.targets)} sites = {len(p.models) * len(p.targets)} pairs, "
                 f"{p.skipped} already answered -> {len(p.todo)} EVREN calls")
    for model in p.models:
        n = sum(1 for _, m in p.todo if m == model)
        note = "  (text only: the model takes no images)" if takes_images(model) is False else ""
        lines.append(f"    {model}: {n}{note}")
    lines.append("  (a retry after a gateway timeout reuses the Idempotency-Key and is not billed twice)")
    lines.append("add --yes to ask them")
    return "\n".join(lines)


# ----------------------------------------------------------------------------- asking


@dataclass
class RunSummary:
    asked: int = 0
    valid: int = 0
    failed: int = 0
    stopped: str | None = None  # why the run stopped early


def error_kind(result: ProviderResult) -> str | None:
    if result.status.value != "provider_error" or not result.errors:
        return None
    return result.errors[0].split(":", 1)[0].strip()


def record(session: Session, target: Target, model: str, result: ProviderResult, *,
           screenshot_available: bool) -> ModelComparison:
    out = result.output
    row = ModelComparison(
        domain_id=target.domain_id, scan_result_id=target.scan_result_id, provider=result.provider, model=model,
        answered_by=result.model, prompt_version=result.prompt_version, taxonomy_version=result.taxonomy_version,
        validation_status=result.status.value, validation_errors=result.errors[:20],
        primary_category_id=out.primary_category if out and result.ok else None,
        secondary_category_ids=list(out.secondary_categories) if out and result.ok else [],
        risk_flags=list(out.risk_categories) if out and result.ok else [],
        confidence=Decimal(str(round(out.confidence, 3))) if out else None,
        screenshot_available=screenshot_available, used_screenshot=result.used_screenshot,
        injection_suspected=bool(result.injection_signals), raw_response=result.raw_json,
        input_tokens=result.input_tokens, output_tokens=result.output_tokens, latency_ms=result.latency_ms,
        request_id=result.request_id,
    )
    session.add(row)
    return row


def run(session: Session, p: Plan, provider_for: Callable[[str], AIProvider], *, store=None,
        use_screenshot: bool = True, pause: float = 2.0, sleep: Callable[[float], None] = time.sleep,
        progress: Callable[[str], None] | None = None) -> RunSummary:
    """Ask every pair of ``p.todo``, one call at a time. Each answer is committed on its own, so an interrupted run
    loses at most the call in flight and the next run picks up where this one stopped."""
    progress = progress or (lambda line: print(line, file=sys.stderr, flush=True))
    providers: dict[str, AIProvider] = {}
    summary = RunSummary()
    by_site: dict[int, list[str]] = {}
    targets: dict[int, Target] = {}
    for target, model in p.todo:
        by_site.setdefault(target.scan_result_id, []).append(model)
        targets[target.scan_result_id] = target
    for scan_id, models in by_site.items():
        target = targets[scan_id]
        data = build_input(session, session.get(ScanResult, scan_id), store, use_screenshot=use_screenshot)
        session.commit()  # no transaction stays open while a model thinks
        for model in models:
            if summary.asked:
                sleep(pause)
            provider = providers.get(model) or providers.setdefault(model, provider_for(model))
            result = provider.classify(data)
            record(session, target, model, result, screenshot_available=data.screenshot is not None)
            session.commit()
            summary.asked += 1
            summary.valid += result.ok
            summary.failed += not result.ok
            answer = (f"{result.output.primary_category} {result.output.confidence:.2f}" if result.ok
                      else f"{result.status.value}: {'; '.join(result.errors)[:120]}")
            seconds = f" ({result.latency_ms / 1000:.0f} s)" if result.latency_ms is not None else ""
            progress(f"[{summary.asked}/{len(p.todo)}] {target.site.domain} · {model}: {answer}{seconds}")
            kind = error_kind(result)
            if kind in STOP_KINDS:
                summary.stopped = f"{model}: {result.errors[0]}"
                return summary
    return summary


# ----------------------------------------------------------------------------- report


@dataclass
class Answer(Row):
    model: str = ""
    prompt_version: str | None = None
    scan_result_id: int | None = None
    screenshot_available: bool = False
    used_screenshot: bool = False

    @property
    def confident_wrong(self) -> bool:
        return self.answered and not self.lenient and (self.confidence or 0) >= 0.90


def collect(session: Session, sites: list[Site], models: list[str]) -> dict[tuple[str, str], Answer]:
    """The latest stored answer of each model for each site, keyed by (domain, model)."""
    by_name = {s.domain: s for s in sites}
    rows = session.execute(
        select(Domain.normalized_domain, ModelComparison)
        .join(ModelComparison, ModelComparison.domain_id == Domain.id)
        .where(Domain.normalized_domain.in_(list(by_name)), ModelComparison.model.in_(models))
        .order_by(ModelComparison.created_at.desc(), ModelComparison.id.desc()))
    answers: dict[tuple[str, str], Answer] = {}
    for name, mc in rows:
        if (name, mc.model) in answers:
            continue
        answers[(name, mc.model)] = Answer(
            by_name[name], crawl="succeeded", validation=mc.validation_status, primary=mc.primary_category_id,
            secondary=list(mc.secondary_category_ids or []),
            confidence=float(mc.confidence) if mc.confidence is not None else None,
            latency_ms=mc.latency_ms, output_tokens=mc.output_tokens,
            errors=[str(e) for e in (mc.validation_errors or [])], model=mc.model, prompt_version=mc.prompt_version,
            scan_result_id=mc.scan_result_id, screenshot_available=mc.screenshot_available,
            used_screenshot=mc.used_screenshot)
    return answers


def _input_label(model: str, answers: list[Answer]) -> str:
    if takes_images(model) is False:
        return "text only (no image input)"
    with_shot = sum(a.used_screenshot for a in answers)
    if not answers or with_shot == len(answers):
        return "text + screenshot"
    if with_shot == 0:
        return "text only (no screenshot used)"
    return f"text + screenshot ({len(answers) - with_shot} without)"


def _cell(a: Answer | None) -> str:
    if a is None:
        return "–"
    if not a.answered:
        return f"_{a.validation or 'no answer'}_"
    mark = "✓" if a.strict else ("≈" if a.lenient else "✗")
    conf = f" {a.confidence:.2f}" if a.confidence is not None else ""
    return f"{a.primary}{conf} {mark}"


def render(sites: list[Site], models: list[str], answers: dict[tuple[str, str], Answer], *, set_name: str,
           prompt_version: str, now: datetime | None = None) -> str:
    now = now or datetime.now(UTC)
    per_model = {m: [answers[(s.domain, m)] for s in sites if (s.domain, m) in answers] for m in models}
    answered_by_all = [s for s in sites if all((s.domain, m) in answers and answers[(s.domain, m)].answered
                                               for m in models)]
    out = [f"# Model comparison ({set_name})", "",
           f"Generated {now:%Y-%m-%d %H:%M} UTC. {len(sites)} sites in the set; prompt version in the code: "
           f"`{prompt_version}`. Every model saw the same stored scan of a site with the production prompt, and every "
           "answer was validated as in production. Nothing here was published or sent to review.", "",
           "Accuracy counts valid answers only. *Strict*: the primary category is the best one in the set; "
           "*lenient*: it is one of the accepted ones. *Common sites*: sites every model answered validly, the "
           f"fairest like-for-like figure ({len(answered_by_all)} sites).", ""]

    out += ["## Models", "",
            "| model | input | answers | valid answers | strict | lenient | strict, common sites | lenient, common sites "
            "| wrong at >= 0.90 | latency median / p95 | output tokens median / total | prompt version |",
            "|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for m in models:
        rows = per_model[m]
        valid = [a for a in rows if a.answered]
        common = [answers[(s.domain, m)] for s in answered_by_all]
        lat = [a.latency_ms for a in rows if a.latency_ms is not None]
        toks = [a.output_tokens for a in rows if a.output_tokens is not None]
        latency = f"{statistics.median(lat) / 1000:.0f} s / {p95(lat) / 1000:.0f} s" if lat else "-"
        tokens = f"{statistics.median(toks):.0f} / {sum(toks)}" if toks else "-"
        versions = ", ".join(sorted({a.prompt_version for a in rows if a.prompt_version})) or "-"
        out.append(f"| {m} | {_input_label(m, rows)} | {len(rows)} | {_pct(len(valid), len(rows))} "
                   f"| {_pct(sum(a.strict for a in valid), len(valid))} | {_pct(sum(a.lenient for a in valid), len(valid))} "
                   f"| {_pct(sum(a.strict for a in common), len(common))} "
                   f"| {_pct(sum(a.lenient for a in common), len(common))} | {sum(a.confident_wrong for a in valid)} "
                   f"| {latency} | {tokens} | {versions} |")

    out += ["", "## Accuracy per stated confidence (lenient correct / answers in the band)", "",
            "| model | " + " | ".join(label for _, _, label in BANDS) + " |", "|---" * (len(BANDS) + 1) + "|"]
    for m in models:
        valid = [a for a in per_model[m] if a.answered and a.confidence is not None]
        cells = []
        for low, high, _ in BANDS:
            band = [a for a in valid if low <= a.confidence < high]
            cells.append(_pct(sum(a.lenient for a in band), len(band)))
        out.append(f"| {m} | " + " | ".join(cells) + " |")

    out += ["", "## Agreement between models (same primary category, sites both answered validly)", "",
            "| model A | model B | both answered | same primary |", "|---|---|---|---|"]
    for a, b in combinations(models, 2):
        both = [s for s in sites if (s.domain, a) in answers and (s.domain, b) in answers
                and answers[(s.domain, a)].answered and answers[(s.domain, b)].answered]
        same = sum(answers[(s.domain, a)].primary == answers[(s.domain, b)].primary for s in both)
        out.append(f"| {a} | {b} | {len(both)} | {_pct(same, len(both))} |")

    wrong = [a for m in models for a in per_model[m] if a.confident_wrong]
    if wrong:
        out += ["", "## Wrong answers at >= 0.90", "", "| model | domain | answer | accepted |", "|---|---|---|---|"]
        out += [f"| {a.model} | {a.site.domain} | {a.primary} {a.confidence:.2f} | {', '.join(a.site.accept)} |"
                for a in wrong]

    out += ["", "## Sites", "", "✓ best answer · ≈ acceptable · ✗ wrong · _status_ no valid answer · – not asked", "",
            "| domain | accepted (best first) | " + " | ".join(models) + " |", "|---|---" + "|---" * len(models) + "|"]
    for s in sites:
        cells = [_cell(answers.get((s.domain, m))) for m in models]
        out.append(f"| {s.domain} | {', '.join(s.accept)} | " + " | ".join(cells) + " |")

    scans = {}
    for (domain, _), a in answers.items():
        scans.setdefault(domain, set()).add(a.scan_result_id)
    mixed = sorted(d for d, ids in scans.items() if len(ids) > 1)
    errors = [a for m in models for a in per_model[m] if not a.answered and a.errors]
    if mixed or errors:
        out += ["", "## Notes", ""]
    if mixed:
        out.append(f"- Models saw different scans (the site was re-scanned between runs): {', '.join(mixed)}")
    for a in errors:
        out.append(f"- {a.model} · {a.site.domain}: {a.validation}: " + "; ".join(a.errors)[:160].replace("|", "/"))
    return "\n".join(out) + "\n"


# ----------------------------------------------------------------------------- wiring (replaced in tests)


def make_provider(settings, model: str) -> AIProvider:
    from ai_providers.registry import evren_provider

    return evren_provider(settings, model)


def make_store(settings):
    from crawler.storage import S3Store

    return S3Store(settings)
