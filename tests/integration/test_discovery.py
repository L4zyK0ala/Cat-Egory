from datetime import UTC, datetime, timedelta

from sqlalchemy import select, update
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


CRUX = (b"origin,rank\nhttps://www.pornsite.xyz,1000\nhttps://m.habersitesi.com.tr,1000\n"
        b"https://www.habersitesi.com.tr,1000\nhttps://bankasi.com.tr,5000\nhttps://bigsite.com,5000\n"
        b"https://deepsite.net,500000\nnot a url,1000\nhttps://norank.com,\n")
CRUX_TR = {"name": "CrUX TR", "kind": "dataset", "format": "csv", "feed_url": "https://lists.example.com/{yyyymm}.gz",
           "column": "origin", "rank_column": "rank", "max_rank": 50000, "registrable_only": True,
           "discover": True, "discover_priority": 1}


def test_a_ranked_gzip_list_of_origins_becomes_one_domain_per_site(engine, clean):
    import gzip

    with Session(engine) as s, s.begin():
        approved(s, "test-crux", CRUX_TR)
        result = sync(s, "test-crux", data=gzip.compress(CRUX), entry=CRUX_TR)
    # www. and m. of one site are one domain; 500000 is over max_rank; a row without a rank is skipped
    assert ranks(engine, "test-crux") == {"pornsite.xyz": 1000, "habersitesi.com.tr": 1000,
                                          "bankasi.com.tr": 5000, "bigsite.com": 5000}
    assert result.invalid == 1  # "not a url"


def test_discovery_takes_the_source_priority_before_the_rank(engine, clean):
    """CrUX Türkiye (priority 1) comes before the .tr list (2) and the world list (3), whatever the numbers."""
    import gzip

    entries = {"test-top": TOP | {"max_entries": None, "discover_priority": 3}, "test-crux": CRUX_TR}
    with Session(engine) as s, s.begin():
        approved(s, "test-top", entries["test-top"])
        approved(s, "test-crux", CRUX_TR)
        sync(s, "test-top", data=CSV, entry=entries["test-top"])  # ranks 2..6: smaller numbers than CrUX's buckets
        sync(s, "test-crux", data=gzip.compress(CRUX), entry=CRUX_TR)
    one = Settings(_env_file=None, discovery_daily_budget=100, discovery_batch_size=1, discovery_max_queued=100)
    order: list[str] = []
    for _ in range(9):  # one at a time: jobs queued together carry the same time
        with Session(engine) as s, s.begin():
            assert queue_discovery(s, one, entries=entries) == 1
        order += [name for name, _, _ in queued(engine) if name not in order]
    # a restricted-looking name takes its quarter first (pornsite.xyz), then the rest in place order: the CrUX
    # buckets, then the world list by its rows
    assert order[0] == "pornsite.xyz"
    assert order[1:] == ["habersitesi.com.tr", "bankasi.com.tr", "bigsite.com", "big.example.com",
                         "haber.example.com.tr", "mid.example.com", "banka.example.com.tr", "small.example.org"]


def test_the_newest_monthly_file_is_found(monkeypatch):
    from integrations import engine as eng

    tried = []

    def fake(url, **_):
        tried.append(url[-11:])
        if "202611" in url or "202610" in url:
            raise eng.SourceError("http_status", "feed answered HTTP 404")
        return b"data"

    monkeypatch.setattr(eng, "download", fake)
    assert eng.download_latest("https://x.example/{yyyymm}.csv.gz", today=datetime(2026, 11, 17, tzinfo=UTC)) == b"data"
    assert tried == ["202611.csv.gz"[-11:], "202610.csv.gz"[-11:], "202609.csv.gz"[-11:]]
    assert eng.download_latest("https://x.example/plain.csv") == b"data"  # no template: one plain download
    monkeypatch.setattr(eng, "download", lambda url, **_: (_ for _ in ()).throw(eng.SourceError("http_status", "404")))
    try:
        eng.download_latest("https://x.example/{yyyymm}.gz", today=datetime(2026, 1, 5, tzinfo=UTC))
        raise AssertionError("expected no file")
    except eng.SourceError as exc:
        assert exc.code == "http_status"


