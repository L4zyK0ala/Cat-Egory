import gzip
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from ai_providers.base import ProviderError
from ai_providers.evren import EvrenProvider
from ai_providers.prompting import PROMPT_VERSION
from classification import evaluation, model_compare
from classification.evaluation import Site, add, collect, load_set, render
from classification.taxonomy import default_taxonomy
from common.config import Settings
from crawler.storage import MemoryStore
from database.models import AIPrediction, Domain, DomainCategory, ModelComparison, ReviewTask, ScanJob
from tests.integration.test_classification import Scripted, make_scan, run


def test_bundled_sets_are_valid_and_disjoint():
    sites = load_set()
    holdout = load_set("evaluation_holdout.yaml")  # bare name = bundled set (as in the container)
    assert len(sites) >= 30 and len({s.domain for s in sites}) == len(sites)
    assert len(holdout) >= 25 and len({s.domain for s in holdout}) == len(holdout)
    assert not {s.domain for s in sites} & {s.domain for s in holdout}  # never tuned on the holdout


def test_set_rejects_unknown_categories(tmp_path: Path):
    bad = tmp_path / "s.yaml"
    bad.write_text("sites:\n  - {domain: a.example.com, accept: [not-a-category]}\n")
    with pytest.raises(ValueError, match="not-a-category"):
        load_set(bad)


def test_add_and_report(engine, clean):
    sites = [Site("news.example.com", ["news", "media"]), Site("bank.example.com", ["banking", "finance"]),
             Site("food.example.com", ["food"], reject=["e-commerce"]), Site("missing.example.com", ["travel"])]
    with sessionmaker(bind=engine)() as s, s.begin():
        assert add(s, sites[:3], dry_run=True) == (3, 3)
    with sessionmaker(bind=engine)() as s, s.begin():
        assert add(s, sites[:3]) == (3, 3)
    with Session(engine) as s:
        assert {(str(j.reason), j.priority) for j in s.scalars(select(ScanJob))} == {("manual", 4)}
        for j in s.scalars(select(ScanJob)):
            j.status = "succeeded"
        s.commit()

    run(engine, make_scan(engine, "news.example.com"), Scripted())              # correct, published
    run(engine, make_scan(engine, "bank.example.com"), Scripted(primary_category="finance",
                                                                 confidence=0.6))            # acceptable, review
    run(engine, make_scan(engine, "food.example.com"), Scripted(primary_category="shopping",
                                                                 secondary_categories=["e-commerce"]))  # wrong

    with Session(engine) as s:
        rows = collect(s, sites, "scripted")
    report = render(rows, "scripted")
    assert "| sites | 4 |" in report
    assert "| valid answers | 3/3 (100 %) |" in report
    assert "| primary correct, strict (best answer) | 1/3 (33 %) |" in report
    assert "| primary correct, lenient (acceptable) | 2/3 (67 %) |" in report
    assert "| wrong and published automatically | 1 |" in report
    assert "| wrong secondary categories (from `reject`) | 1 |" in report
    assert "**wrong** (expected food)" in report and "acceptable (best: banking)" in report
    assert "| missing.example.com | not added |" in report


def test_report_shows_errors_and_failed_requeue(engine, clean, monkeypatch, capsys):
    import database.session

    monkeypatch.setattr(database.session, "session_factory", lambda: sessionmaker(bind=engine))
    sites = [Site("ok.example.com", ["news"]), Site("bad.example.com", ["news"])]
    monkeypatch.setattr(evaluation, "load_set", lambda path=None: sites)
    run(engine, make_scan(engine, "ok.example.com"), Scripted())
    run(engine, make_scan(engine, "bad.example.com"), Scripted(primary_category="made-up"))
    with Session(engine) as s:
        report = render(collect(s, sites, "scripted"), "scripted")
    assert "invalid_category: primary_category: unknown content category 'made-up'" in report
    assert evaluation.main(["add", "--failed", "--provider", "scripted"]) == 0
    assert "dry run: 1 sites" in capsys.readouterr().out


