"""How EVREN's content-safety model (``qwen3-guard-4b`` on ``/v1/moderations``) judges pages we already know.

A one-off measurement, nothing more: it reads stored page text, asks the guard model about it and prints a report.
No domain, category, threat flag or review changes, and nothing is stored. Run in the worker (it holds the key):

    python -m threats.guard_eval                    # dry run: how many pages each group has
    python -m threats.guard_eval --per-group 150 --yes > guard-eval.md
    python -m threats.guard_eval --by-category --yes > guard-categories.md   # licensed vs unlicensed content

Groups (each page is the newest stored scan of the domain that has text):

* ``threat``: an active threat of our own (reviewer, detector, Octryx) of type phishing, scam or fraud;
* ``legit_finance_gov``: checked by a person, main category banking, finance or government, no active threat;
* ``legit_other``: checked by a person, any other category except adult content, no active threat;
* ``injection``: a page whose classification found text aimed at the model;
* ``adult``: main category adult content.

The report gives, per group, EVREN's verdict (``evren.safety``: Safe / Controversial / Unsafe) and its categories
(``evren.guard_categories``; the OpenAI-style ``flagged`` stays false for "Controversial", so it is not used), and
how well two simple rules would tell our threats from checked legitimate sites.

``--by-category`` groups published sites by main category instead (default: licensed and illegal gambling,
streaming media and piracy, and a few ordinary categories as a baseline) and shows whether "Non-violent Illegal
Acts" tells the unlicensed side of each pair from the licensed one. The labels are the published ones (mostly the
AI's), so a few may be wrong; the report counts the person-checked ones apart.
"""
from __future__ import annotations

import argparse
import sys
import time
from collections import Counter
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field

import httpx
from sqlalchemy import and_, exists, func, select
from sqlalchemy.orm import Session

from classification.engine import OWN_THREAT_SOURCES
from common.config import get_settings
from database.models import (
    AIPrediction,
    ClassificationStatus,
    DataSource,
    Domain,
    Evidence,
    EvidenceType,
    ExtractedContent,
    ScanResult,
    ThreatIntelligence,
    ThreatType,
)

from .records import domain_scoped

MODEL = "qwen3-guard-4b"
BATCH = 32            # texts per request (the gateway's limit)
MAX_CHARS = 3500      # per text: 32 x 3,500 stays under the 128,000-character request limit
THREATS = (ThreatType.PHISHING, ThreatType.SCAM, ThreatType.FRAUD)
FINANCE_GOV = ("banking", "finance", "government")
GROUPS = ("threat", "legit_finance_gov", "legit_other", "injection", "adult")
LEGIT = ("legit_finance_gov", "legit_other")
ILLEGAL = "Non-violent Illegal Acts"
CATEGORY_GROUPS = ("gambling", "illegal-gambling", "streaming-media", "piracy", "video", "file-sharing",
                   "software-downloads", "news", "education", "e-commerce")
PAIRS = (("gambling", "illegal-gambling"), ("streaming-media", "piracy"), ("software-downloads", "piracy"))
PUBLISHED = (ClassificationStatus.CLASSIFIED, ClassificationStatus.VERIFIED)

Ask = Callable[[list[str]], list[dict]]


@dataclass
class Page:
    group: str
    domain: str
    text: str
    checked: bool = False  # a person verified its category


@dataclass
class Tally:
    n: int = 0
    checked: int = 0
    checked_illegal: int = 0
    safety: Counter = field(default_factory=Counter)
    categories: Counter = field(default_factory=Counter)
    not_safe: int = 0
    illegal: int = 0
    jailbreak: int = 0
    sexual: int = 0


def _newest_text_scans():
    """Domain id -> its newest scan that has extracted text."""
    newest = (select(ScanResult.domain_id, func.max(ScanResult.id).label("scan_id"))
              .join(ExtractedContent, ExtractedContent.scan_result_id == ScanResult.id)
              .where(ExtractedContent.text_excerpt.is_not(None))
              .group_by(ScanResult.domain_id).subquery())
    return newest


def _active_own_threat(types: Iterable[ThreatType] | None = None):
    cond = [ThreatIntelligence.domain_id == Domain.id, ThreatIntelligence.is_active, domain_scoped(),
            DataSource.slug.in_(OWN_THREAT_SOURCES)]
    if types is not None:
        cond.append(ThreatIntelligence.threat_type.in_(list(types)))
    return exists(select(ThreatIntelligence.id).join(DataSource, DataSource.id == ThreatIntelligence.source_id)
                  .where(and_(*cond)))