def test_sync_applies_filters_and_ranks(engine, clean):
    with Session(engine) as s, s.begin():
        approved(s, "test-top", TOP)
        approved(s, "test-tr", TR)
        assert sync(s, "test-top", data=CSV, entry=TOP).valid == 3
        assert sync(s, "test-tr", data=CSV, entry=TR).valid == 2
    # the row in the source file (line 1 is the header): both cuts of the same list agree on popularity
    assert ranks(engine, "test-top") == {"big.example.com": 2, "haber.example.com.tr": 3, "mid.example.com": 4}
    assert ranks(engine, "test-tr") == {"haber.example.com.tr": 3, "banka.example.com.tr": 5}
    with Session(engine) as s, s.begin():  # the list changed: ranks follow, dropped entries are delisted
        sync(s, "test-top", data=CSV.replace(b"1,1,big", b"9,9,gone"), entry=TOP)
    assert ranks(engine, "test-top")["haber.example.com.tr"] == 3 and ranks(engine, "test-top")["big.example.com"] == 2
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
    # most popular first, by the row in the shared source file: big (2), haber (3), then mid (4), then banka (5).
    # (Before, the .tr cut numbered its own rows 1, 2 ... and banka, 5th in the file, came before mid.)
    # (jobs queued in one batch share queued_at and have random ids, so compare the batch as a set)
    assert {q[0] for q in queued(engine)} == {"big.example.com", "haber.example.com.tr"}
    assert {q[1:] for q in queued(engine)} == {("discovery", PRIORITY)}
    with Session(engine) as s, s.begin():
        assert queue_discovery(s, budget, entries=entries) == 1  # only 1 left of today's budget of 3
        assert queue_discovery(s, budget, entries=entries) == 0
    assert {q[0] for q in queued(engine)} - {"big.example.com", "haber.example.com.tr"} == {"mid.example.com"}

    tomorrow = datetime.now(UTC) + timedelta(days=1)
    with Session(engine) as s, s.begin():
        assert queue_discovery(s, budget, entries=entries, now=tomorrow) == 1  # a new day, one domain left
        s.execute(ScanJob.__table__.update().values(status=JobStatus.SUCCEEDED))
        assert queue_discovery(s, budget, entries=entries, now=tomorrow) == 0  # each domain only once
    assert len(queued(engine)) == 4 and "banka.example.com.tr" in {q[0] for q in queued(engine)}


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


def spend(engine, statuses, code=None, when=None):
    """Give the queued discovery jobs these outcomes, oldest first."""
    with Session(engine) as s, s.begin():
        jobs = s.scalars(select(ScanJob).where(ScanJob.status == JobStatus.QUEUED).order_by(ScanJob.id)).all()
        for job, status in zip(jobs, statuses, strict=False):
            job.status, job.error_code, job.finished_at = status, code, when or datetime.now(UTC)


def setup_list(engine):
    entries = {"test-top": TOP | {"max_entries": None}}
    with Session(engine) as s, s.begin():
        approved(s, "test-top", entries["test-top"])
        sync(s, "test-top", data=CSV, entry=entries["test-top"])
    return entries


def test_only_scans_that_can_reach_the_ai_use_up_the_budget(engine, clean):
    entries = setup_list(engine)
    budget = Settings(_env_file=None, discovery_daily_budget=2, discovery_batch_size=2, discovery_max_queued=10)
    with Session(engine) as s, s.begin():
        assert queue_discovery(s, budget, entries=entries) == 2
    with Session(engine) as s, s.begin():
        assert queue_discovery(s, budget, entries=entries) == 0  # both still count while they may succeed
    spend(engine, [JobStatus.FAILED, JobStatus.BLOCKED], "dns_nxdomain")
    with Session(engine) as s, s.begin():
        assert queue_discovery(s, budget, entries=entries) == 2  # their places came back
    spend(engine, [JobStatus.SUCCEEDED, JobStatus.SUCCEEDED])
    with Session(engine) as s, s.begin():
        assert queue_discovery(s, budget, entries=entries) == 0  # successes use it up


def test_an_outage_pauses_discovery_but_dead_sites_do_not(engine, clean):
    entries = setup_list(engine)
    cfg = Settings(_env_file=None, discovery_daily_budget=9, discovery_batch_size=1, discovery_max_queued=10,
                   discovery_pause_after_infra_failures=2)
    now = datetime.now(UTC)
    for _ in range(2):
        with Session(engine) as s, s.begin():
            assert queue_discovery(s, cfg, entries=entries, now=now) == 1
    spend(engine, [JobStatus.FAILED, JobStatus.FAILED], "dns_nxdomain", now)
    with Session(engine) as s, s.begin():
        assert queue_discovery(s, cfg, entries=entries, now=now) == 1  # the sites' fault: carry on
    with Session(engine) as s, s.begin():
        s.execute(update(ScanJob).where(ScanJob.status == JobStatus.FAILED).values(error_code="sandbox_unavailable"))
        assert queue_discovery(s, cfg, entries=entries, now=now) == 0  # our fault: stop
        assert queue_discovery(s, cfg, entries=entries, now=now + timedelta(hours=2)) == 1  # the hour has passed


