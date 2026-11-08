from datetime import UTC, datetime

import fakeredis
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from common.config import Settings
from database.models import Domain, DomainSourceRecord, ScanJob
from integrations.brand_watch import default_watchlist
from integrations.ct_watch import PRIORITY, admit, handle
from tests.integration.test_classification import Scripted, make_scan, reviews, run

S = Settings(_env_file=None, ct_watch_max_per_hour=2)


def test_admit_dedupes_and_caps_per_hour():
    r = fakeredis.FakeRedis()
    now = datetime(2026, 11, 9, 10, tzinfo=UTC)
    assert admit(r, "a.example.com", S, now) and not admit(r, "a.example.com", S, now)  # once per 30 days
    assert admit(r, "b.example.com", S, now)
    assert not admit(r, "c.example.com", S, now)                    # third in this hour
    assert admit(r, "c.example.com", S, now.replace(hour=11))       # dropped names may come back later


def test_stream_names_become_scans_and_always_get_a_person(engine, clean):
    names = ["garantibbva-giris.com", "wikipedia.org", "garantibbva.com.tr", "*.ptt-odeme.net", "ptt-odeme.net"]
    queued = handle(names, default_watchlist(), fakeredis.FakeRedis(), S, sessionmaker(bind=engine))
    assert queued == 2
    with Session(engine) as s:
        jobs = {s.get(Domain, j.domain_id).normalized_domain: (str(j.reason), j.priority) for j in s.scalars(select(ScanJob))}
        labels = dict(s.execute(select(Domain.normalized_domain, DomainSourceRecord.source_category)
                                .join(DomainSourceRecord, DomainSourceRecord.domain_id == Domain.id)).all())
    assert jobs == {"garantibbva-giris.com": ("discovery", PRIORITY), "ptt-odeme.net": ("discovery", PRIORITY)}
    assert labels["garantibbva-giris.com"] == "look-alike: Garanti BBVA (bank)"

    # the model is very sure it is a bank: it still goes to a person, nothing is published
    out = run(engine, make_scan(engine, "garantibbva-giris.com"), Scripted(primary_category="banking", confidence=0.99))
    assert (out.action, out.reason) == ("review", "brand_lookalike")
    assert ("brand_lookalike", "open") in reviews(engine)