def pages(session: Session, per_group: int) -> list[Page]:
    newest = _newest_text_scans()
    text = (select(Domain.normalized_domain, ScanResult.page_title, ExtractedContent.text_excerpt)
            .join(newest, newest.c.domain_id == Domain.id)
            .join(ScanResult, ScanResult.id == newest.c.scan_id)
            .join(ExtractedContent, ExtractedContent.scan_result_id == ScanResult.id))
    verified = Domain.classification_status == ClassificationStatus.VERIFIED
    # our injection check leaves a system note on the prediction (classification/engine.py)
    injected = exists(select(Evidence.id).join(AIPrediction, AIPrediction.id == Evidence.ai_prediction_id)
                      .where(AIPrediction.scan_result_id == newest.c.scan_id, Evidence.type == EvidenceType.SYSTEM))
    where = {
        "threat": [_active_own_threat(THREATS)],
        "legit_finance_gov": [verified, Domain.primary_category_id.in_(FINANCE_GOV), ~_active_own_threat()],
        "legit_other": [verified, Domain.primary_category_id.not_in((*FINANCE_GOV, "adult-content")),
                        ~_active_own_threat()],
        "injection": [injected],
        "adult": [Domain.primary_category_id == "adult-content"],
    }
    out: list[Page] = []
    for group in GROUPS:
        # a fixed, name-based order: the same sample on every run, spread over the alphabet
        rows = session.execute(text.where(*where[group]).order_by(func.md5(Domain.normalized_domain))
                               .limit(per_group)).all()
        out += [Page(group, name, (f"{title or ''}\n{excerpt or ''}").strip()[:MAX_CHARS]) for name, title, excerpt in rows]
    return out


def pages_by_category(session: Session, per_group: int, categories: Iterable[str] = CATEGORY_GROUPS) -> list[Page]:
    """Published sites by main category, the same fixed sample on every run; person-checked ones first."""
    newest = _newest_text_scans()
    verified = Domain.classification_status == ClassificationStatus.VERIFIED
    text = (select(Domain.normalized_domain, ScanResult.page_title, ExtractedContent.text_excerpt, verified)
            .join(newest, newest.c.domain_id == Domain.id)
            .join(ScanResult, ScanResult.id == newest.c.scan_id)
            .join(ExtractedContent, ExtractedContent.scan_result_id == ScanResult.id)
            .where(Domain.is_active, Domain.classification_status.in_(PUBLISHED)))
    out: list[Page] = []
    for category in categories:
        rows = session.execute(text.where(Domain.primary_category_id == category)
                               .order_by(verified.desc(), func.md5(Domain.normalized_domain)).limit(per_group)).all()
        out += [Page(category, name, (f"{title or ''}\n{excerpt or ''}").strip()[:MAX_CHARS], bool(checked))
                for name, title, excerpt, checked in rows]
    return out


def http_ask(http: httpx.Client, base_url: str, key: str) -> Ask:
    def ask(texts: list[str]) -> list[dict]:
        r = http.post(f"{base_url.rstrip('/')}/v1/moderations", json={"model": MODEL, "input": texts},
                      headers={"Authorization": f"Bearer {key}", "Accept": "application/json"})
        r.raise_for_status()
        results = r.json().get("results") or []
        if len(results) != len(texts):
            raise ValueError(f"asked about {len(texts)} texts, got {len(results)} answers")
        return results
    return ask


def judge(items: list[Page], ask: Ask, *, pause: float = 0.5,
          sleep: Callable[[float], None] = time.sleep) -> tuple[dict[str, Tally], str | None]:
    """Ask in batches; stop at the first error and report what was answered (the error is returned)."""
    tallies = {g: Tally() for g in dict.fromkeys([*GROUPS, *(p.group for p in items)])}
    for start in range(0, len(items), BATCH):
        if start:
            sleep(pause)
        batch = items[start:start + BATCH]
        try:
            answers = ask([p.text for p in batch])
        except (httpx.HTTPError, ValueError) as exc:
            status = exc.response.status_code if isinstance(exc, httpx.HTTPStatusError) else None
            return tallies, f"stopped after {start} pages: {type(exc).__name__}" + (f" (HTTP {status})" if status else "")
        for page, answer in zip(batch, answers, strict=True):
            evren = answer.get("evren") or {}
            safety = str(evren.get("safety") or "unknown")
            cats = [str(c) for c in evren.get("guard_categories") or []]
            t = tallies[page.group]
            t.n += 1
            t.checked += page.checked
            t.checked_illegal += page.checked and ILLEGAL in cats
            t.safety[safety] += 1
            t.categories.update(cats)
            t.not_safe += safety != "Safe"
            t.illegal += ILLEGAL in cats
            t.jailbreak += any("jailbreak" in c.lower() for c in cats)
            t.sexual += any("sexual" in c.lower() for c in cats)
    return tallies, None


def _pct(part: int, whole: int) -> str:
    return f"{part}/{whole} ({100 * part / whole:.0f} %)" if whole else "–"