def test_cli_dry_run(engine, clean, monkeypatch, capsys):
    import database.session

    monkeypatch.setattr(database.session, "session_factory", lambda: sessionmaker(bind=engine))
    assert evaluation.main(["add"]) == 0
    assert "dry run: " in capsys.readouterr().out
    assert evaluation.main(["report"]) == 0
    assert "# Classification evaluation (evren)" in capsys.readouterr().out


# ----------------------------------------------------------------------------- model comparison



QWEN, GLM = "qwen3.8-flash-next", "glm-5.3"


class Fake(Scripted):
    """Scripted answers per domain; images only where the real model takes them."""

    def __init__(self, model, answers=None, **kw):
        super().__init__(model=model, **kw)
        self.answers, self.seen_images = answers or {}, []

    @property
    def supports_images(self):
        return model_compare.takes_images(self._model) is not False

    def _complete(self, system, user, image, data):
        self.seen_images.append(image is not None)
        self.answer = self.answers.get(data.domain, {})
        return super()._complete(system, user, image, data)


def store_with_artifacts():
    store = MemoryStore()
    store.put("h.gz", gzip.compress(b"<html><h1>Page</h1></html>"), "text/html")
    store.put("shot.png", b"\x89PNG", "image/png")
    return store


def plan_for(engine, sites, models, again=False):
    with Session(engine) as s:
        return model_compare.plan(s, sites, models, prompt_version=PROMPT_VERSION,
                                  taxonomy_version=default_taxonomy().version, again=again)


COMPARE_SITES = [Site("news.example.com", ["news", "media"]), Site("bank.example.com", ["banking", "finance"]),
                 Site("unscanned.example.com", ["travel"])]


def test_compare_asks_each_model_once_and_changes_nothing_public(engine, clean):
    make_scan(engine, "news.example.com")
    make_scan(engine, "bank.example.com")
    with Session(engine) as s:
        before = {d.normalized_domain: str(d.classification_status) for d in s.scalars(select(Domain))}

    p = plan_for(engine, COMPARE_SITES, [QWEN, GLM])
    assert [s.domain for s in p.unscanned] == ["unscanned.example.com"]
    assert len(p.todo) == 4 and p.skipped == 0
    fakes = {QWEN: Fake(QWEN, {"bank.example.com": {"primary_category": "finance", "confidence": 0.8}}),
             GLM: Fake(GLM, {"bank.example.com": {"primary_category": "made-up"}})}
    lines, pauses = [], []
    with Session(engine) as s:
        summary = model_compare.run(s, p, fakes.__getitem__, store=store_with_artifacts(), pause=1.5,
                                    sleep=pauses.append, progress=lines.append)
    assert (summary.asked, summary.valid, summary.failed, summary.stopped) == (4, 3, 1, None)
    assert pauses == [1.5, 1.5, 1.5] and len(lines) == 4
    assert fakes[QWEN].seen_images == [True, True] and fakes[GLM].seen_images == [False, False]  # glm: text only

    with Session(engine) as s:
        rows = {(r.domain_id, r.model): r for r in s.scalars(select(ModelComparison))}
        assert len(rows) == 4
        assert {r.used_screenshot for (_, m), r in rows.items() if m == GLM} == {False}
        assert {r.screenshot_available for r in rows.values()} == {True}
        # nothing visitors see, no review, no second opinion, no production prediction
        assert s.scalar(select(func.count()).select_from(AIPrediction)) == 0
        assert s.scalar(select(func.count()).select_from(ReviewTask)) == 0
        assert s.scalar(select(func.count()).select_from(DomainCategory)) == 0
        assert {d.normalized_domain: str(d.classification_status) for d in s.scalars(select(Domain))} == before

    # idempotent: only the invalid answer is asked again; --again asks everything
    again = plan_for(engine, COMPARE_SITES, [QWEN, GLM])
    assert [(t.site.domain, m) for t, m in again.todo] == [("bank.example.com", GLM)] and again.skipped == 3
    assert len(plan_for(engine, COMPARE_SITES, [QWEN, GLM], again=True).todo) == 4


