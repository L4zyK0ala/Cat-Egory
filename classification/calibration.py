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


def report(session: Session, bands: int = 10) -> dict:
    rows = session.execute(
        select(AIPrediction.provider, AIPrediction.model, AIPrediction.confidence, AIPrediction.primary_category_id,
               ReviewTask.decision_primary_id)
        .join(ReviewTask, ReviewTask.ai_prediction_id == AIPrediction.id)
        .where(ReviewTask.status == ReviewStatus.DONE, ReviewTask.decision_primary_id.is_not(None),
               AIPrediction.confidence.is_not(None))
    ).all()
    per_model: dict[str, list[Band]] = defaultdict(
        lambda: [Band(i / bands, (i + 1) / bands) for i in range(bands)])
    for provider, model, confidence, predicted, decided in rows:
        c = float(confidence)
        band = per_model[f"{provider}/{model}"][min(int(c * bands), bands - 1)]
        band.count += 1
        band.correct += int(predicted == decided)
        band.confidence_sum += c
    return {
        "samples": len(rows),
        "min_samples_per_band": MIN_SAMPLES_PER_BAND,
        "models": {
            name: [{"band": f"{b.lower:.1f}-{b.upper:.1f}", "count": b.count, "accuracy": b.accuracy,
                    "mean_confidence": b.mean_confidence, "reliable": b.count >= MIN_SAMPLES_PER_BAND}
                   for b in bs if b.count]
            for name, bs in per_model.items()
        },
    }
