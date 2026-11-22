"""Measure EVREN's decision model ``clef`` on pages we already know: phishing, and licensed vs unlicensed.

    python -m threats.clef_eval                      # dry run: the pages per group and the calls a run makes
    python -m threats.clef_eval --sample 3 --yes     # three calls, raw answers printed: check the answer format first
    python -m threats.clef_eval --yes > clef-$(date +%F).md

The guard model (``threats/guard_eval.py``) reacts to a page's subject, so it cannot tell a copied bank login from
the real one or a licensed casino from an unlicensed one. ``clef`` (``POST /v1/systemone``) answers typed questions
about one "state" in a single pass; this asks four yes/no (``noul``) questions per page in one call:

* ``brand_match``: does the brand or institution on the page match the domain visited?
* ``phishing``: does it imitate a brand or institution to collect passwords, cards or identity data?
* ``licence_info``: does the page show licence, regulator or company details?
* ``unlicensed``: does it offer unlicensed betting/casino, or films, series, music or software without permission?

Groups: our confirmed phishing and scams, person-checked banking/finance/government and other legitimate sites (as in
the guard measurement), and published gambling / illegal-gambling / streaming-media / piracy sites. Each call
carries the domain first in the state, the page title and text, and the stored screenshot (1280x800 viewport,
about 1,000 image tokens; the gateway counts 32x32 px per token). Advice from the EVREN forum (2026-11-22):
the answer's confidence is the chosen option's value in ``probabilities``; past 16,384 input tokens the input is
cut silently, which shows as ``usage.input_tokens == 16384`` (counted as "cut" here); there is no batch endpoint.

It stores nothing and changes nothing; the report is printed. Run it in the worker (the key lives there).
"""
from __future__ import annotations

import argparse
import base64
import json
import sys
import time
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from common.config import get_settings
from database.models import Domain, ScanResult, Screenshot

from .guard_eval import Page, _newest_text_scans, pages, pages_by_category

MODEL = "clef"
STATE_CHARS = 6000          # page text in the state; with the image this stays far below 16,384 tokens
INPUT_LIMIT = 16384         # past this the gateway cuts the input without an error
SURE = 0.90                 # the confidence Cat-Egory would act on
PHISHING_GROUPS = ("threat", "legit_finance_gov", "legit_other")
LICENCE_GROUPS = ("gambling", "illegal-gambling", "streaming-media", "piracy")
QUESTIONS = {
    "brand_match": "Sayfada görünen marka, şirket ya da kurum kimliği, ziyaret edilen alan adıyla örtüşüyor mu? "
                   "(Örneğin bir bankanın giriş sayfası o bankanın kendi alan adında mı?)",
    "phishing": "Bu sayfa, bir markayı ya da kurumu taklit ederek ziyaretçiden şifre, kart, kimlik ya da telefon "
                "bilgisi istemeye çalışan bir oltalama veya dolandırıcılık sayfası mı?",
    "licence_info": "Sayfada lisans numarası, düzenleyici kurum, şirket unvanı ya da künye gibi yasal bilgi var mı?",
    "unlicensed": "Bu site lisanssız (kaçak) bahis veya casino hizmeti ya da izinsiz (korsan) film, dizi, müzik "
                  "veya yazılım sunuyor mu?",
}
YES = {"yes", "evet", "true", "1", "y", "e"}
NO = {"no", "hayır", "hayir", "false", "0", "n", "h"}

Ask = Callable[[dict], dict]


@dataclass
class Item:
    page: Page
    final_url: str | None = None
    screenshot: bytes | None = None
    mime: str = "image/png"


@dataclass
class Verdict:
    """One question's answer: yes / no / other (an abstention or an unknown label) and the chosen option's value."""
    value: str
    confidence: float | None = None

    @property
    def sure(self) -> bool:
        return self.confidence is not None and self.confidence >= SURE


@dataclass
class Result:
    item: Item
    verdicts: dict[str, Verdict] = field(default_factory=dict)
    input_tokens: int | None = None
    error: str | None = None

    @property
    def cut(self) -> bool:
        return self.input_tokens is not None and self.input_tokens >= INPUT_LIMIT


# ----------------------------------------------------------------------------- pages


def load_items(session: Session, store, *, per_group: int, licence_per_group: int) -> list[Item]:
    """The guard measurement's phishing groups and the licence categories, each with its newest screenshot."""
    chosen = [p for p in pages(session, per_group) if p.group in PHISHING_GROUPS]
    chosen += pages_by_category(session, licence_per_group, LICENCE_GROUPS)
    newest = _newest_text_scans()
    rows = session.execute(
        select(Domain.normalized_domain, ScanResult.final_url, Screenshot.object_key, Screenshot.mime_type)
        .join(newest, newest.c.domain_id == Domain.id)
        .join(ScanResult, ScanResult.id == newest.c.scan_id)
        .outerjoin(Screenshot, Screenshot.scan_result_id == ScanResult.id)
        .where(Domain.normalized_domain.in_([p.domain for p in chosen]))).all()
    by_name = {name: (url, key, mime) for name, url, key, mime in rows}
    items = []
    for page in chosen:
        url, key, mime = by_name.get(page.domain, (None, None, None))
        shot = None
        if key and store is not None:
            try:
                shot = store.get(key)
            except Exception:  # noqa: BLE001 - the text alone still goes
                shot = None
        items.append(Item(page, url, shot, mime or "image/png"))
    return items