def report(tallies: dict[str, Tally], error: str | None) -> str:
    lines = [f"# Guard model on known pages ({MODEL})", ""]
    if error:
        lines += [f"**Incomplete:** {error}.", ""]
    lines += ["| group | pages | Safe | Controversial | Unsafe | most frequent categories |", "|---|---|---|---|---|---|"]
    for g in GROUPS:
        t = tallies[g]
        top = ", ".join(f"{c} {n}" for c, n in t.categories.most_common(4)) or "–"
        lines.append(f"| {g} | {t.n} | {t.safety['Safe']} | {t.safety['Controversial']} | {t.safety['Unsafe']} | {top} |")
    threat, inj = tallies["threat"], tallies["injection"]
    legit_n = sum(tallies[g].n for g in LEGIT)
    lines += ["", "## Telling our threats from checked legitimate sites", "",
              "| rule | catches threats | false alarms on legitimate sites | on banking, finance, government |",
              "|---|---|---|---|",
              f"| not Safe | {_pct(threat.not_safe, threat.n)} | {_pct(sum(tallies[g].not_safe for g in LEGIT), legit_n)} "
              f"| {_pct(tallies['legit_finance_gov'].not_safe, tallies['legit_finance_gov'].n)} |",
              f"| \"{ILLEGAL}\" | {_pct(threat.illegal, threat.n)} | {_pct(sum(tallies[g].illegal for g in LEGIT), legit_n)} "
              f"| {_pct(tallies['legit_finance_gov'].illegal, tallies['legit_finance_gov'].n)} |",
              "", "## Other signals", "",
              f"* Pages with text aimed at the model, called Jailbreak: {_pct(inj.jailbreak, inj.n)}; "
              f"not Safe: {_pct(inj.not_safe, inj.n)}.",
              f"* Jailbreak on checked legitimate sites: {_pct(sum(tallies[g].jailbreak for g in LEGIT), legit_n)}.",
              f"* Adult-content pages with a sexual category: {_pct(tallies['adult'].sexual, tallies['adult'].n)}; "
              f"on checked legitimate sites: {_pct(sum(tallies[g].sexual for g in LEGIT), legit_n)}.",
              ""]
    return "\n".join(lines)


def report_by_category(tallies: dict[str, Tally], error: str | None) -> str:
    lines = [f"# Guard model by main category ({MODEL})", ""]
    if error:
        lines += [f"**Incomplete:** {error}.", ""]
    lines += [f"| category | pages (person-checked) | Safe | Controversial | Unsafe | \"{ILLEGAL}\" | "
              "on person-checked | most frequent categories |", "|---|---|---|---|---|---|---|---|"]
    for g, t in tallies.items():
        if not t.n:
            continue
        top = ", ".join(f"{c} {n}" for c, n in t.categories.most_common(3)) or "–"
        lines.append(f"| {g} | {t.n} ({t.checked}) | {t.safety['Safe']} | {t.safety['Controversial']} | "
                     f"{t.safety['Unsafe']} | {_pct(t.illegal, t.n)} | {_pct(t.checked_illegal, t.checked)} | {top} |")
    lines += ["", f"## Does \"{ILLEGAL}\" tell the unlicensed side apart?", "",
              "| licensed | unlicensed | caught on the unlicensed side | false alarms on the licensed side |",
              "|---|---|---|---|"]
    for legal, illegal in PAIRS:
        a, b = tallies.get(legal), tallies.get(illegal)
        if a and b and a.n and b.n:
            lines.append(f"| {legal} | {illegal} | {_pct(b.illegal, b.n)} | {_pct(a.illegal, a.n)} |")
    return "\n".join(lines + [""])


def main(argv: list[str] | None = None) -> int:
    from database.session import session_factory

    parser = argparse.ArgumentParser(prog="python -m threats.guard_eval", description=__doc__.split("\n\n")[0])
    parser.add_argument("--per-group", type=int, default=150, help="pages per group at most (default 150)")
    parser.add_argument("--yes", action="store_true", help="ask the guard model (without it: a dry run)")
    parser.add_argument("--pause", type=float, default=0.5, help="seconds between requests (default 0.5)")
    parser.add_argument("--by-category", action="store_true", help="group published sites by main category")
    parser.add_argument("--categories", help=f"comma-separated, with --by-category (default: {','.join(CATEGORY_GROUPS)})")
    args = parser.parse_args(argv)
    categories = [c.strip() for c in (args.categories or "").split(",") if c.strip()] or list(CATEGORY_GROUPS)
    with session_factory()() as session:
        items = (pages_by_category(session, max(1, args.per_group), categories) if args.by_category
                 else pages(session, max(1, args.per_group)))
    counts = Counter(p.group for p in items)
    print(" · ".join(f"{g}: {counts[g]}" for g in (categories if args.by_category else GROUPS)), file=sys.stderr)
    if not args.yes:
        print(f"dry run: {len(items)} pages, {-(-len(items) // BATCH)} requests (add --yes to ask {MODEL})",
              file=sys.stderr)
        return 0
    s = get_settings()
    key = s.evren_api_key.get_secret_value().strip() if s.evren_api_key else ""
    if not key:
        print("CATEGORY_EVREN_API_KEY is not set here (run this in the worker container)", file=sys.stderr)
        return 1
    with httpx.Client(timeout=httpx.Timeout(120.0, connect=15.0), follow_redirects=False) as http:
        tallies, error = judge(items, http_ask(http, s.evren_base_url, key), pause=args.pause)
    print((report_by_category if args.by_category else report)(tallies, error))
    return 2 if error else 0


if __name__ == "__main__":
    sys.exit(main())
