"""Page captures are deleted when their retention ends: the privacy notice promises it."""
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from backend.app.scan_tasks import purge_artifacts, store_result
from crawler.storage import MemoryStore
from database.models import ExtractedContent, Screenshot
from tests.integration.test_scan_pipeline import S, add, run_precheck, success_result

KEYS = ("scans/x/screenshot.png", "scans/x/page.html.gz", "scans/x/text.txt.gz")


def stored(client, engine, admin):
    add(client, admin, "ok.com")
    _, job_id = run_precheck(engine, "ok.com")
    with Session(engine) as s, s.begin():
        store_result(s, success_result(job_id), job_id, S)
    store = MemoryStore()
    for key in KEYS:
        store.put(key, b"x", "text/plain")
    return store


def rows(engine):
    with Session(engine) as s:
        return (s.scalar(select(func.count()).select_from(Screenshot)),
                s.scalar(select(func.count()).select_from(ExtractedContent)))


def test_nothing_is_deleted_before_its_time(client, engine, admin):
    store = stored(client, engine, admin)
    with Session(engine) as s, s.begin():
        assert purge_artifacts(s, store) == 0
    assert rows(engine) == (1, 1) and len(store.objects) == 3


def test_expired_captures_are_gone_from_database_and_storage(client, engine, admin):
    store = stored(client, engine, admin)
    past = datetime.now(UTC) - timedelta(days=1)
    with Session(engine) as s, s.begin():
        s.execute(update(Screenshot).values(retention_until=past))
        s.execute(update(ExtractedContent).values(retention_until=past))
    with Session(engine) as s, s.begin():
        assert purge_artifacts(s, store) == 2
    assert rows(engine) == (0, 0) and store.objects == {}
