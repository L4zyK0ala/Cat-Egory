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
