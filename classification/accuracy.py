"""How right is Cat-Egory? The public accuracy report (``GET /api/v1/accuracy``, the /accuracy page).

Measured against people, never against the model itself:

* **Published AI answers.** Confident answers are published without a person; reviewers check a random sample of
  them ("verification_sample"). The share they kept is the accuracy of what the public sees from the AI. It is the
  only unbiased figure here, so it leads the page, with a 95% interval (Wilson) that shows how much a small
  sample can be trusted.
* **Hard cases.** Answers the model was unsure about, or where two models disagreed, always go to a person. Their
  hit rate is shown apart: it is lower by design and must not be read as overall accuracy.
* **Threat flags.** Our own records (a reviewer's or Octryx's): how many are active, how many were taken back, and how
  long taking one back took. A withdrawn flag is a mistake or a site that was cleaned; the report cannot tell which.
* **Who decided.** Published domains by the origin of their main category (AI, person, community, rule).
"""
from __future__ import annotations

import math
from collections import defaultdict
from datetime import UTC, datetime
from statistics import median

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from database.models import (
    AIPrediction,
    AuditLog,
    DataSource,
    Domain,
    DomainCategory,
    ReviewStatus,
    ReviewTask,
    ThreatIntelligence,
)
from database.publication import published

from .engine import OWN_THREAT_SOURCES

SAMPLE_REASON = "verification_sample"
HARD_REASONS = ("low_confidence", "needs_extra_validation", "model_disagreement", "model_requested_review")
MIN_PER_CATEGORY = 5  # below this a category's share says nothing


def wilson(correct: int, n: int, z: float = 1.96) -> tuple[float, float] | None:
    if n == 0:
        return None
    p = correct / n
    centre = (p + z * z / (2 * n)) / (1 + z * z / n)
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / (1 + z * z / n)
    return max(0.0, centre - half), min(1.0, centre + half)


def _share(correct: int, n: int, rejected: int = 0) -> dict:
    interval = wilson(correct, n)
    return {"n": n, "correct": correct, "rejected": rejected, "accuracy": correct / n if n else None,
            "low": interval[0] if interval else None, "high": interval[1] if interval else None}


def report(session: Session, now: datetime | None = None) -> dict:
    rows = session.execute(
        select(ReviewTask.reason, AIPrediction.primary_category_id, ReviewTask.decision_primary_id)
        .join(AIPrediction, AIPrediction.id == ReviewTask.ai_prediction_id)
        .where(ReviewTask.status == ReviewStatus.DONE, AIPrediction.primary_category_id.is_not(None))
    ).all()
    sample = [(p, d) for reason, p, d in rows if reason == SAMPLE_REASON]
    hard = [(p, d) for reason, p, d in rows if reason in HARD_REASONS]

    per_category: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    for predicted, decided in sample:
        per_category[predicted][0] += 1
        per_category[predicted][1] += int(predicted == decided)
    categories = sorted(({"category": c, **_share(k, n)} for c, (n, k) in per_category.items() if n >= MIN_PER_CATEGORY),
                        key=lambda r: (-r["n"], r["category"]))

    own = select(DataSource.id).where(DataSource.slug.in_(OWN_THREAT_SOURCES))
    records = session.execute(
        select(ThreatIntelligence.domain_id, ThreatIntelligence.is_active, ThreatIntelligence.raw,
               ThreatIntelligence.first_seen)
        .where(ThreatIntelligence.source_id.in_(own), ThreatIntelligence.url_id.is_(None))).all()
    withdrawn = [r for r in records if not r.is_active and not (r.raw or {}).get("expired_at")]
    expired = [r for r in records if not r.is_active and (r.raw or {}).get("expired_at")]
    first_seen: dict[str, datetime] = {}
    names = dict(session.execute(select(Domain.id, Domain.normalized_domain)
                                 .where(Domain.id.in_({r.domain_id for r in records}))).all()) if records else {}
    for r in records:
        name = names.get(r.domain_id)
        if name and (name not in first_seen or r.first_seen < first_seen[name]):
            first_seen[name] = r.first_seen
    hours = [(at - first_seen[target]).total_seconds() / 3600 for target, at in session.execute(
        select(AuditLog.target_id, AuditLog.occurred_at).where(AuditLog.action == "threat.withdraw")).all()
        if target in first_seen and at >= first_seen[target]]

    origins = dict(session.execute(
        select(DomainCategory.origin, func.count(func.distinct(DomainCategory.domain_id)))
        .join(Domain, Domain.id == DomainCategory.domain_id)
        .where(DomainCategory.valid_to.is_(None), DomainCategory.is_primary, published())
        .group_by(DomainCategory.origin)).all())

    return {
        "generated_at": (now or datetime.now(UTC)).isoformat(),
        "published_ai": {**_share(sum(p == d for p, d in sample), len(sample), sum(d is None for _, d in sample)),
                         "categories": categories, "min_per_category": MIN_PER_CATEGORY},
        "hard_cases": _share(sum(p == d for p, d in hard), len(hard), sum(d is None for _, d in hard)),
        "threats": {"total": len(records), "active": sum(r.is_active for r in records), "withdrawn": len(withdrawn),
                    "expired": len(expired),
                    "withdrawn_share": len(withdrawn) / len(records) if records else None,
                    "median_hours_to_withdraw": median(hours) if hours else None, "withdrawals_timed": len(hours)},
        "published_by_origin": {str(k): v for k, v in origins.items()},
    }