# ----------------------------------------------------------------------------- one call


def request(item: Item) -> dict:
    p = item.page
    state = (f"Ziyaret edilen alan adı: {p.domain}\n"
             + (f"Son adres: {item.final_url}\n" if item.final_url else "")
             + f"\nSayfa başlığı ve metni:\n{p.text[:STATE_CHARS]}")
    body = {"model": MODEL, "state": state,
            "questions": {qid: {"type": "noul", "instructions": text} for qid, text in QUESTIONS.items()}}
    if item.screenshot:
        body["images"] = [f"data:{item.mime};base64,{base64.b64encode(item.screenshot).decode()}"]
    return body


def verdict(answer: dict) -> Verdict:
    """Read one answer defensively: the label from ``answer``/``choice``/``value``/``label`` (else the likeliest
    option), the confidence from ``probabilities`` (else ``confidence``/``score``)."""
    if not isinstance(answer, dict):
        return Verdict("other")
    probs = answer.get("probabilities")
    probs = probs if isinstance(probs, dict) else {}
    label = next((answer[k] for k in ("answer", "choice", "value", "label") if answer.get(k) is not None), None)
    if label is None and probs:
        label = max(probs, key=lambda k: float(probs[k]))
    conf = None
    if label is not None and str(label) in probs:
        conf = float(probs[str(label)])
    elif isinstance(answer.get("confidence"), int | float):
        conf = float(answer["confidence"])
    elif isinstance(answer.get("score"), int | float):
        conf = float(answer["score"])
    text = str(label).strip().lower() if label is not None else ""
    return Verdict("yes" if text in YES else "no" if text in NO else "other", conf)


def http_ask(http: httpx.Client, base_url: str, key: str) -> Ask:
    def ask(body: dict) -> dict:
        r = http.post(f"{base_url.rstrip('/')}/v1/systemone", json=body,
                      headers={"Authorization": f"Bearer {key}", "Accept": "application/json"})
        r.raise_for_status()
        return r.json()
    return ask


def run(items: list[Item], ask: Ask, *, pause: float = 1.0, sleep: Callable[[float], None] = time.sleep,
        raw: Callable[[str], None] | None = None) -> tuple[list[Result], str | None]:
    """One call per page. A 429 waits a minute and tries again (three times); any other error, or a fourth 429,
    stops the run and keeps what was answered."""
    results: list[Result] = []
    for i, item in enumerate(items):
        if i:
            sleep(pause)
        for attempt in range(4):
            try:
                data = ask(request(item))
                break
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code == 429 and attempt < 3:
                    sleep(60)
                    continue
                return results, f"stopped after {i} pages: HTTP {exc.response.status_code}"
            except (httpx.HTTPError, ValueError) as exc:
                return results, f"stopped after {i} pages: {type(exc).__name__}"
        if not isinstance(data, dict):
            return results, f"stopped after {i} pages: unexpected answer"
        if raw is not None:
            raw(json.dumps({"domain": item.page.domain, "answers": data.get("answers"), "usage": data.get("usage")},
                           ensure_ascii=False))
        answers = data.get("answers") or {}
        usage = data.get("usage") or {}
        tokens = usage.get("input_tokens", usage.get("prompt_tokens"))
        results.append(Result(item, {q: verdict(answers.get(q)) for q in QUESTIONS},
                              int(tokens) if isinstance(tokens, int | float) else None))
    return results, None


# ----------------------------------------------------------------------------- report


def _pct(k: int, n: int) -> str:
    return f"{k}/{n} ({round(100 * k / n)} %)" if n else "-"


def tally(results: list[Result]) -> dict[str, dict[str, Counter]]:
    """group -> question -> counts of yes / no / other and of yes and no at >= SURE."""
    out: dict[str, dict[str, Counter]] = {}
    for r in results:
        for q, v in r.verdicts.items():
            c = out.setdefault(r.item.page.group, {}).setdefault(q, Counter())
            c[v.value] += 1
            c[f"{v.value}_sure"] += v.sure
            c["n"] += 1
    return out