def test_compare_stops_on_rate_limit_and_resumes(engine, clean):
    make_scan(engine, "news.example.com")
    make_scan(engine, "bank.example.com")

    class Limited(Fake):
        def _complete(self, system, user, image, data):
            raise ProviderError("rate", "too many requests", 429)

    p = plan_for(engine, COMPARE_SITES, [QWEN])
    with Session(engine) as s:
        summary = model_compare.run(s, p, lambda m: Limited(m), pause=0, progress=lambda line: None)
    assert summary.asked == 1 and summary.stopped.startswith(f"{QWEN}: rate")
    with Session(engine) as s:
        assert [r.validation_status for r in s.scalars(select(ModelComparison))] == ["provider_error"]
    assert len(plan_for(engine, COMPARE_SITES, [QWEN]).todo) == 2  # the failed pair is asked again


def test_compare_cli_exit_code_when_stopped(engine, clean, monkeypatch, capsys):
    import common.config
    import database.session

    make_scan(engine, "news.example.com")
    monkeypatch.setattr(database.session, "session_factory", lambda: sessionmaker(bind=engine))
    monkeypatch.setattr(evaluation, "load_set", lambda path=None: COMPARE_SITES[:1])
    monkeypatch.setattr(common.config, "get_settings", lambda: Settings(_env_file=None, evren_api_key="test-key"))
    monkeypatch.setattr(model_compare, "make_store", lambda settings: MemoryStore())
    monkeypatch.setattr(model_compare, "make_provider", lambda settings, model: Scripted(fail=True, model=model))
    assert evaluation.main(["compare", "--models", QWEN, "--yes", "--pause", "0"]) == 0  # connection: not a stop
    class NoCredit(Scripted):
        def _complete(self, system, user, image, data):
            raise ProviderError("credit", "no credit", 402)

    monkeypatch.setattr(model_compare, "make_provider", lambda settings, model: NoCredit(model=model))
    assert evaluation.main(["compare", "--models", QWEN, "--yes", "--pause", "0"]) == 2
    captured = capsys.readouterr()
    assert "stopped early" in captured.err and "# Model comparison" in captured.out


def test_compare_report(engine, clean):
    sites = COMPARE_SITES + [Site("food.example.com", ["food"])]
    for name in ("news.example.com", "bank.example.com", "food.example.com"):
        make_scan(engine, name)
    fakes = {QWEN: Fake(QWEN, {"bank.example.com": {"primary_category": "finance", "confidence": 0.8},
                               "food.example.com": {"primary_category": "shopping", "confidence": 0.92}}),
             GLM: Fake(GLM, {"bank.example.com": {"primary_category": "banking", "confidence": 0.6},
                             "food.example.com": {"primary_category": "made-up"}})}
    with Session(engine) as s:
        model_compare.run(s, plan_for(engine, sites, [QWEN, GLM]), fakes.__getitem__, store=store_with_artifacts(),
                          pause=0, progress=lambda line: None)
        report = model_compare.render(sites, [QWEN, GLM], model_compare.collect(s, sites, [QWEN, GLM]),
                                      set_name="test.yaml", prompt_version=PROMPT_VERSION)
    lines = report.splitlines()
    qwen = next(line for line in lines if line.startswith(f"| {QWEN} | text + screenshot |"))
    assert "| 3 | 3/3 (100 %) | 1/3 (33 %) | 2/3 (67 %) | 1/2 (50 %) | 2/2 (100 %) | 1 |" in qwen
    glm = next(line for line in lines if line.startswith(f"| {GLM} | text only (no image input) |"))
    assert "| 3 | 2/3 (67 %) | 2/2 (100 %) | 2/2 (100 %) | 2/2 (100 %) | 2/2 (100 %) | 0 |" in glm
    assert f"| {QWEN} | 1/2 (50 %) | 1/1 (100 %) | - | - |" in report  # bands: news 0.95 ok, food 0.92 wrong
    assert f"| {GLM} | 1/1 (100 %) | - | 1/1 (100 %) | - |" in report
    assert f"| {QWEN} | {GLM} | 2 | 1/2 (50 %) |" in report  # news agree, bank differs
    assert f"| {QWEN} | food.example.com | shopping 0.92 | food |" in report  # wrong at >= 0.90
    assert "| bank.example.com | banking, finance | finance 0.80 ≈ | banking 0.60 ✓ |" in report
    assert "| unscanned.example.com | travel | – | – |" in report
    assert "| food.example.com | food | shopping 0.92 ✗ | _invalid_category_ |" in report
    assert "unknown content category 'made-up'" in report


