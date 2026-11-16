"""Yaş Mama, the local classifier, learns from published labels and guesses in shadow mode only."""
import random

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from backend.app import classify_tasks
from classification import yasmama
from classification.engine import record_threats
from classification.yasmama import Page, Weights, fit, held_out, snapshot, train
from common.config import Settings
from crawler.storage import MemoryStore
from database.models import (
    AssignmentOrigin,
    ClassificationStatus,
    Domain,
    DomainCategory,
    ExtractedContent,
    JobStatus,
    LocalModel,
    LocalPrediction,
    ScanJob,
    ScanReason,
    ScanResult,
    TrainingExample,
)
from tests.integration.test_classification import Scripted, make_scan

S = Settings(_env_file=None, local_min_train=20, local_min_examples=3, local_shadow=True)

WORDS = {
    "news": "haber son dakika gündem ekonomi spor manşet köşe yazarı gazete breaking news headlines",
    "e-commerce": "sepet ürün kargo indirim satın al fiyat ödeme kampanya shop cart checkout order",
    "government": "bakanlık kamu hizmet vatandaş başvuru resmi kurum e-devlet ministry public service",
}


def text_of(category, rng):
    words = WORDS[category].split()
    return " ".join(rng.choice(words) for _ in range(40))


# --------------------------------------------------------------------------- the model itself

def toy(n=20, seed=1):
    rng = random.Random(seed)
    pages, labels = [], []
    for category in WORDS:
        for i in range(n):
            pages.append(Page(host=f"{category}{i}.example", title=f"{category} site", text=text_of(category, rng)))
            labels.append(category)
    return pages, labels


def test_the_model_learns_and_survives_a_round_trip_without_pickle():
    pages, labels = toy()
    model = fit(pages, labels, [1.0] * len(pages), version="v1", text_chars=2000)
    rng = random.Random(9)
    probe = [Page(host="x.example", text=text_of(c, rng)) for c in WORDS]
    guesses = model.predict(probe)
    assert [g[0][0] for g in guesses] == list(WORDS)
    assert all(0 < sum(p for _, p in g) <= 1 + 1e-6 for g in guesses) and len(guesses[0]) == 3
    again = Weights.from_bytes(model.to_bytes())
    assert [g[0][0] for g in again.predict(probe)] == list(WORDS) and again.version == "v1"


def test_two_categories_still_give_probabilities():
    pages, labels = toy()
    keep = [i for i, label in enumerate(labels) if label != "government"]
    model = fit([pages[i] for i in keep], [labels[i] for i in keep], [1.0] * len(keep), version="v", text_chars=500)
    [[(best, p), (other, q)]] = model.predict([Page(host="a.example", text="sepet kargo indirim ödeme")])
    assert best == "e-commerce" and p > q and abs(p + q - 1) < 1e-6


def test_turkish_capital_i_is_folded_like_a_person_would():
    assert yasmama._clean("İSTANBUL İl Müdürlüğü") == yasmama._clean("istanbul il müdürlüğü")


def test_a_fifth_of_domains_is_held_out_for_good():
    share = sum(held_out(i) for i in range(1, 5001)) / 5000
    assert 0.17 < share < 0.23 and held_out(42) == held_out(42)


# --------------------------------------------------------------------------- the training snapshot

def labelled(engine, name, category, origin=AssignmentOrigin.AI, confidence=0.95, text=None, seed=0):
    """A domain with a published main category and a crawled page."""
    with Session(engine) as s, s.begin():
        d = Domain(domain=name, normalized_domain=name, registrable_domain=name, public_suffix="example",
                   classification_status=ClassificationStatus.CLASSIFIED, primary_category_id=category)
        s.add(d)
        s.flush()
        s.add(DomainCategory(domain_id=d.id, category_id=category, is_primary=True, origin=origin,
                             confidence=confidence, taxonomy_version="2.0.0"))
        job = ScanJob(domain_id=d.id, reason=ScanReason.SCHEDULED, status=JobStatus.SUCCEEDED)
        s.add(job)
        s.flush()
        scan = ScanResult(scan_job_id=job.id, domain_id=d.id, requested_url=f"https://{name}/",
                          final_url=f"https://{name}/", page_title=f"{category} home")
        s.add(scan)
        s.flush()
        s.add(ExtractedContent(scan_result_id=scan.id, text_excerpt=text or text_of(category, random.Random(seed))))
        return d.id


