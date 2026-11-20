"""The crawler's Redis user (deployment/redis/crawler.acl), started the way compose starts Redis.

The crawler opens hostile pages. If one escaped Chromium's sandbox, this user must still let the crawler do its job
(take crawl jobs, hand results to the core) and nothing else: no reading the core's keys, no other queues, no
admin commands. Needs a local ``redis-server``; skipped without one."""
import os
import shutil
import socket
import subprocess
import sys
import textwrap
import time
from pathlib import Path

import pytest

redis = pytest.importorskip("redis")
ROOT = Path(__file__).resolve().parents[2]
ACL = ROOT / "deployment/redis/crawler.acl"
pytestmark = pytest.mark.skipif(shutil.which("redis-server") is None, reason="no local redis-server")


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="module")
def server(tmp_path_factory):
    port = free_port()
    data = tmp_path_factory.mktemp("redis")
    # the same shell line as the compose service (set -f; rules read from the file), plus a local port
    line = (f'set -f; exec redis-server --port {port} --bind 127.0.0.1 --dir {data} --save "" '
            f'--requirepass corepw --user crawler on ">crawlerpw" $(cat {ACL})')
    proc = subprocess.Popen(["sh", "-c", line], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    core = redis.Redis(port=port, password="corepw")
    for _ in range(50):
        try:
            core.ping()
            break
        except redis.ConnectionError:
            time.sleep(0.1)
    yield port, core
    proc.terminate()
    proc.wait(10)


def test_compose_reads_the_same_rules():
    compose = (ROOT / "deployment/docker-compose.yml").read_text()
    assert "set -f; exec redis-server" in compose and "$$(cat /etc/redis/crawler.acl)" in compose
    assert "./redis/crawler.acl:/etc/redis/crawler.acl:ro" in compose
    assert "redis://crawler:${REDIS_CRAWLER_PASSWORD" in compose
    crawler = compose.split("\n  crawler:\n", 1)[1].split("\n  beat:\n", 1)[0]
    assert "REDIS_PASSWORD:" not in crawler.replace("REDIS_CRAWLER_PASSWORD", "")  # never the core's password
    assert "${S3_ACCESS_KEY" not in crawler and "${S3_CRAWLER_ACCESS_KEY" in crawler
    assert '"actions":["Write:category-artifacts"]' in compose


def test_the_crawler_user_cannot_reach_the_core(server):
    port, core = server
    core.set("category:secret", "x")
    core.lpush("maintenance", "job")
    crawler = redis.Redis(port=port, username="crawler", password="crawlerpw")
    crawler.lpush("crawl", "ok")                     # its own queues
    assert crawler.rpop("crawl") == b"ok"
    crawler.lpush("scan", "result")                  # results go to the core's queues, write only
    for deny in (lambda: crawler.get("category:secret"), lambda: crawler.keys("*"),
                 lambda: crawler.lrange("scan", 0, -1), lambda: crawler.rpop("scan"),
                 lambda: crawler.lpush("maintenance", "evil"), lambda: crawler.flushall(),
                 lambda: crawler.config_get("*"), lambda: crawler.hgetall("category:task-heartbeat")):
        with pytest.raises(redis.exceptions.NoPermissionError):
            deny()
    assert core.llen("maintenance") == 1 and core.lrange("scan", 0, -1) == [b"result"]
    with pytest.raises(redis.exceptions.AuthenticationError):
        redis.Redis(port=port, username="crawler", password="corepw").ping()


def test_a_crawler_worker_takes_jobs_and_hands_results_on(server, tmp_path):
    """A real Celery worker with the crawler's settings and start flags, a stub crawl task instead of a browser."""
    from celery import Celery, signature

    port, core = server
    core.flushall()
    core.acl_log_reset()
    (tmp_path / "fake_crawler.py").write_text(textwrap.dedent('''
        from backend.app.worker import celery_app

        @celery_app.task(name="category.crawl.run", ignore_result=True)
        def run(payload):
            return {"domain": payload["domain"]}
    '''))
    env = dict(os.environ, CATEGORY_REDIS_URL=f"redis://crawler:crawlerpw@127.0.0.1:{port}/0",
               PYTHONPATH=f"{tmp_path}{os.pathsep}{ROOT}")
    dockerfile = (ROOT / "deployment/docker/Dockerfile.crawler").read_text()
    flags = [f for f in ("--without-gossip", "--without-mingle", "--without-heartbeat") if f in dockerfile]
    assert len(flags) == 3
    worker = subprocess.Popen([sys.executable, "-m", "celery", "-A", "fake_crawler:celery_app", "worker",
                               "-Q", "crawl-fast,crawl", "--concurrency", "1", "--loglevel", "WARNING", *flags],
                              env=env, cwd=tmp_path, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    try:
        app = Celery("core", broker=f"redis://:corepw@127.0.0.1:{port}/0")
        app.conf.update(task_serializer="json", accept_content=["json"])
        for queue, back in (("crawl", "scan"), ("crawl-fast", "fast")):
            app.send_task("category.crawl.run", args=[{"domain": f"{queue}.example"}], queue=queue,
                          link=signature("category.scan.persist", args=["job"], queue=back))
        for _ in range(150):
            if core.llen("scan") and core.llen("fast"):
                break
            time.sleep(0.1)
        assert (core.llen("scan"), core.llen("fast")) == (1, 1)
        assert core.acl_log() == []  # nothing it needed was refused
    finally:
        worker.terminate()
        out = worker.communicate(timeout=30)[0]
    assert "NoPermission" not in out, out[-2000:]


def test_selftest_reports_wide_credentials(monkeypatch, capsys):
    """crawler.selftest fails when the crawler holds the core's Redis user or a key that can read artefacts."""
    from botocore.exceptions import ClientError

    from crawler import selftest, storage

    class Narrow:
        def keys(self, _):
            raise redis.exceptions.NoPermissionError("no")

    class Wide:
        def keys(self, _):
            return []

    class Store:
        bucket = "b"

        def __init__(self, settings, readable):
            self.readable = readable
            self.client = self

        def put(self, *a, **k):
            return None

        def get(self, key):
            if not self.readable:
                raise ClientError({"Error": {"Code": "AccessDenied"}}, "GetObject")
            return b"ok"

        def list_objects_v2(self, **k):
            if not self.readable:
                raise ClientError({"Error": {"Code": "AccessDenied"}}, "ListObjectsV2")
            return {}

    from types import SimpleNamespace
    settings = SimpleNamespace(redis_url="redis://x")
    monkeypatch.setattr(redis.Redis, "from_url", classmethod(lambda cls, *a, **k: Narrow()))
    monkeypatch.setattr(storage, "S3Store", lambda s: Store(s, readable=False))
    assert selftest.credentials(settings) == 0
    assert "core keys      : refused (good)" in capsys.readouterr().out
    monkeypatch.setattr(redis.Redis, "from_url", classmethod(lambda cls, *a, **k: Wide()))
    monkeypatch.setattr(storage, "S3Store", lambda s: Store(s, readable=True))
    assert selftest.credentials(settings) == 3
    out = capsys.readouterr().out
    assert "READABLE" in out and out.count("ALLOWED") == 2
