"""Calibration report: how often was the model right, per stated-confidence band?

Ground truth = human review decisions. A prediction counts as correct when the reviewer kept its primary
category (approve, or set the same primary). Until there are enough reviewed samples per band, the
confidence thresholds in ``ConfidencePolicy`` are a starting guess, not a measured accuracy.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from database.models import AIPrediction, ReviewStatus, ReviewTask

MIN_SAMPLES_PER_BAND = 30


@dataclass
class Band:
    lower: float
    upper: float
    count: int = 0
    correct: int = 0
    confidence_sum: float = 0.0

    @property
    def accuracy(self) -> float | None:
        return self.correct / self.count if self.count else None

    @property
    def mean_confidence(self) -> float | None:
        return self.confidence_sum / self.count if self.count else None


def _ece_brier(points: list[tuple[float, bool]], bands: int = 10) -> tuple[float | None, float | None]:
    """Expected calibration error (|accuracy - stated confidence| per band, weighted by size) and Brier score
    (mean squared gap between stated confidence and right/wrong). Both 0 for a perfectly calibrated model."""
    if not points:
        return None, None
    groups: dict[int, list[tuple[float, bool]]] = defaultdict(list)
    for c, ok in points:
        groups[min(int(c * bands), bands - 1)].append((c, ok))
    ece = sum(len(g) / len(points) * abs(sum(ok for _, ok in g) / len(g) - sum(c for c, _ in g) / len(g))
              for g in groups.values())
    brier = sum((c - ok) ** 2 for c, ok in points) / len(points)
    return round(ece, 4), round(brier, 4)


def report(session: Session, bands: int = 10) -> dict:
    """Per model: accuracy by stated-confidence band over every reviewed answer, with ECE and Brier score; and the
    same over the random verification samples only (``random``), the unbiased view of what is auto-published,
    with accuracy per main category (Wilson 95% interval)."""
    from .accuracy import CATEGORY_SAMPLE_REASON, SAMPLE_REASON, wilson

    rows = session.execute(
        select(AIPrediction.provider, AIPrediction.model, AIPrediction.confidence, AIPrediction.primary_category_id,
               ReviewTask.decision_primary_id, ReviewTask.reason)
        .join(ReviewTask, ReviewTask.ai_prediction_id == AIPrediction.id)
        .where(ReviewTask.status == ReviewStatus.DONE, ReviewTask.decision_primary_id.is_not(None),
               AIPrediction.confidence.is_not(None))
    ).all()
    per_model: dict[str, list[Band]] = defaultdict(
        lambda: [Band(i / bands, (i + 1) / bands) for i in range(bands)])
    points: dict[str, list[tuple[float, bool]]] = defaultdict(list)
    random_points: dict[str, list[tuple[float, bool]]] = defaultdict(list)
    random_by_category: dict[str, dict[str, list[tuple[float, bool]]]] = defaultdict(lambda: defaultdict(list))
    for provider, model, confidence, predicted, decided, reason in rows:
        c, ok = float(confidence), predicted == decided
        name = f"{provider}/{model}"
        band = per_model[name][min(int(c * bands), bands - 1)]
        band.count += 1
        band.correct += int(ok)
        band.confidence_sum += c
        points[name].append((c, ok))
        if reason in (SAMPLE_REASON, CATEGORY_SAMPLE_REASON):
            random_points[name].append((c, ok))
            random_by_category[name][predicted or "-"].append((c, ok))

    def share(pts: list[tuple[float, bool]]) -> dict:
        n, k = len(pts), sum(ok for _, ok in pts)
        low_high = wilson(k, n)
        return {"n": n, "accuracy": k / n if n else None, "low": low_high[0] if low_high else None,
                "high": low_high[1] if low_high else None,
                "mean_confidence": sum(c for c, _ in pts) / n if n else None}

    models = {}
    for name, bs in per_model.items():
        ece, brier = _ece_brier(points[name], bands)
        r_ece, r_brier = _ece_brier(random_points[name], bands)
        models[name] = {
            "bands": [{"band": f"{b.lower:.1f}-{b.upper:.1f}", "count": b.count, "accuracy": b.accuracy,
                       "mean_confidence": b.mean_confidence, "reliable": b.count >= MIN_SAMPLES_PER_BAND}
                      for b in bs if b.count],
            "ece": ece, "brier": brier,
            "random": {**share(random_points[name]), "ece": r_ece, "brier": r_brier,
                       "categories": sorted(({"category": c, **share(p)} for c, p in random_by_category[name].items()),
                                            key=lambda r: (-r["n"], r["category"]))},
        }
    return {"samples": len(rows), "min_samples_per_band": MIN_SAMPLES_PER_BAND, "models": models}