def examples(engine):
    with Session(engine) as s:
        return {s.get(Domain, e.domain_id).normalized_domain: (e.label, e.origin) for e in s.scalars(select(TrainingExample))}


def test_the_snapshot_takes_confident_and_human_labels_only(engine, clean):
    labelled(engine, "ai.example", "news")
    labelled(engine, "unsure.example", "news", confidence=0.75)
    labelled(engine, "person.example", "government", origin=AssignmentOrigin.HUMAN, confidence=None)
    labelled(engine, "rule.example", "government", origin=AssignmentOrigin.RULE)
    flagged = labelled(engine, "flagged.example", "e-commerce")
    with Session(engine) as s, s.begin():
        record_threats(s, s.get(Domain, flagged), ["phishing"])  # a copy of a shop is not a shop
    with Session(engine) as s, s.begin():
        stats = snapshot(s, S)
    assert examples(engine) == {"ai.example": ("news", "ai"), "person.example": ("government", "human")}
    assert stats["saved"] == 2

    with Session(engine) as s, s.begin():
        stats = snapshot(s, S.model_copy(update={"local_train_on_ai_labels": False}))
    assert examples(engine) == {"person.example": ("government", "human")} and stats["removed"] == 1


def test_a_label_keeps_its_text_after_the_page_was_purged(engine, clean):
    labelled(engine, "news.example", "news", text="son dakika haberleri")
    with Session(engine) as s, s.begin():
        snapshot(s, S)
        s.execute(ExtractedContent.__table__.delete())  # retention removed the crawl artefacts
        s.scalar(select(DomainCategory)).category_id = "media"  # and a reviewer re-filed the site
    with Session(engine) as s, s.begin():
        assert snapshot(s, S)["kept_old_text"] == 1
    with Session(engine) as s:
        e = s.scalar(select(TrainingExample))
        assert (e.label, e.text) == ("media", "son dakika haberleri")


# --------------------------------------------------------------------------- training

def corpus(engine, per_category=12):
    for category in WORDS:
        for i in range(per_category):
            origin = AssignmentOrigin.HUMAN if i % 4 == 0 else AssignmentOrigin.AI
            labelled(engine, f"{category}-{i}.example", category, origin=origin, seed=i)


def test_training_stores_a_scored_model_and_keeps_only_recent_weights(engine, clean):
    corpus(engine)
    store = MemoryStore()
    with Session(engine) as s, s.begin():
        first = train(s, store, S)
        assert not isinstance(first, str), first
        assert first.is_current and first.train_examples + first.test_examples == 36
        assert set(first.metrics["train_by_category"]) == set(WORDS) and first.metrics["peak_memory_mb"] > 0
        assert sum(g["pages"] for g in first.metrics["groups"].values()) == first.test_examples
        assert Weights.from_bytes(store.get(first.object_key)).classes == sorted(WORDS)
        first_version, first_key = first.version, first.object_key

    settings = S.model_copy(update={"local_keep_models": 1})
    from datetime import UTC, datetime, timedelta

    with Session(engine) as s, s.begin():
        second = train(s, store, settings, now=datetime.now(UTC) + timedelta(days=7)).version
    with Session(engine) as s:
        rows = {m.version: (m.is_current, m.object_key) for m in s.scalars(select(LocalModel))}
    assert rows[second][0] and not rows[first_version][0]
    assert rows[first_version][1] is None  # older weights deleted from storage
    with pytest.raises(KeyError):
        store.get(first_key)


