"""Confidence policy: maps a model's stated confidence to the next action.

Measured on 2026-11-20 (``python -m backend.cli calibration``): of 99 randomly sampled published answers 99 % were
right, and reviewed answers the model stated 0.80-0.89 for were right 96 % of the time (426), 0.50-0.69 97 %: the
model is under-confident, and its confidence barely separates right from wrong. So an answer is published from
``auto_approve_ordinary`` (0.80) in ordinary categories, while ``auto_approve`` (0.90) stays for SENSITIVE ones,
where a wrong label matters more (restricted content, banking, finance, government), the main or a secondary
category alike. Watch the random verification samples after any change.
"""
from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from enum import StrEnum

# Restricted content (taxonomy group "group-restricted-content") and categories people trust a label for.
SENSITIVE = frozenset({"adult-content", "gambling", "illegal-gambling", "piracy", "alcohol", "tobacco", "weapons",
                       "illegal-drugs", "violence", "hate-and-discrimination", "banking", "finance", "government"})


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
    auto_approve_ordinary: float | None = None  # None: auto_approve everywhere
    sensitive: frozenset[str] = SENSITIVE

    def __post_init__(self) -> None:
        ordinary = self.auto_approve if self.auto_approve_ordinary is None else self.auto_approve_ordinary
        if not 0 <= self.human_review <= self.extra_validation <= ordinary <= self.auto_approve <= 1:
            raise ValueError("thresholds must satisfy 0 <= review <= extra <= ordinary <= auto <= 1")

    def threshold(self, categories: Iterable[str | None] = ()) -> float:
        """The confidence needed to publish an answer with these categories (main and secondary)."""
        if self.auto_approve_ordinary is None or any(c in self.sensitive for c in categories if c):
            return self.auto_approve
        return self.auto_approve_ordinary

    def decide(self, confidence: float, *, requires_human_review: bool = False, has_risk: bool = False,
               injection_suspected: bool = False, categories: Iterable[str | None] = ()) -> Decision:
        # Signals that always need a person, regardless of how sure the model claims to be.
        if requires_human_review or injection_suspected or has_risk:
            return Decision.HUMAN_REVIEW if confidence >= self.human_review else Decision.REANALYZE
        if confidence >= self.threshold(categories):
            return Decision.AUTO_APPROVE_CANDIDATE
        if confidence >= self.extra_validation:
            return Decision.EXTRA_VALIDATION
        if confidence >= self.human_review:
            return Decision.HUMAN_REVIEW
        return Decision.REANALYZE


DEFAULT_POLICY = ConfidencePolicy()
