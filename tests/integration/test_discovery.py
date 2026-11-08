from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.discovery import PRIORITY, queue_discovery
from common.config import Settings
from database.models import DataSource, Domain, DomainSourceRecord, JobStatus, ScanJob
from integrations.engine import select_rows, sync
from tests.integration.test_integrations import approved

CSV = (b"GlobalRank,TldRank,Domain,TLD,RefSubNets\n"
       b"1,1,big.example.com,com,900\n2,1,haber.example.com.tr,tr,800\n3,2,mid.example.com,com,700\n"
       b"4,2,banka.example.com.tr,tr,600\n5,3,small.example.org,org,500\n")
TOP = {"name": "Top", "kind": "dataset", "format": "csv", "feed_url": "https://lists.example.com/top.csv",
       "discover": True, "max_entries": 3}
TR = TOP | {"name": "TR", "max_entries": None, "include_suffixes": ["tr"]}
S = Settings(_env_file=None, discovery_daily_budget=4, discovery_batch_size=2, discovery_max_queued=10)


def ranks(engine, slug):
    with Session(engine) as s:
        return dict(s.execute(select(Domain.normalized_domain, DomainSourceRecord.source_rank)
                              .join(DomainSourceRecord, DomainSourceRecord.domain_id == Domain.id)
                              .join(DataSource, DataSource.id == DomainSourceRecord.source_id)
                              .where(DataSource.slug == slug)).all())


def queued(engine):
    with Session(engine) as s:
        return [(s.get(Domain, j.domain_id).normalized_domain, str(j.reason), j.priority)
                for j in s.scalars(select(ScanJob).order_by(ScanJob.queued_at, ScanJob.id))]


def test_select_rows_filters_keep_order():
    rows = [(1, "a.com"), (2, "b.com.tr"), (3, "C.GOV.TR."), (4, "d.com"), (5, "e.tr")]
    assert select_rows(rows, {"max_entries": 2}) == [(1, "a.com"), (2, "b.com.tr")]
    assert select_rows(rows, {"include_suffixes": ["tr"]}) == [(2, "b.com.tr"), (3, "C.GOV.TR."), (5, "e.tr")]
    assert select_rows(rows, {"include_suffixes": [".tr"], "max_entries": 1}) == [(2, "b.com.tr")]
    assert select_rows(rows, {}) == rows


def test_sync_applies_filters_and_ranks(engine, clean):
    with Session(engine) as s, s.begin():
        approved(s, "test-top", TOP)
        approved(s, "test-tr", TR)
        assert sync(s, "test-top", data=CSV, entry=TOP).valid == 3
        assert sync(s, "test-tr", data=CSV, entry=TR).valid == 2
    assert ranks(engine, "test-top") == {"big.example.com": 1, "haber.example.com.tr": 2, "mid.example.com": 3}
    assert ranks(engine, "test-tr") == {"haber.example.com.tr": 1, "banka.example.com.tr": 2}
    with Session(engine) as s, s.begin():  # the list changed: ranks follow, dropped entries are delisted
        sync(s, "test-top", data=CSV.replace(b"1,1,big", b"9,9,gone"), entry=TOP)
    assert ranks(engine, "test-top")["haber.example.com.tr"] == 2 and ranks(engine, "test-top")["big.example.com"] == 1
    with Session(engine) as s:
        assert not s.scalar(select(DomainSourceRecord.is_listed).join(Domain, Domain.id == DomainSourceRecord.domain_id)
                            .where(Domain.normalized_domain == "big.example.com"))
    assert queued(engine) == []  # syncing alone never scans


def test_discovery_budget_order_and_once(engine, clean):
    entries = {"test-top": TOP, "test-tr": TR}
    with Session(engine) as s, s.begin():
        approved(s, "test-top", TOP)
        approved(s, "test-tr", TR)
        sync(s, "test-top", data=CSV, entry=TOP)
        sync(s, "test-tr", data=CSV, entry=TR)
    budget = Settings(_env_file=None, discovery_daily_budget=3, discovery_batch_size=2, discovery_max_queued=10)
    with Session(engine) as s, s.begin():
        assert queue_discovery(s, budget, entries=entries) == 2  # batch size
    # best rank across sources first: big (1 in top) and haber (1 in tr), then banka (2 in tr), then mid (3)
    assert [q[0] for q in queued(engine)] == ["big.example.com", "haber.example.com.tr"]
    assert {q[1:] for q in queued(engine)} == {("discovery", PRIORITY)}
    with Session(engine) as s, s.begin():
        assert queue_discovery(s, budget, entries=entries) == 1  # only 1 left of today's budget of 3
        assert queue_discovery(s, budget, entries=entries) == 0
    assert [q[0] for q in queued(engine)][2:] == ["banka.example.com.tr"]

    tomorrow = datetime.now(UTC) + timedelta(days=1)
    with Session(engine) as s, s.begin():
        assert queue_discovery(s, budget, entries=entries, now=tomorrow) == 1  # a new day, one domain left
        s.execute(ScanJob.__table__.update().values(status=JobStatus.SUCCEEDED))
        assert queue_discovery(s, budget, entries=entries, now=tomorrow) == 0  # each domain only once
    assert [q[0] for q in queued(engine)][3:] == ["mid.example.com"]


def test_discovery_waits_for_other_work_and_needs_approval(engine, clean, client, admin):
    entries = {"test-top": TOP}
    with Session(engine) as s, s.begin():
        approved(s, "test-top", TOP)
        sync(s, "test-top", data=CSV, entry=TOP)
    client.post("/api/v1/admin/domains", headers=admin, json={"domains": ["a.example.net", "b.example.net"]})
    busy = Settings(_env_file=None, discovery_daily_budget=10, discovery_batch_size=5, discovery_max_queued=2)
    with Session(engine) as s, s.begin():
        assert queue_discovery(s, busy, entries=entries) == 0  # two manual scans already waiting
        assert queue_discovery(s, busy, entries={"test-top": TOP | {"discover": False}}) == 0
        s.execute(DataSource.__table__.update().where(DataSource.slug == "test-top").values(enabled=False))
        assert queue_discovery(s, S, entries=entries) == 0  # disabled source
        assert queue_discovery(s, Settings(_env_file=None, discovery_daily_budget=0), entries=entries) == 0