def test_too_few_pages_train_nothing(engine, clean):
    labelled(engine, "one.example", "news")
    with Session(engine) as s, s.begin():
        assert "not enough" in train(s, MemoryStore(), S)
        assert s.scalar(select(func.count()).select_from(LocalModel)) == 0


# --------------------------------------------------------------------------- shadow mode

def trained(engine):
    corpus(engine)
    store = MemoryStore()
    with Session(engine) as s, s.begin():
        train(s, store, S)
    yasmama._loaded.clear()
    return store


def test_the_guess_is_stored_next_to_the_ai_answer_and_changes_nothing(engine, clean):
    store = trained(engine)
    scan_id = make_scan(engine, text="son dakika gündem manşet haber", title="Daily News")
    with Session(engine) as s, s.begin():
        out = classify_tasks.classify_scan(s, scan_id, S, provider=Scripted(), store=store)
    assert out.action == "applied"
    with Session(engine) as s:
        guess = s.scalar(select(LocalPrediction))
        assert guess.ai_prediction_id == out.prediction_ids[0] and guess.primary_category_id == "news"
        assert 0 < float(guess.confidence) <= 1 and len(guess.top) == 3
        d = s.scalar(select(Domain).where(Domain.normalized_domain == "news.example.com"))
        assert d.primary_category_id == "news"  # the AI's answer, published as before


def test_no_guess_when_off_or_without_a_model_and_a_broken_store_is_harmless(engine, clean):
    scan_id = make_scan(engine)
    with Session(engine) as s, s.begin():  # no model yet
        assert classify_tasks.classify_scan(s, scan_id, S, provider=Scripted(), store=MemoryStore()).action == "applied"
    store = trained(engine)
    scan_id = make_scan(engine, name="other.example.com")
    with Session(engine) as s, s.begin():  # switched off
        classify_tasks.classify_scan(s, scan_id, S.model_copy(update={"local_shadow": False}), provider=Scripted(),
                                     store=store)
    with Session(engine) as s:
        assert s.scalar(select(func.count()).select_from(LocalPrediction)) == 0

    yasmama._loaded.clear()
    scan_id = make_scan(engine, name="third.example.com")
    with Session(engine) as s, s.begin():  # weights missing from storage: classification goes on
        out = classify_tasks.classify_scan(s, scan_id, S, provider=Scripted(), store=MemoryStore())
    assert out.action == "applied"
    with Session(engine) as s:
        assert s.scalar(select(func.count()).select_from(LocalPrediction)) == 0
        assert s.scalar(select(Domain.primary_category_id).where(Domain.normalized_domain == "third.example.com")) == "news"


def test_the_report_compares_with_the_ai_and_with_people(engine, clean):
    store = trained(engine)
    scan_id = make_scan(engine, text="son dakika gündem manşet haber")
    with Session(engine) as s, s.begin():
        classify_tasks.classify_scan(s, scan_id, S, provider=Scripted(), store=store)
        text = yasmama.report(s, S)
    assert "Yaş Mama" in text and "agreement with confident AI answers (1 pages)" in text
    assert "| 0.0 | 1 (100 %) | 1/1 (100 %) |" in text
    # a stable order although JSONB reorders keys: people first, thresholds rising, categories alphabetical
    assert text.index("people's labels") < text.index("confident AI labels")
    thresholds = [line.split(" | ")[0][2:] for line in text.splitlines() if line.startswith("| 0.") and "(" in line]
    assert thresholds[:5] == ["0.5", "0.7", "0.8", "0.9", "0.95"]


def test_the_report_without_a_model(engine, clean):
    with Session(engine) as s:
        assert "No model trained yet" in yasmama.report(s, S)


def test_weekly_training_does_nothing_while_shadow_mode_is_off(monkeypatch):
    monkeypatch.setattr(classify_tasks, "get_settings", lambda: Settings(_env_file=None, local_shadow=False))
    assert classify_tasks.yasmama_train.run() == "off"
