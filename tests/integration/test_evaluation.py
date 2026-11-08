from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from classification import evaluation
from classification.evaluation import Site, add, collect, load_set, render
from database.models import ScanJob
from tests.integration.test_classification import Scripted, make_scan, run


def test_bundled_set_is_valid():
    sites = load_set()
    assert len(sites) >= 30 and len({s.domain for s in sites}) == len(sites)


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
