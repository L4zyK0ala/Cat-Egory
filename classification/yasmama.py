"""Yaş Mama: Cat-egory's own category classifier, which learns from the labels Cat-egory already has.

The AI provider may stop being free. This model is trained on the domains' current main categories: people's
decisions (weighted higher) and, unless ``local_train_on_ai_labels`` is off, confident AI answers. Small categories
weigh more than their page count (``local_class_balance``), or news and government would drown out tobacco and
VPNs. It runs on the CPU in the worker and costs nothing per page.

For now it only runs in **shadow mode** (``CATEGORY_LOCAL_SHADOW=true``): after every AI answer its guess is stored
in ``local_predictions`` and compared, by ``report``, with the AI and with people. It never publishes a category,
never opens a review and never says anything about threats. Whether it may answer on its own one day is decided
from those numbers, per category and confidence threshold.

    python -m classification.yasmama train     # snapshot the labels, train, store the model (runs in the worker)
    python -m classification.yasmama report    # held-out scores and live shadow agreement (Markdown)

Model: hashed word 1-2-grams of the title, description and visible text plus hashed character 3-5-grams of the
host, title and description (Turkish and English mixed, no vocabulary to store), sublinear TF-IDF, multinomial
logistic regression (saga: small memory). Weights are a NumPy archive in the object store, loaded without pickle.
A fixed fifth of the domains (by a hash of their id) is never trained on; their scores are the honest ones.
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import logging
import resource
import sys
import time
import warnings
from collections import Counter
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta, timezone
from urllib.parse import urlsplit

import numpy as np
from scipy import sparse
from sqlalchemy import delete, exists, func, select, true, update
from sqlalchemy.orm import Session

from common.config import Settings
from common.timeutil import local_midnight
from database.models import (
    AIPrediction,
    AssignmentOrigin,
    Domain,
    DomainCategory,
    ExtractedContent,
    LocalModel,
    LocalPrediction,
    ScanResult,
    ThreatIntelligence,
    TrainingExample,
    ValidationStatus,
)

log = logging.getLogger("category.yasmama")

HASH_FEATURES = 2 ** 17          # words and characters together
THRESHOLDS = (0.5, 0.7, 0.8, 0.9, 0.95)
TRUSTED = ("human", "community")  # people's decisions
WEIGHT = {"human": 3.0, "community": 3.0, "ai": 1.0}
TOP = 3
CHUNK = 1000
BATCH = 2000  # pages hashed at a time
MIN_PER_CATEGORY_REPORT = 5


# --------------------------------------------------------------------------- features


def _clean(text: str | None) -> str:
    # str.lower() turns "İ" into "i" plus a combining dot, so "İstanbul" would not match "istanbul"
    return (text or "").replace("İ", "i").casefold()


@dataclass(frozen=True)
class Page:
    host: str
    title: str | None = None
    description: str | None = None
    text: str | None = None

    def words(self, chars: int) -> str:
        return " ".join(p for p in (self.title, self.description, (self.text or "")[:chars]) if p)

    def letters(self) -> str:
        return " ".join(p for p in (self.host, self.title, self.description) if p)

    @property
    def empty(self) -> bool:
        return not (self.title or self.description or self.text)


def _analyzer(chars: int):
    """Word 1-2-grams of the title, description and text plus character 3-5-grams of the host, title and description,
    as one token stream: one hashed matrix, no stacking (which would copy it)."""
    from sklearn.feature_extraction.text import HashingVectorizer

    word = HashingVectorizer(analyzer="word", ngram_range=(1, 2), token_pattern=r"(?u)\b\w\w+\b",  # noqa: S106 - a regex
                             lowercase=False).build_analyzer()
    char = HashingVectorizer(analyzer="char_wb", ngram_range=(3, 5), lowercase=False).build_analyzer()

    def tokens(page: Page) -> list[str]:
        return [*word(_clean(page.words(chars))), *("\x01" + g for g in char(_clean(page.letters())))]

    return tokens


def counts(pages: list[Page], chars: int) -> sparse.csr_matrix:
    from sklearn.feature_extraction.text import HashingVectorizer

    vectorizer = HashingVectorizer(analyzer=_analyzer(chars), n_features=HASH_FEATURES, alternate_sign=False,
                                   norm=None, dtype=np.float32)
    if len(pages) <= BATCH:
        return vectorizer.transform(pages)
    # Large sets are hashed twice in batches: once to size the matrix, once to fill it. Hashing everything at once,
    # or stacking batches, holds two to three copies of the matrix at the peak (worker memory).
    batches = range(0, len(pages), BATCH)
    nnz = [vectorizer.transform(pages[i:i + BATCH]).nnz for i in batches]
    data = np.empty(sum(nnz), dtype=np.float32)
    indices = np.empty(sum(nnz), dtype=np.int32)
    indptr = np.zeros(len(pages) + 1, dtype=np.int64)
    at = 0
    for i, n in zip(batches, nnz, strict=True):
        part = vectorizer.transform(pages[i:i + BATCH])
        data[at:at + n], indices[at:at + n] = part.data, part.indices
        indptr[i + 1:i + 1 + part.shape[0]] = part.indptr[1:] + at
        at += n
    return sparse.csr_matrix((data, indices, indptr), shape=(len(pages), HASH_FEATURES))


def weigh(x: sparse.csr_matrix, idf: np.ndarray, *, copy: bool = True) -> sparse.csr_matrix:
    from sklearn.preprocessing import normalize

    if copy:
        x = x.copy()
    x.data = 1.0 + np.log(x.data)  # sublinear term frequency
    x.data *= idf[x.indices]       # in place: no second matrix
    return normalize(x, copy=False)


@dataclass
class Weights:
    version: str
    classes: list[str]
    idf: np.ndarray        # (features,)
    coef: np.ndarray       # (classes, features)
    intercept: np.ndarray  # (classes,)
    text_chars: int

    def predict(self, pages: list[Page]) -> list[list[tuple[str, float]]]:
        scores = weigh(counts(pages, self.text_chars), self.idf) @ self.coef.T + self.intercept
        scores = np.asarray(scores, dtype=np.float64)
        scores -= scores.max(axis=1, keepdims=True)
        p = np.exp(scores)
        p /= p.sum(axis=1, keepdims=True)
        best = np.argsort(-p, axis=1)[:, :TOP]
        return [[(self.classes[j], float(p[i, j])) for j in row] for i, row in enumerate(best)]

    def to_bytes(self) -> bytes:
        buf = io.BytesIO()
        np.savez_compressed(buf, classes=np.array(self.classes), idf=self.idf.astype(np.float32),
                            coef=self.coef.astype(np.float16), intercept=self.intercept.astype(np.float32),
                            meta=np.array(json.dumps({"version": self.version, "text_chars": self.text_chars,
                                                      "features": int(self.idf.shape[0])})))
        return buf.getvalue()

    @classmethod
    def from_bytes(cls, data: bytes) -> Weights:
        with np.load(io.BytesIO(data), allow_pickle=False) as z:  # never unpickle what comes from storage
            meta = json.loads(str(z["meta"]))
            return cls(version=meta["version"], classes=[str(c) for c in z["classes"]], idf=z["idf"],
                       coef=z["coef"].astype(np.float32), intercept=z["intercept"], text_chars=meta["text_chars"])


def fit(pages: list[Page], labels: list[str], weights: list[float], *, version: str, text_chars: int) -> Weights:
    from sklearn.exceptions import ConvergenceWarning
    from sklearn.linear_model import LogisticRegression

    x = counts(pages, text_chars)
    df = np.bincount(x.indices, minlength=x.shape[1])
    idf = (np.log((1 + x.shape[0]) / (1 + df)) + 1).astype(np.float32)
    x = weigh(x, idf, copy=False)  # the raw counts are not needed any more
    model = LogisticRegression(solver="saga", C=10.0, max_iter=60, tol=1e-3)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", ConvergenceWarning)
        model.fit(x, labels, sample_weight=np.asarray(weights))
    coef, intercept = model.coef_, model.intercept_
    if len(model.classes_) == 2:  # binary: one row for the second class; as softmax rows [0, w]
        coef, intercept = np.vstack([np.zeros_like(coef), coef]), np.concatenate([[0.0], intercept])
    return Weights(version=version, classes=[str(c) for c in model.classes_], idf=idf,
                   coef=coef.astype(np.float32), intercept=intercept.astype(np.float32), text_chars=text_chars)


def class_weights(sizes: Counter[str], balance: float) -> dict[str, float]:
    """Per category: (pages per category on average / this category's pages) ** balance. With balance 0 every page
    counts 1; with 1 every category weighs the same in total; 0.5 halves the gap (a category 100 times larger
    weighs 10 times more, not 100). The average weight of a page stays about 1."""
    if not sizes or balance <= 0:
        return dict.fromkeys(sizes, 1.0)
    mean = sum(sizes.values()) / len(sizes)
    return {c: (mean / n) ** balance for c, n in sizes.items()}


def held_out(domain_id: int) -> bool:
    """A fixed fifth of all domains is never trained on (stable across retraining)."""
    return int(hashlib.sha256(f"local-test:{domain_id}".encode()).hexdigest()[:8], 16) % 5 == 0


def _host(url: str | None) -> str | None:
    return urlsplit(url).hostname if url else None


# --------------------------------------------------------------------------- training snapshot


def snapshot(session: Session, settings: Settings) -> dict[str, int]:
    """Bring ``training_examples`` up to date with the domains' current main categories and their newest page text.
    A label whose page text was purged keeps the text it was saved with."""
    origins = [AssignmentOrigin(o) for o in TRUSTED] + ([AssignmentOrigin.AI] if settings.local_train_on_ai_labels else [])
    threat = exists(select(ThreatIntelligence.id).where(ThreatIntelligence.domain_id == DomainCategory.domain_id,
                                                        ThreatIntelligence.is_active))
    rows = session.execute(
        select(DomainCategory.domain_id, DomainCategory.category_id, DomainCategory.origin, DomainCategory.confidence,
               Domain.normalized_domain)
        .join(Domain, Domain.id == DomainCategory.domain_id)
        .where(DomainCategory.is_primary, DomainCategory.valid_to.is_(None), Domain.is_active,
               DomainCategory.origin.in_(origins), ~threat)).all()
    labelled = {r.domain_id: r for r in rows
                if str(r.origin) in TRUSTED
                or (r.confidence is not None and float(r.confidence) >= settings.confidence_auto_approve)}
    stats = Counter(labelled=len(labelled))
    ids = list(labelled)
    for start in range(0, len(ids), CHUNK):
        chunk = ids[start:start + CHUNK]
        newest = (select(func.max(ScanResult.id))  # each domain's newest scan that still has its text
                  .join(ExtractedContent, ExtractedContent.scan_result_id == ScanResult.id)
                  .where(ScanResult.domain_id.in_(chunk), ExtractedContent.text_excerpt.is_not(None))
                  .group_by(ScanResult.domain_id))
        pages = {r.domain_id: r for r in session.execute(
            select(ScanResult.domain_id, ScanResult.id, ScanResult.final_url, ScanResult.page_title,
                   ScanResult.meta_description, ExtractedContent.text_excerpt)
            .join(ExtractedContent, ExtractedContent.scan_result_id == ScanResult.id)
            .where(ScanResult.id.in_(newest)))}
        saved = {e.domain_id: e for e in session.scalars(select(TrainingExample)
                                                         .where(TrainingExample.domain_id.in_(chunk)))}
        for domain_id in chunk:
            label, page, example = labelled[domain_id], pages.get(domain_id), saved.get(domain_id)
            if page is None and example is None:
                stats["without_text"] += 1
                continue
            if example is None:
                example = TrainingExample(domain_id=domain_id, host=label.normalized_domain)
                session.add(example)
            if page is not None:
                example.scan_result_id, example.host = page.id, _host(page.final_url) or label.normalized_domain
                example.title, example.description = page.page_title, page.meta_description
                example.text = (page.text_excerpt or "")[:settings.local_text_chars]
                stats["saved"] += 1
            else:
                stats["kept_old_text"] += 1
            example.label, example.origin = label.category_id, str(label.origin)
            example.confidence, example.updated_at = label.confidence, datetime.now(UTC)
    session.flush()
    gone = session.execute(delete(TrainingExample).where(TrainingExample.domain_id.not_in(ids) if ids else true()))
    stats["removed"] = gone.rowcount or 0
    return dict(stats)


def page_of(example: TrainingExample) -> Page:
    return Page(host=example.host, title=example.title, description=example.description, text=example.text)


# --------------------------------------------------------------------------- scores


def evaluate(model: Weights, examples: list[TrainingExample]) -> dict:
    """Scores on held-out pages: per group (people's labels, AI labels), per confidence threshold, per category."""
    if not examples:
        return {}
    guesses = model.predict([page_of(e) for e in examples])
    out: dict = {"groups": {}, "categories": {}}
    for group, keep in (("human", lambda e: e.origin in TRUSTED), ("ai", lambda e: e.origin == "ai")):
        pairs = [(e.label, g[0]) for e, g in zip(examples, guesses, strict=True) if keep(e)]
        if not pairs:
            continue
        stats = {"pages": len(pairs), "correct": sum(label == best for label, (best, _) in pairs), "thresholds": {}}
        for t in THRESHOLDS:
            answered = [(label, best) for label, (best, p) in pairs if p >= t]
            stats["thresholds"][str(t)] = {"answered": len(answered),
                                           "correct": sum(label == best for label, best in answered)}
        out["groups"][group] = stats
    actual = Counter(e.label for e in examples)
    predicted = Counter(g[0][0] for g in guesses)
    hits = Counter(e.label for e, g in zip(examples, guesses, strict=True) if e.label == g[0][0])
    for category, n in sorted(actual.items()):
        if n >= MIN_PER_CATEGORY_REPORT:
            out["categories"][category] = {"pages": n, "guessed": predicted[category], "correct": hits[category]}
    # Every category counts the same here, so a model that only gets the big ones right scores low.
    recalls = [c["correct"] / c["pages"] for c in out["categories"].values()]
    out["macro_recall"] = round(sum(recalls) / len(recalls), 4) if recalls else None
    return out


def train(session: Session, store, settings: Settings, now: datetime | None = None) -> LocalModel | str:
    """Snapshot, train, score and store a model. Returns the new current model, or why nothing was trained."""
    started, now = time.monotonic(), now or datetime.now(UTC)
    stats = snapshot(session, settings)
    examples = list(session.scalars(select(TrainingExample).order_by(TrainingExample.updated_at.desc(),
                                                                     TrainingExample.id.desc())
                                    .limit(settings.local_max_train)))
    learn = [e for e in examples if not held_out(e.domain_id)]
    test = [e for e in examples if held_out(e.domain_id)]
    per_category = Counter(e.label for e in learn)
    classes = {c for c, n in per_category.items() if n >= settings.local_min_examples}
    learn = [e for e in learn if e.label in classes]
    if len(learn) < settings.local_min_train or len(classes) < 2:
        return (f"not enough labelled pages: {len(learn)} in {len(classes)} categories "
                f"(need {settings.local_min_train} in at least 2)")
    version = now.strftime("%Y%m%d-%H%M%S")
    balance = class_weights(Counter(e.label for e in learn), settings.local_class_balance)
    model = fit([page_of(e) for e in learn], [e.label for e in learn],
                [WEIGHT.get(e.origin, 1.0) * balance[e.label] for e in learn],
                version=version, text_chars=settings.local_text_chars)
    metrics = evaluate(model, test) | {
        "class_balance": settings.local_class_balance,
        "snapshot": stats, "train_by_category": dict(sorted(per_category.items())),
        "too_small": sorted(c for c in per_category if c not in classes),
        "ai_labels": settings.local_train_on_ai_labels, "seconds": round(time.monotonic() - started, 1),
        "peak_memory_mb": round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024)}
    key = f"models/yasmama/{version}.npz"
    store.put(key, model.to_bytes(), "application/octet-stream")
    session.execute(update(LocalModel).where(LocalModel.is_current).values(is_current=False))
    row = LocalModel(version=version, object_key=key, is_current=True, train_examples=len(learn),
                     test_examples=len(test), metrics=metrics, trained_at=now)
    session.add(row)
    session.flush()
    for old in session.scalars(select(LocalModel).where(LocalModel.object_key.is_not(None))
                               .order_by(LocalModel.trained_at.desc()).offset(settings.local_keep_models)):
        store.delete(old.object_key)
        old.object_key = None
    log.info("Yaş Mama trained", extra={"version": version, "train": len(learn), "test": len(test),
                                           "seconds": metrics["seconds"]})
    return row


# --------------------------------------------------------------------------- shadow mode

_loaded: dict[str, Weights] = {}  # one model per worker process, reloaded when a new one becomes current


def current(session: Session, store) -> Weights | None:
    row = session.execute(select(LocalModel.version, LocalModel.object_key).where(LocalModel.is_current)).first()
    if row is None or row.object_key is None:
        return None
    if row.version not in _loaded:
        _loaded.clear()
        _loaded[row.version] = Weights.from_bytes(store.get(row.object_key))
    return _loaded[row.version]


def shadow(session: Session, scan: ScanResult, ai_prediction_id: int | None, store) -> LocalPrediction | None:
    """Store what the local model would say about this scan. Publishes nothing."""
    model = current(session, store)
    if model is None:
        return None
    text = session.scalar(select(ExtractedContent.text_excerpt).where(ExtractedContent.scan_result_id == scan.id))
    domain = session.get(Domain, scan.domain_id)
    page = Page(host=_host(scan.final_url) or domain.normalized_domain, title=scan.page_title,
                description=scan.meta_description, text=text)
    if page.empty:
        return None
    top = model.predict([page])[0]
    guess = LocalPrediction(domain_id=scan.domain_id, scan_result_id=scan.id, ai_prediction_id=ai_prediction_id,
                            model_version=model.version, primary_category_id=top[0][0],
                            confidence=round(top[0][1], 3), top=[[c, round(p, 3)] for c, p in top])
    session.add(guess)
    return guess


# --------------------------------------------------------------------------- report


def _pct(part: int, whole: int) -> str:
    return f"{100 * part / whole:.0f} %" if whole else "-"


def report(session: Session, settings: Settings, now: datetime | None = None, days: int = 30) -> str:
    now = now or datetime.now(UTC)
    model = session.scalar(select(LocalModel).where(LocalModel.is_current))
    out = ["# Yaş Mama (local classifier, shadow mode)", ""]
    if model is None:
        return "\n".join(out + ["No model trained yet: `python -m classification.yasmama train` in the worker.", ""])
    m = model.metrics
    out += [f"Model `{model.version}`, trained {model.trained_at:%Y-%m-%d %H:%M} UTC on {model.train_examples} pages "
            f"({'people and confident AI answers' if m.get('ai_labels') else 'people only'}); "
            f"{model.test_examples} held-out pages; {m.get('seconds')} s, peak memory {m.get('peak_memory_mb')} MB.", ""]
    names = {"human": "people's labels", "ai": "confident AI labels"}
    # JSONB does not keep key order (it sorts keys by length), so the order is set here
    groups = m.get("groups", {})
    for group, s in ((g, groups[g]) for g in names if g in groups):
        out += [f"## Held-out pages with {names[group]}: {s['correct']}/{s['pages']} right "
                f"({_pct(s['correct'], s['pages'])})", "",
                "| model confidence ≥ | answers alone | right when it answers |", "|---|---|---|"]
        for t, v in sorted(s["thresholds"].items(), key=lambda kv: float(kv[0])):
            out.append(f"| {t} | {v['answered']} ({_pct(v['answered'], s['pages'])}) | "
                       f"{v['correct']}/{v['answered']} ({_pct(v['correct'], v['answered'])}) |")
        out.append("")
    if m.get("categories"):
        out += [f"## Per category (held-out, ≥ {MIN_PER_CATEGORY_REPORT} pages)", "",
                "| category | pages | recall | precision |", "|---|---|---|---|"]
        for c, v in sorted(m["categories"].items()):
            out.append(f"| {c} | {v['pages']} | {_pct(v['correct'], v['pages'])} | {_pct(v['correct'], v['guessed'])} |")
        out.append("")
    if m.get("too_small"):
        out += [f"Too few pages to learn (< {settings.local_min_examples}): " + ", ".join(m["too_small"]), ""]

    s = live(session, settings, now=now, days=days)
    out += [f"## Live, last {days} days: agreement with confident AI answers ({s['compared']} pages)", ""]
    if s["compared"]:
        out += ["| local confidence ≥ | would answer alone | agrees with the AI |", "|---|---|---|"]
        for row in s["thresholds"]:
            out.append(f"| {row['threshold']} | {row['answered']} ({_pct(row['answered'], s['compared'])}) | "
                       f"{row['agree']}/{row['answered']} ({_pct(row['agree'], row['answered'])}) |")
        out.append("")
        out += [f"### Per day (Türkiye time; alone = local confidence ≥ {ALONE})", "",
                "| day | model | guesses | compared | agrees | alone | right alone |", "|---|---|---|---|---|---|---|"]
        for d in reversed(s["daily"]):
            out.append(f"| {d['day']} | {', '.join(d['models'])} | {d['guesses']} | {d['compared']} | "
                       f"{d['agree']} ({_pct(d['agree'], d['compared'])}) | {d['alone']} | "
                       f"{d['alone_agree']} ({_pct(d['alone_agree'], d['alone'])}) |")
        out.append("")
    p = s["people"]
    out += [f"## Live: pages a person decided after the guess: {p['agree']}/{p['decided']} agree "
            f"({_pct(p['agree'], p['decided'])})", ""]
    return "\n".join(out)


ALONE = 0.8  # the confidence the daily view follows: above it the model could one day answer without the AI


def live(session: Session, settings: Settings, *, now: datetime | None = None, days: int = 30) -> dict:
    """Shadow guesses of the last ``days`` days (Türkiye days, today included) against the AI and against people.

    ``compared`` counts guesses next to a valid AI answer at or above the auto-approve confidence (the answers that
    are published without a person); only those count as agreement. ``daily`` lists every day with a guess."""
    now = now or datetime.now(UTC)
    zone = timezone(timedelta(hours=settings.app_utc_offset_hours))
    since = local_midnight(now, settings.app_utc_offset_hours) - timedelta(days=days - 1)
    rows = session.execute(
        select(LocalPrediction.created_at, LocalPrediction.model_version, LocalPrediction.primary_category_id,
               LocalPrediction.confidence, AIPrediction.primary_category_id.label("ai"),
               (AIPrediction.validation_status == ValidationStatus.VALID)
               & (AIPrediction.confidence >= settings.confidence_auto_approve))
        .outerjoin(AIPrediction, AIPrediction.id == LocalPrediction.ai_prediction_id)
        .where(LocalPrediction.created_at >= since).order_by(LocalPrediction.created_at)).all()
    days_out: dict[str, dict] = {}
    pairs: list[tuple[str, float, str]] = []
    for created, version, mine, p, ai, confident in rows:
        day = created.astimezone(zone).date().isoformat()  # the driver may hand back any offset
        d = days_out.setdefault(day, {"day": day, "models": [], "guesses": 0, "compared": 0, "agree": 0,
                                      "alone": 0, "alone_agree": 0})
        d["guesses"] += 1
        if version not in d["models"]:
            d["models"].append(version)
        if not confident:
            continue
        p = float(p)
        pairs.append((mine, p, ai))
        d["compared"] += 1
        d["agree"] += mine == ai
        if p >= ALONE:
            d["alone"] += 1
            d["alone_agree"] += mine == ai
    thresholds = []
    for t in (0.0, *THRESHOLDS):
        answered = [(mine, ai) for mine, p, ai in pairs if p >= t]
        thresholds.append({"threshold": t, "answered": len(answered),
                           "agree": sum(mine == ai for mine, ai in answered)})
    decided = session.execute(
        select(LocalPrediction.primary_category_id, DomainCategory.category_id)
        .join(DomainCategory, DomainCategory.domain_id == LocalPrediction.domain_id)
        .where(LocalPrediction.created_at >= since, DomainCategory.is_primary, DomainCategory.valid_to.is_(None),
               DomainCategory.origin.in_([AssignmentOrigin(o) for o in TRUSTED]),
               DomainCategory.valid_from > LocalPrediction.created_at)).all()
    return {"since": since.date().isoformat(), "compared": len(pairs), "thresholds": thresholds,
            "daily": list(days_out.values()),
            "people": {"decided": len(decided), "agree": sum(mine == final for mine, final in decided)}}


# --------------------------------------------------------------------------- command line


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m classification.yasmama", description=__doc__.split("\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("train", help="snapshot the labels, train and store a model (no AI calls)")
    p = sub.add_parser("report", help="held-out scores and live shadow agreement (Markdown)")
    p.add_argument("--days", type=int, default=30, help="live window (default 30)")
    args = parser.parse_args(argv)

    from common.config import get_settings
    from database.session import session_factory

    settings = get_settings()
    with session_factory()() as session, session.begin():
        if args.command == "train":
            from crawler.storage import S3Store

            result = train(session, S3Store(settings), settings)
            if isinstance(result, str):
                print(result, file=sys.stderr)
                return 1
            print(f"trained {result.version}: {result.train_examples} pages, {result.test_examples} held out")
        print(report(session, settings, days=getattr(args, "days", 30)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
