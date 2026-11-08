import fakeredis
import redis

from backend.app import worker
from backend.app.metrics import HeartbeatCollector


class Task:
    name = "category.scan.dispatch"


def test_task_success_records_heartbeat(monkeypatch):
    fake = fakeredis.FakeRedis()
    monkeypatch.setattr(worker, "_heartbeat_client", lambda url: fake)
    monkeypatch.setattr(worker.time, "time", lambda: 1_800_000_000.7)
    worker.record_heartbeat(sender=Task())
    worker.record_heartbeat(sender=None)  # unknown sender: ignored
    assert fake.hgetall(worker.HEARTBEAT_KEY) == {b"category.scan.dispatch": b"1800000000"}


def test_redis_failure_never_breaks_a_task(monkeypatch):
    class Down:
        def hset(self, *a):
            raise redis.ConnectionError("down")

    monkeypatch.setattr(worker, "_heartbeat_client", lambda url: Down())
    worker.record_heartbeat(sender=Task())  # no exception


def test_collector_exports_heartbeats_and_health():
    fake = fakeredis.FakeRedis()
    fake.hset(worker.HEARTBEAT_KEY, mapping={"category.scan.dispatch": 100, "category.feeds.build": 50})
    families = {f.name: f for f in HeartbeatCollector(lambda: fake).collect()}
    samples = {s.labels["task"]: s.value for s in families["category_task_last_success_timestamp_seconds"].samples}
    assert samples == {"category.feeds.build": 50.0, "category.scan.dispatch": 100.0}
    assert families["category_heartbeats_up"].samples[0].value == 1

    def broken():
        raise redis.ConnectionError("down")

    families = {f.name: f for f in HeartbeatCollector(broken).collect()}
    assert list(families) == ["category_heartbeats_up"] and families["category_heartbeats_up"].samples[0].value == 0


def test_beat_schedule_is_persistent():
    from pathlib import Path

    import yaml

    compose = yaml.safe_load((Path(__file__).parents[2] / "deployment/docker-compose.yml").read_text())
    beat = compose["services"]["beat"]
    schedule = beat["command"][beat["command"].index("--schedule") + 1]
    assert schedule.startswith("/var/lib/celery-beat/") and "beatdata:/var/lib/celery-beat" in beat["volumes"]
