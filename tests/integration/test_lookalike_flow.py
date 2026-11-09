import fakeredis
import idna
import pytest
from sqlalchemy.orm import Session, sessionmaker

from common.config import Settings
from integrations import lookalike
from integrations.brand_watch import default_watchlist
from integrations.ct_watch import handle
from integrations.engine import sync
from tests.integration.test_classification import Scripted, make_scan, run
from tests.integration.test_integrations import approved

TOP_CSV = b"GlobalRank,TldRank,Domain\n1,1,google.com\n2,2,amazon.com\n3,3,wikipedia.org\n"
ENTRY = {"name": "Top", "kind": "dataset", "format": "csv", "feed_url": "https://lists.example.com/top.csv",
         "discover": True}


@pytest.fixture
def top_source(engine, clean, monkeypatch):
    monkeypatch.setattr(lookalike, "TOP_SOURCE", "test-top-lookalike")
    monkeypatch.setattr(lookalike, "_cache", None)
    with Session(engine) as s, s.begin():
        approved(s, "test-top-lookalike", ENTRY)
        sync(s, "test-top-lookalike", data=TOP_CSV, entry=ENTRY)
    yield
    lookalike._cache = None


def test_lookalike_of_popular_site_goes_to_a_person_and_can_be_flagged(engine, top_source, client, admin):
    fake = idna.encode("аmazon.com", uts46=True).decode()  # Cyrillic а
    out = run(engine, make_scan(engine, fake), Scripted(primary_category="e-commerce", confidence=0.99))
    assert (out.action, out.reason) == ("review", "lookalike_of:amazon.com")
    assert client.get(f"/api/v1/domain/{fake}").json()["categories"] == []  # nothing published

    task = client.get("/api/v1/admin/reviews", headers=admin).json()["items"][0]
    r = client.post(f"/api/v1/admin/reviews/{task['id']}/decision", headers=admin,
                    json={"action": "set", "primary": "e-commerce", "risk": ["lookalike-domain", "phishing"]})
    assert r.status_code == 200
    risk = client.get(f"/api/v1/domain/{fake}").json()["risk"]
    assert risk["lookalike_domain"] and risk["phishing"]

    # the protected site itself is classified normally
    assert run(engine, make_scan(engine, "amazon.com"), Scripted(primary_category="e-commerce")).action == "applied"


def test_ct_stream_queues_homographs_of_popular_sites(engine, top_source):
    names = [idna.encode("gооgle.com", uts46=True).decode(), "google.com", "amazom.com", "random-shop.net"]
    with Session(engine) as s:
        top = lookalike.load_top_index(s, 1000)
    queued = handle(names, default_watchlist(), fakeredis.FakeRedis(), Settings(_env_file=None),
                    sessionmaker(bind=engine), top)
    assert queued == 2


