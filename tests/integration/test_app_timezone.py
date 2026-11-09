"""The dashboard's day follows Türkiye time; stored times stay UTC."""
from datetime import UTC, datetime, timedelta

from sqlalchemy import update
from sqlalchemy.orm import Session

from backend.app.scan_tasks import store_result
from common.timeutil import local_midnight
from database.models import ScanJob
from tests.integration.test_scan_pipeline import S, add, run_precheck, success_result


def test_a_scan_just_after_midnight_counts_for_the_new_turkish_day(client, engine, admin):
    add(client, admin, "ok.com")
    _, job_id = run_precheck(engine, "ok.com")
    with Session(engine) as s, s.begin():
        store_result(s, success_result(job_id), job_id, S)
    midnight = local_midnight(datetime.now(UTC), 3)
    with Session(engine) as s, s.begin():  # 00:01 in Türkiye is still yesterday in UTC
        s.execute(update(ScanJob).values(finished_at=midnight + timedelta(minutes=1)))
    stats = client.get("/api/v1/stats").json()
    assert midnight.date().isoformat() in stats["daily_scans"]
    assert stats["domains_scanned_today"] == 1
