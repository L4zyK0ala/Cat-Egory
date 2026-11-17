"""Yaş Mama (admin tab): the local classifier's trained models and how its shadow guesses compare with the AI and with
people over time (classification/yasmama.py). Read-only; nothing here changes what is published."""
from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from common.config import get_settings
from database.models import LocalModel
from database.session import get_session

from ..deps import Principal, rate_limit, require

router = APIRouter(prefix="/api/v1/admin/yasmama", tags=["admin"], dependencies=[Depends(rate_limit("write"))])


class Threshold(BaseModel):
    threshold: float
    answered: int  # pages the model would answer alone at this confidence
    correct: int   # of those, right (held out: the label; live: same as the AI)


class Group(BaseModel):
    pages: int
    correct: int
    thresholds: list[Threshold]


class Category(BaseModel):
    category: str
    pages: int    # held-out pages with this label
    guessed: int  # held-out pages the model put here
    correct: int


class Model(BaseModel):
    version: str
    trained_at: datetime
    current: bool
    train_examples: int
    test_examples: int
    ai_labels: bool | None
    seconds: float | None
    peak_memory_mb: int | None
    class_balance: float | None  # local_class_balance at training (None: before it existed, i.e. 0)
    macro_recall: float | None   # held-out recall averaged over categories, each counting the same
    human: Group | None  # held-out pages a person labelled
    ai: Group | None     # held-out pages with a confident AI label


class Day(BaseModel):
    day: str
    models: list[str]
    guesses: int
    compared: int     # guesses beside a confident, valid AI answer
    agree: int
    alone: int        # compared guesses at confidence >= alone_threshold
    alone_agree: int


class People(BaseModel):
    decided: int  # pages a person decided after the guess
    agree: int


class YasMamaOut(BaseModel):
    shadow: bool
    alone_threshold: float
    auto_approve: float
    days: int
    since: str
    current: Model | None
    categories: list[Category]  # held-out, current model
    too_small: list[str]        # categories with too few pages to learn
    models: list[Model]         # oldest first
    compared: int
    thresholds: list[Threshold]  # live, over the whole period
    daily: list[Day]
    people: People


def _group(raw: dict | None) -> Group | None:
    if not raw:
        return None
    rows = sorted(raw.get("thresholds", {}).items(), key=lambda kv: float(kv[0]))
    return Group(pages=raw["pages"], correct=raw["correct"],
                 thresholds=[Threshold(threshold=float(t), answered=v["answered"], correct=v["correct"]) for t, v in rows])


def _model(row: LocalModel) -> Model:
    m = row.metrics or {}
    groups = m.get("groups", {})
    return Model(version=row.version, trained_at=row.trained_at, current=row.is_current,
                 train_examples=row.train_examples, test_examples=row.test_examples, ai_labels=m.get("ai_labels"),
                 seconds=m.get("seconds"), peak_memory_mb=m.get("peak_memory_mb"),
                 class_balance=m.get("class_balance"), macro_recall=m.get("macro_recall", _macro(m)),
                 human=_group(groups.get("human")), ai=_group(groups.get("ai")))


def _macro(metrics: dict) -> float | None:
    """Models trained before macro_recall was stored: the same figure from their per-category scores."""
    cats = [c for c in metrics.get("categories", {}).values() if c.get("pages")]
    return round(sum(c["correct"] / c["pages"] for c in cats) / len(cats), 4) if cats else None


@router.get("", response_model=YasMamaOut, summary="Yaş Mama: trained models and shadow agreement per day")
def yasmama(days: int = Query(30, ge=1, le=90), _: Principal = Depends(require("scans:read")),
            session: Session = Depends(get_session)) -> YasMamaOut:
    from classification.yasmama import ALONE, live  # NumPy and SciPy: loaded on first use, never on the public API

    settings = get_settings()
    rows = list(session.scalars(select(LocalModel).order_by(LocalModel.trained_at, LocalModel.id)))
    current = next((r for r in rows if r.is_current), None)
    metrics = (current.metrics or {}) if current else {}
    s = live(session, settings, days=days)
    return YasMamaOut(
        shadow=settings.local_shadow, alone_threshold=ALONE, auto_approve=settings.confidence_auto_approve,
        days=days, since=s["since"], current=_model(current) if current else None,
        categories=[Category(category=c, **v) for c, v in sorted(metrics.get("categories", {}).items())],
        too_small=metrics.get("too_small", []), models=[_model(r) for r in rows], compared=s["compared"],
        thresholds=[Threshold(threshold=t["threshold"], answered=t["answered"], correct=t["agree"])
                    for t in s["thresholds"]],
        daily=[Day(**d) for d in s["daily"]], people=People(**s["people"]))
