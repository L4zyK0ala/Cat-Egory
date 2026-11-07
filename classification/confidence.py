"""Confidence policy: maps a (not yet calibrated) model confidence to the next action.

The bands are a starting policy, not accuracy guarantees. They must be re-tuned once enough human-verified
samples exist (Phase 4: reliability diagrams per provider/model, then isotonic or Platt calibration).
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class Decision(StrEnum):
    AUTO_APPROVE_CANDIDATE = "auto_approve_candidate"
    EXTRA_VALIDATION = "extra_validation"
    HUMAN_REVIEW = "human_review"
    REANALYZE = "reanalyze"


@dataclass(frozen=True, slots=True)
class ConfidencePolicy:
    auto_approve: float = 0.90
    extra_validation: float = 0.70
    human_review: float = 0.50

    def __post_init__(self) -> None:
        if not 0 <= self.human_review <= self.extra_validation <= self.auto_approve <= 1:
            raise ValueError("thresholds must satisfy 0 <= review <= extra <= auto <= 1")

    def decide(self, confidence: float, *, requires_human_review: bool = False, has_risk: bool = False,
               injection_suspected: bool = False) -> Decision:
        # Signals that always need a person, regardless of how sure the model claims to be.
        if requires_human_review or injection_suspected or has_risk:
            return Decision.HUMAN_REVIEW if confidence >= self.human_review else Decision.REANALYZE
        if confidence >= self.auto_approve:
            return Decision.AUTO_APPROVE_CANDIDATE
        if confidence >= self.extra_validation:
            return Decision.EXTRA_VALIDATION
        if confidence >= self.human_review:
            return Decision.HUMAN_REVIEW
        return Decision.REANALYZE


DEFAULT_POLICY = ConfidencePolicy()