def test_infra_failures_are_counted_for_the_alert(engine, clean):
    from backend.app.discovery import infra_failures_last_hour

    entries = setup_list(engine)
    cfg = Settings(_env_file=None, discovery_daily_budget=9, discovery_batch_size=3, discovery_max_queued=10)
    now = datetime.now(UTC)
    with Session(engine) as s, s.begin():
        assert queue_discovery(s, cfg, entries=entries, now=now) == 3
    spend(engine, [JobStatus.FAILED], "internal_error", now)
    spend(engine, [JobStatus.FAILED], "dns_nxdomain", now)
    spend(engine, [JobStatus.FAILED], "storage_error", now - timedelta(hours=2))
    with Session(engine) as s:
        assert infra_failures_last_hour(s, now) == 1


def test_a_share_of_each_batch_goes_to_restricted_hints(engine, clean):
    rows = ["GlobalRank,TldRank,Domain,TLD,RefSubNets"] + [f"{i},{i},site{i}.example.com,com,1" for i in range(1, 9)]
    rows += ["9,9,mega-casino.example.com,com,1", "10,10,bet365.example.com,com,1", "11,11,gunes-haber.example.com,com,1",
             "12,12,essex-news.example.com,com,1", "13,13,vapeshop.example.com,com,1"]
    entry = TOP | {"max_entries": None}
    with Session(engine) as s, s.begin():
        approved(s, "test-top", entry)
        sync(s, "test-top", data="\n".join(rows).encode(), entry=entry)
    batch = Settings(_env_file=None, discovery_daily_budget=100, discovery_batch_size=8, discovery_max_queued=100)
    with Session(engine) as s, s.begin():
        assert queue_discovery(s, batch, entries={"test-top": entry}) == 8
    names = {q[0] for q in queued(engine)}
    assert {"mega-casino.example.com", "bet365.example.com"} <= names  # 8 // 4 = 2 hinted, most popular first
    assert "vapeshop.example.com" not in names  # the third hint waits for the next batch
    assert "gunes-haber.example.com" not in names and "essex-news.example.com" not in names  # not hints
    assert {f"site{i}.example.com" for i in range(1, 7)} <= names  # the rest by popularity


def test_discovery_stops_at_the_daily_token_cap(engine, clean):
    from database.models import AIPrediction, ValidationStatus

    entries = {"test-top": TOP}
    with Session(engine) as s, s.begin():
        approved(s, "test-top", TOP)
        sync(s, "test-top", data=CSV, entry=TOP)
    capped = Settings(_env_file=None, discovery_daily_budget=10, discovery_batch_size=1, discovery_max_queued=10,
                      ai_daily_token_cap=10_000)

    def spent(tokens, provider="evren", when=None):
        with Session(engine) as s, s.begin():
            d = s.scalar(select(Domain).limit(1))
            s.add(AIPrediction(domain_id=d.id, provider=provider, model="m", prompt_version="1", taxonomy_version="1",
                               validation_status=ValidationStatus.VALID, input_tokens=tokens - 500, output_tokens=500,
                               **({"created_at": when} if when else {})))

    spent(9_000, when=datetime.now(UTC) - timedelta(days=1))  # yesterday: does not count
    spent(50_000, provider="mock")                              # the offline mock costs nothing
    spent(6_000)
    with Session(engine) as s, s.begin():
        assert queue_discovery(s, capped, entries=entries) == 1  # 6,000 of 10,000 used
    spent(4_000)
    with Session(engine) as s, s.begin():
        assert queue_discovery(s, capped, entries=entries) == 0  # cap reached: discovery waits for tomorrow
        assert queue_discovery(s, capped, entries=entries, now=datetime.now(UTC) + timedelta(days=1)) == 1



def test_a_cancelled_scan_is_not_a_scan(engine, clean):
    entries = {"test-top": TOP}
    with Session(engine) as s, s.begin():
        approved(s, "test-top", TOP)
        sync(s, "test-top", data=CSV, entry=TOP)
    every = Settings(_env_file=None, discovery_daily_budget=10, discovery_batch_size=3, discovery_max_queued=10)
    with Session(engine) as s, s.begin():
        assert queue_discovery(s, every, entries=entries) == 3
        s.execute(ScanJob.__table__.update().values(status=JobStatus.CANCELLED, error_code="demo_cleanup"))
    with Session(engine) as s, s.begin():
        assert queue_discovery(s, every, entries=entries) == 3  # never ran, so they come back
        s.execute(ScanJob.__table__.update().where(ScanJob.status == JobStatus.QUEUED).values(status=JobStatus.SUCCEEDED))
        assert queue_discovery(s, every, entries=entries) == 0  # really scanned now: once is enough