def test_compare_sends_no_image_to_text_only_models(engine, clean, monkeypatch, capsys):
    """The real EVREN client with a fake transport (no network): glm-5.3 gets text only, qwen gets the screenshot."""
    import common.config
    import database.session

    make_scan(engine, "news.example.com")
    monkeypatch.setattr(database.session, "session_factory", lambda: sessionmaker(bind=engine))
    monkeypatch.setattr(evaluation, "load_set", lambda path=None: COMPARE_SITES[:1])
    monkeypatch.setattr(common.config, "get_settings", lambda: Settings(_env_file=None, evren_api_key="test-key"))
    monkeypatch.setattr(model_compare, "make_store", lambda settings: store_with_artifacts())
    bodies = []

    def handler(request):
        body = json.loads(request.content)
        bodies.append(body)
        answer = {"domain": "news.example.com", "primary_category": "news", "confidence": 0.93,
                  "evidence": [{"type": "text", "observation": "headlines"}]}
        return httpx.Response(200, json={"id": "r1", "model": f"vendor/{body['model']}",
                                         "choices": [{"message": {"content": json.dumps(answer)},
                                                      "finish_reason": "stop"}],
                                         "usage": {"prompt_tokens": 1000, "completion_tokens": 120}})

    monkeypatch.setattr(model_compare, "make_provider", lambda settings, model: EvrenProvider(
        api_key="test-key", base_url="https://evren.invalid", model=model,
        client=httpx.Client(transport=httpx.MockTransport(handler))))

    assert evaluation.main(["compare", "--models", f"{QWEN},{GLM}", "--pause", "0"]) == 0
    assert evaluation.main(["compare", "--models", f"{QWEN},{GLM}", "--yes", "--pause", "0"]) == 0
    out = capsys.readouterr().out
    assert "-> 2 EVREN calls" in out and f"{GLM}: 1  (text only" in out
    assert "# Model comparison (evaluation_set.yaml)" in out
    images = {b["model"]: isinstance(b["messages"][1]["content"], list) for b in bodies}
    assert images == {QWEN: True, GLM: False}
    with Session(engine) as s:
        assert {r.answered_by for r in s.scalars(select(ModelComparison))} == {QWEN, GLM}

    from backend.app.discovery import tokens_since
    with Session(engine) as s:  # comparisons count towards the daily token cap
        assert tokens_since(s, datetime.now(UTC) - timedelta(hours=1)) == 2 * 1120

    # second run: nothing left to ask, the report is still printed
    assert evaluation.main(["compare", "--models", f"{QWEN},{GLM}", "--yes"]) == 0
    assert len(bodies) == 2 and "| news.example.com | news, media | news 0.93 ✓ | news 0.93 ✓ |" in capsys.readouterr().out


