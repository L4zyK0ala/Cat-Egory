"""threats/clef_eval.py: the pages it asks about, what it sends, how it reads clef's answers (no gateway calls)."""
import base64

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from classification.engine import apply_categories
from classification.taxonomy import default_taxonomy
from crawler.storage import MemoryStore
from database.models import AssignmentOrigin, ClassificationStatus, Domain
from tests.integration.test_classification import make_scan
from tests.integration.test_guard_eval import seed
from threats import clef_eval

LICENCE = {"lisansli-loto.com": ("gambling", "Milli Piyango resmi bilet"),
           "kacak-bahis.net": ("illegal-gambling", "Kaçak bahis, bonus"),
           "dizi-film.com": ("streaming-media", "Lisanslı diziler"),
           "korsan-film.net": ("piracy", "Full HD film izle bedava")}


def seed_all(engine):
    seed(engine)
    for name, (primary, text) in LICENCE.items():
        make_scan(engine, name, text=text, title=name)
        with Session(engine) as s, s.begin():
            d = s.scalar(select(Domain).where(Domain.normalized_domain == name))
            apply_categories(s, d, primary=primary, secondary=[], confidence=0.9, origin=AssignmentOrigin.AI,
                             taxonomy=default_taxonomy(), status=ClassificationStatus.CLASSIFIED)


def answer(yes: set[str], p: float = 0.95, tokens: int = 2400):
    """clef's shape (2026-11-22): one number per noul question, the probability of yes."""
    return {"answers": {q: {"type": "noul", "noul": p if q in yes else 1 - p} for q in clef_eval.QUESTIONS},
            "usage": {"input_tokens": tokens, "output_tokens": 0}}


def test_pages_requests_and_report(engine, clean):
    seed_all(engine)
    store = MemoryStore()
    store.put("shot.png", b"\x89PNG fake", "image/png")  # make_scan stores every screenshot under this key
    with Session(engine) as s:
        items = clef_eval.load_items(s, store, per_group=10, licence_per_group=10)
    groups = {it.page.domain: it.page.group for it in items}
    assert groups["ziraat-giris.net"] == "threat" and groups["ornekbank.com.tr"] == "legit_finance_gov"
    assert groups["korsan-film.net"] == "piracy" and "yetiskin.net" not in groups and "tricky.net" not in groups

    body = clef_eval.request(next(it for it in items if it.page.domain == "ziraat-giris.net"))
    assert body["model"] == "clef" and body["state"].startswith("Ziyaret edilen alan adı: ziraat-giris.net\n")
    assert set(body["questions"]) == set(clef_eval.QUESTIONS)
    assert {q["type"] for q in body["questions"].values()} == {"noul"}
    assert body["images"] == ["data:image/png;base64," + base64.b64encode(b"\x89PNG fake").decode()]

    def ask(b):
        name = b["state"].split(": ", 1)[1].split("\n", 1)[0]
        if name in ("ziraat-giris.net", "kazanc-fon.net"):
            return answer({"phishing"})
        if name in ("kacak-bahis.net", "korsan-film.net"):
            return answer({"unlicensed", "brand_match"}, p=0.7)
        if name == "haber.com.tr":
            return answer({"brand_match", "licence_info"}, tokens=16384)  # cut by the gateway
        return answer({"brand_match", "licence_info"})

    pauses = []
    results, error = clef_eval.run(items, ask, pause=1.0, sleep=pauses.append)
    assert error is None and len(results) == len(items) and pauses == [1.0] * (len(items) - 1)
    out = clef_eval.report(results, error)
    assert "Input cut at 16384 tokens: 1." in out and "| legit_other | haber.com.tr |" in out and "(cut)" in out
    assert "| phishing = yes | 2/2 (100 %) | 0/3 (0 %) |" in out
    assert "| brand_match = no | 2/2 (100 %) | 0/3 (0 %) |" in out
    assert "| unlicensed = yes (betting) | 1/1 (100 %) | 0/1 (0 %) |" in out
    assert "| unlicensed = yes, sure (betting) | 0/1 (0 %) | 0/1 (0 %) |" in out  # 0.70 is not sure


def test_reading_answers_defensively():
    v = clef_eval.verdict
    assert (v({"type": "noul", "noul": 0.798}).value, round(v({"type": "noul", "noul": 0.798}).confidence, 3)) == ("yes", 0.798)
    assert (v({"type": "noul", "noul": 0.07}).value, round(v({"type": "noul", "noul": 0.07}).confidence, 2)) == ("no", 0.93)
    assert v({"type": "noul", "noul": 0.07}).sure and not v({"type": "noul", "noul": 0.6}).sure
    assert (v({"answer": "Evet", "probabilities": {"Evet": 0.93, "Hayır": 0.07}}).value,
            v({"answer": "Evet", "probabilities": {"Evet": 0.93}}).confidence) == ("yes", 0.93)
    assert v({"probabilities": {"no": 0.8, "yes": 0.2}}).value == "no"          # no label: the likeliest option
    assert v({"choice": "unknown", "confidence": 0.4}).value == "other"
    assert v(None).value == "other" and v({}).confidence is None
    assert v({"answer": "yes", "probabilities": {"yes": 0.95}}).sure and not v({"answer": "yes"}).sure


def test_rate_limit_waits_then_an_error_stops(engine, clean):
    seed_all(engine)
    with Session(engine) as s:
        items = clef_eval.load_items(s, None, per_group=1, licence_per_group=1)[:3]
    calls = {"n": 0}

    def ask(_):
        calls["n"] += 1
        if calls["n"] == 1:
            raise httpx.HTTPStatusError("busy", request=httpx.Request("POST", "http://x"),
                                        response=httpx.Response(429))
        if calls["n"] == 3:
            raise httpx.HTTPStatusError("down", request=httpx.Request("POST", "http://x"),
                                        response=httpx.Response(503))
        return answer(set())

    waits = []
    results, error = clef_eval.run(items, ask, pause=0, sleep=waits.append)
    assert len(results) == 1 and error == "stopped after 1 pages: HTTP 503" and 60 in waits
    assert "**Incomplete:** stopped after 1 pages: HTTP 503." in clef_eval.report(results, error)