def test_parked_lookalikes_are_watched_not_queued(engine, top_source, client, admin, capsys):
    from datetime import UTC, datetime, timedelta

    from sqlalchemy import select

    from backend import cli
    from database.models import ClassificationStatus, Domain, ReviewStatus, ReviewTask

    parked = dict(primary_category="parked-domains", secondary_categories=[])
    out = run(engine, make_scan(engine, "amazom.com"), Scripted(confidence=0.96, **parked))
    assert (out.action, out.reason) == ("watch", "parked_lookalike")
    with Session(engine) as s:
        d = s.scalar(select(Domain).where(Domain.normalized_domain == "amazom.com"))
        assert d.classification_status == ClassificationStatus.REANALYZE and d.primary_category_id is None
        assert timedelta(days=6) < d.next_scan - datetime.now(UTC) <= timedelta(days=7)  # re-checked weekly
    # not sure it is parked, a threat claim, or a real page: a person looks
    assert run(engine, make_scan(engine, "amazan.com"), Scripted(confidence=0.8, **parked)).action == "review"
    assert run(engine, make_scan(engine, "anazon.com"),
               Scripted(confidence=0.97, risk_categories=["phishing"], **parked)).action == "review"
    assert run(engine, make_scan(engine, "amazcn.com"), Scripted(primary_category="banking")).action == "review"

    # queues: look-alikes and threat claims apart from category questions
    run(engine, make_scan(engine, "news.example.com"), Scripted(confidence=0.6))
    by_queue = {q: [i["domain"] for i in client.get("/api/v1/admin/reviews", headers=admin,
                                                    params={"queue": q}).json()["items"]]
                for q in ("categories", "threats", "all")}
    assert by_queue["categories"] == ["news.example.com"]
    assert by_queue["threats"] == ["amazan.com", "anazon.com", "amazcn.com"] and len(by_queue["all"]) == 4
    stats = client.get("/api/v1/stats").json()
    assert (stats["pending_reviews"], stats["pending_threat_candidates"]) == (1, 3)

    # a review opened before the rule existed (sure, parked) is closed by the triage command
    with Session(engine) as s, s.begin():
        d = s.scalar(select(Domain).where(Domain.normalized_domain == "amazom.com"))
        s.add(ReviewTask(domain_id=d.id, reason="lookalike_of:amazon.com", status=ReviewStatus.OPEN,
                         ai_prediction_id=out.prediction_ids[0]))
    assert cli.main(["triage-lookalikes"]) == 0 and "dry run: would close 1" in capsys.readouterr().out
    assert cli.main(["triage-lookalikes", "--yes"]) == 0 and "closed 1 parked" in capsys.readouterr().out
    assert client.get("/api/v1/stats").json()["pending_threat_candidates"] == 3  # the unsure ones stay


def test_threat_flags_on_popular_or_official_sites_need_confirmation(engine, top_source, client, admin, make_key):
    """A subdomain of a popular site (here amazon.com from the top list), a brand's official domain or a .gov.tr
    name is almost never a threat: the API asks for confirmation (409 protected_domain) first."""
    names = ["api-glb.smoot.amazon.com", "giris.akbank.com", "ornek.gov.tr", "random-shop.net"]
    for name in names:
        run(engine, make_scan(engine, name), Scripted(confidence=0.6))  # opens a category review
    tasks = {t["domain"]: t["id"] for t in client.get("/api/v1/admin/reviews", headers=admin).json()["items"]}

    def decide(name, **extra):
        return client.post(f"/api/v1/admin/reviews/{tasks[name]}/decision", headers=admin,
                           json={"action": "approve", "risk": ["scam"]} | extra)

    for name, owner in (("api-glb.smoot.amazon.com", "amazon.com"), ("giris.akbank.com", "akbank.com"),
                        ("ornek.gov.tr", ".gov.tr")):
        r = decide(name)
        assert r.status_code == 409 and r.json()["error"]["code"] == "protected_domain", name
        assert r.json()["error"]["details"]["owner"] == owner
    assert decide("api-glb.smoot.amazon.com", confirm_protected=True).status_code == 200
    assert decide("random-shop.net").status_code == 200  # an ordinary site: no question

    # the same guard on the admin threats API and when accepting a "this is phishing" report
    body = {"domain": "giris.akbank.com", "types": ["phishing"], "evidence": "login form on the page"}
    assert client.post("/api/v1/admin/threats", headers=admin, json=body).status_code == 409
    assert client.post("/api/v1/admin/threats", headers=admin, json=body | {"confirm_protected": True}).status_code == 201
    client.post("/api/v1/community/report", headers=make_key("contributor")[0], json={
        "domain": "ornek.gov.tr", "type": "false_negative", "category": "phishing",
        "reason": "The page asks for e-Devlet passwords."})
    sub = client.get("/api/v1/admin/submissions", headers=admin).json()[0]
    r = client.post(f"/api/v1/admin/submissions/{sub['id']}/moderate", headers=admin, json={"action": "accept"})
    assert r.status_code == 409
    assert client.post(f"/api/v1/admin/submissions/{sub['id']}/moderate", headers=admin,
                       json={"action": "accept", "confirm_protected": True}).status_code == 200