def report(results: list[Result], error: str | None) -> str:
    t = tally(results)
    groups = [g for g in (*PHISHING_GROUPS, *LICENCE_GROUPS) if g in t]
    cut = sum(r.cut for r in results)
    lines = [f"# clef on known pages ({MODEL})", "",
             f"{len(results)} pages, one call each with four yes/no questions; the domain first in the state, the "
             f"screenshot as an image. Sure: the chosen answer's probability is at least {SURE:.2f}. "
             f"Input cut at {INPUT_LIMIT} tokens: {cut}.", ""]
    if error:
        lines += [f"**Incomplete:** {error}.", ""]
    lines += ["## Answers by group", "", "| group | pages | question | yes | no | other | yes, sure | no, sure |",
              "|---|---|---|---|---|---|---|---|"]
    for g in groups:
        for q in QUESTIONS:
            c = t[g].get(q, Counter())
            n = c["n"]
            lines.append(f"| {g} | {n} | {q} | {_pct(c['yes'], n)} | {_pct(c['no'], n)} | {_pct(c['other'], n)} "
                         f"| {_pct(c['yes_sure'], n)} | {_pct(c['no_sure'], n)} |")

    def share(group: str, q: str, value: str) -> tuple[int, int]:
        c = t.get(group, {}).get(q, Counter())
        return c[value], c["n"]

    def merged(groups_: tuple[str, ...], q: str, value: str) -> tuple[int, int]:
        pairs = [share(g, q, value) for g in groups_]
        return sum(k for k, _ in pairs), sum(n for _, n in pairs)

    legit = ("legit_finance_gov", "legit_other")
    lines += ["", "## Does it tell them apart?", "",
              "| signal | caught (should be yes) | false alarm (should be no) |", "|---|---|---|"]
    for label, q, value, bad, good in (
            ("phishing = yes", "phishing", "yes", ("threat",), legit),
            ("phishing = yes, sure", "phishing", "yes_sure", ("threat",), legit),
            ("brand_match = no", "brand_match", "no", ("threat",), legit),
            ("brand_match = no, sure", "brand_match", "no_sure", ("threat",), legit),
            ("unlicensed = yes (betting)", "unlicensed", "yes", ("illegal-gambling",), ("gambling",)),
            ("unlicensed = yes, sure (betting)", "unlicensed", "yes_sure", ("illegal-gambling",), ("gambling",)),
            ("unlicensed = yes (films, music)", "unlicensed", "yes", ("piracy",), ("streaming-media",)),
            ("unlicensed = yes, sure (films, music)", "unlicensed", "yes_sure", ("piracy",), ("streaming-media",)),
            ("licence_info = no (betting)", "licence_info", "no", ("illegal-gambling",), ("gambling",))):
        lines.append(f"| {label} | {_pct(*merged(bad, q, value))} | {_pct(*merged(good, q, value))} |")
    lines += ["", "## Pages", "", "| group | domain | " + " | ".join(QUESTIONS) + " | input tokens |",
              "|---|---|" + "---|" * len(QUESTIONS) + "---|"]
    for r in results:
        cells = [f"{v.value} {v.confidence:.2f}" if v.confidence is not None else v.value
                 for v in (r.verdicts[q] for q in QUESTIONS)]
        lines.append(f"| {r.item.page.group} | {r.item.page.domain} | {' | '.join(cells)} | "
                     f"{r.input_tokens if r.input_tokens is not None else '-'}{' (cut)' if r.cut else ''} |")
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    from database.session import session_factory

    parser = argparse.ArgumentParser(prog="python -m threats.clef_eval", description=__doc__.split("\n\n")[0])
    parser.add_argument("--per-group", type=int, default=100, help="pages per phishing group (default 100)")
    parser.add_argument("--licence-per-group", type=int, default=50, help="pages per licence category (default 50)")
    parser.add_argument("--sample", type=int, help="ask only the first N pages (spread over the groups)")
    parser.add_argument("--yes", action="store_true", help="call clef (without it: a dry run)")
    parser.add_argument("--raw", action="store_true", help="print each raw answer to stderr (always on with --sample)")
    parser.add_argument("--pause", type=float, default=1.0, help="seconds between calls (default 1)")
    parser.add_argument("--no-images", action="store_true", help="send the text only")
    args = parser.parse_args(argv)
    s = get_settings()
    store = None
    if not args.no_images:
        from crawler.storage import S3Store
        store = S3Store(s)
    with session_factory()() as session:
        items = load_items(session, store if args.yes else None, per_group=max(1, args.per_group),
                           licence_per_group=max(1, args.licence_per_group))
    if args.sample:
        by_group: dict[str, list[Item]] = {}
        for it in items:
            by_group.setdefault(it.page.group, []).append(it)
        spread = [it for i in range(args.sample) for g in by_group.values() if i < len(g) for it in [g[i]]]
        items = spread[:args.sample]
    counts = Counter(it.page.group for it in items)
    print(" · ".join(f"{g}: {counts[g]}" for g in (*PHISHING_GROUPS, *LICENCE_GROUPS)), file=sys.stderr)
    if not args.yes:
        print(f"dry run: {len(items)} calls to {MODEL} (add --yes)", file=sys.stderr)
        return 0
    key = s.evren_api_key.get_secret_value().strip() if s.evren_api_key else ""
    if not key:
        print("CATEGORY_EVREN_API_KEY is not set here (run this in the worker container)", file=sys.stderr)
        return 1
    raw = (lambda line: print(line, file=sys.stderr, flush=True)) if args.raw or args.sample else None
    with httpx.Client(timeout=httpx.Timeout(120.0, connect=15.0), follow_redirects=False) as http:
        results, error = run(items, http_ask(http, s.evren_base_url, key), pause=args.pause, raw=raw)
    print(report(results, error), end="")
    return 2 if error else 0


if __name__ == "__main__":
    sys.exit(main())