def test_compare_cli_checks(engine, clean, monkeypatch, capsys):
    import common.config
    import database.session

    monkeypatch.setattr(database.session, "session_factory", lambda: sessionmaker(bind=engine))
    assert evaluation.main(["compare", "--models", "gpt-9"]) == 1
    assert "unknown model(s) gpt-9" in capsys.readouterr().err
    make_scan(engine, "news.example.com")
    monkeypatch.setattr(evaluation, "load_set", lambda path=None: COMPARE_SITES[:1])
    monkeypatch.setattr(common.config, "get_settings", lambda: Settings(_env_file=None, evren_api_key=None))
    assert evaluation.main(["compare", "--models", QWEN, "--yes"]) == 1  # no key outside the worker
    assert "run this in the worker container" in capsys.readouterr().err
    assert evaluation.main(["compare-report", "--models", QWEN]) == 0
    assert "| news.example.com | news, media | – |" in capsys.readouterr().out


def test_db_sets_take_a_persons_decision(engine, clean, monkeypatch, capsys):
    """verified:N / hard:N: person-checked, scanned sites; the answer is the person's; the pick is reproducible."""
    import database.session
    from classification.engine import apply_categories
    from database.models import AssignmentOrigin, ClassificationStatus, ReviewStatus

    sites = {"kept.example.com": ("news", ClassificationStatus.VERIFIED, "news"),
             "changed.example.com": ("blogs", ClassificationStatus.VERIFIED, "news"),
             "auto.example.com": ("news", ClassificationStatus.CLASSIFIED, None),
             "threat.example.com": ("news", ClassificationStatus.VERIFIED, None)}
    for name in sites:
        make_scan(engine, name)
    with Session(engine) as s, s.begin():
        for name, (primary, status, ai_said) in sites.items():
            d = s.scalar(select(Domain).where(Domain.normalized_domain == name))
            apply_categories(s, d, primary=primary, secondary=["media"] if name == "kept.example.com" else [],
                             confidence=0.9, origin=AssignmentOrigin.HUMAN, taxonomy=default_taxonomy(), status=status)
            if ai_said:
                p = AIPrediction(domain_id=d.id, provider="evren", model=QWEN, prompt_version="x", taxonomy_version="3.0.0",
                                 validation_status="valid", primary_category_id=ai_said, confidence=0.6)
                s.add(p)
                s.flush()
                s.add(ReviewTask(domain_id=d.id, ai_prediction_id=p.id, reason="low_confidence", status=ReviewStatus.DONE,
                                 decision_primary_id=primary))
        s.execute(Domain.__table__.update().where(Domain.normalized_domain == "threat.example.com")
                  .values(is_active=False))

    with Session(engine) as s:
        picked = evaluation.load_db_set(s, "verified", 10)
        assert {x.domain for x in picked} == {"kept.example.com", "changed.example.com"}
        assert next(x for x in picked if x.domain == "kept.example.com").accept == ["news", "media"]
        assert [x.domain for x in evaluation.load_db_set(s, "hard", 10)] == ["changed.example.com"]
        assert [x.domain for x in evaluation.load_db_set(s, "verified", 2, seed=7)] == \
               [x.domain for x in evaluation.load_db_set(s, "verified", 2, seed=7)]  # same seed, same pick
        assert len(evaluation.load_db_set(s, "verified", 1)) == 1

    assert evaluation.db_set("verified:150") == ("verified", 150) and evaluation.db_set("evaluation_set.yaml") is None
    with pytest.raises(ValueError):
        evaluation.db_set("hard:0")
    monkeypatch.setattr(database.session, "session_factory", lambda: sessionmaker(bind=engine))
    assert evaluation.main(["compare-report", "--models", QWEN, "--set", "hard:5"]) == 0
    out = capsys.readouterr().out
    assert "hard:5, seed 2026, 1 sites, answer: a person's decision" in out and "changed.example.com" in out
    assert evaluation.main(["report", "--set", "verified:5"]) == 1  # file sets only