def test_parked_lookalike_whose_only_claim_is_the_name_is_watched(engine, top_source):
    """gsoogle.com: parked page, the model adds "lookalike-domain" because of the name alone."""
    parked = dict(primary_category="parked-domains", secondary_categories=[], confidence=0.95)
    out = run(engine, make_scan(engine, "gooogle.com"), Scripted(risk_categories=["lookalike-domain"], **parked))
    assert (out.action, out.reason) == ("watch", "parked_lookalike")
    out = run(engine, make_scan(engine, "googie.com"),
              Scripted(risk_categories=["lookalike-domain", "phishing"], **parked))
    assert out.action == "review"  # any claim beyond the name: a person decides
    out = run(engine, make_scan(engine, "goog1e.com"),
              Scripted(risk_categories=["lookalike-domain"], requires_human_review=True, **parked))
    assert out.action == "watch"  # on a look-alike the model asks for a person because of the name alone


def test_unsure_parked_lookalikes_are_settled_by_a_second_model(engine, top_source, capsys):
    from sqlalchemy import select

    from backend import cli
    from backend.app.classify_tasks import second_opinion_for_task
    from database.models import ReviewStatus, ReviewTask
    from tests.integration.test_classification import S

    parked = dict(primary_category="parked-domains", secondary_categories=[], risk_categories=["lookalike-domain"])
    # new classifications: the second model is asked at once
    out = run(engine, make_scan(engine, "gooogle.com"), Scripted(confidence=0.8, **parked),
              second=Scripted(model="second", confidence=0.75, **parked))
    assert (out.action, len(out.prediction_ids)) == ("watch", 2)
    out = run(engine, make_scan(engine, "googie.com"), Scripted(confidence=0.8, **parked),
              second=Scripted(model="second", primary_category="business"))
    assert out.action == "review"  # the second model sees a real page: a person decides

    # reviews opened before a second model was set
    for name in ("goog1e.com", "gogle.com"):
        run(engine, make_scan(engine, name), Scripted(confidence=0.8, **parked))
    run(engine, make_scan(engine, "googel.com"), Scripted(confidence=0.5, **parked))  # too unsure
    with Session(engine) as s:
        ids = cli.second_opinion_tasks(s)
        names, left = cli.triage_lookalikes(s, dry_run=True)
    assert len(ids) == 2 and names == []
    assert left == {"second_opinion": 2, "models_disagree": 1, "low_confidence": 1}  # googie.com: asked already

    settle = {}
    for task_id, second in zip(ids, (Scripted(model="second", confidence=0.9, **parked),
                                     Scripted(model="second", primary_category="banking")), strict=True):
        with Session(engine) as s, s.begin():
            settle[task_id] = second_opinion_for_task(s, task_id, S, second=second)
    assert sorted(settle.values()) == ["applied", "disagree"]
    with Session(engine) as s:
        open_ = {t.id for t in s.scalars(select(ReviewTask).where(ReviewTask.status == ReviewStatus.OPEN))}
    assert [i for i, r in settle.items() if r == "applied"][0] not in open_
    assert [i for i, r in settle.items() if r == "disagree"][0] in open_

    assert cli.main(["triage-lookalikes"]) == 0
    printed = capsys.readouterr().out
    assert "left open:" in printed and "the second model disagreed" in printed


def test_a_second_opinion_queued_twice_asks_the_model_once(engine, top_source):
    from backend import cli
    from backend.app.classify_tasks import second_opinion_for_task
    from tests.integration.test_classification import S

    parked = dict(primary_category="parked-domains", secondary_categories=[], risk_categories=["lookalike-domain"])
    run(engine, make_scan(engine, "gooogle.com"), Scripted(confidence=0.8, **parked))
    with Session(engine) as s:
        (task_id,) = cli.second_opinion_tasks(s)
    first, again = Scripted(model="second", primary_category="banking"), Scripted(model="second", **parked)
    for provider in (first, again):  # the CLI was run twice before the worker got to it
        with Session(engine) as s, s.begin():
            second_opinion_for_task(s, task_id, S, second=provider)
    assert (first.calls, again.calls) == (1, 0)
    with Session(engine) as s:
        assert cli.second_opinion_tasks(s) == []
