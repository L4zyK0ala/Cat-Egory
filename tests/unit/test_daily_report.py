"""Sending the daily summary: once a day, never without settings, and the bot token never in an error."""
from contextlib import nullcontext
from datetime import UTC, datetime

import fakeredis
import httpx
import pytest
from celery.schedules import crontab
from pydantic import SecretStr

from backend.app import report_tasks, worker
from common.config import get_settings

TOKEN = "123456:secret-token"  # noqa: S105 - test value
NOW = datetime(2026, 11, 15, 6, 0, tzinfo=UTC)


@pytest.fixture
def configured(monkeypatch):
    settings = get_settings().model_copy(update={"alert_telegram_token": SecretStr(TOKEN), "alert_telegram_chat_id": "-100"})
    monkeypatch.setattr(report_tasks, "get_settings", lambda: settings)
    monkeypatch.setattr(report_tasks, "session_factory", lambda: lambda: nullcontext(None))
    monkeypatch.setattr(report_tasks, "build", lambda session, now, beats: "özet")
    monkeypatch.setattr(report_tasks, "read_heartbeats", dict)
    return settings


def telegram(status=200, sent=None):
    def handler(request):
        if sent is not None:
            sent.append(request)
        return httpx.Response(status, json={"ok": status == 200})
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_sent_once_a_day(configured):
    store, sent = fakeredis.FakeRedis(), []
    assert report_tasks.run_daily_report(NOW, client=telegram(sent=sent), store=store) == "sent"
    assert report_tasks.run_daily_report(NOW, client=telegram(sent=sent), store=store) == "already sent"
    assert len(sent) == 1
    assert sent[0].url.path == f"/bot{TOKEN}/sendMessage"
    assert b'"chat_id":"-100"' in sent[0].content.replace(b" ", b"") and "özet".encode() in sent[0].content


def test_a_failed_send_can_be_retried_and_hides_the_token(configured):
    store = fakeredis.FakeRedis()
    with pytest.raises(report_tasks.TelegramError) as err:
        report_tasks.run_daily_report(NOW, client=telegram(status=401), store=store)
    assert TOKEN not in str(err.value) and err.value.__cause__ is None
    assert report_tasks.run_daily_report(NOW, client=telegram(), store=store) == "sent"

    def down(request):
        raise httpx.ConnectError(f"cannot reach {request.url}")

    with pytest.raises(report_tasks.TelegramError) as err:
        report_tasks.send_telegram("x", TOKEN, "-100", httpx.Client(transport=httpx.MockTransport(down)))
    assert TOKEN not in str(err.value) and err.value.__cause__ is None and err.value.__suppress_context__


def test_nothing_is_sent_without_settings(monkeypatch):
    for update in ({"alert_telegram_token": None, "alert_telegram_chat_id": "-100"},
                   {"alert_telegram_token": SecretStr(TOKEN), "alert_telegram_chat_id": None},
                   {"alert_telegram_token": SecretStr(TOKEN), "alert_telegram_chat_id": "-100", "daily_report_enabled": False}):
        settings = get_settings().model_copy(update=update)
        monkeypatch.setattr(report_tasks, "get_settings", lambda s=settings: s)
        assert report_tasks.run_daily_report(NOW, client=telegram(status=500), store=fakeredis.FakeRedis()) == "off"


def test_scheduled_at_nine_turkiye_time_on_the_main_worker():
    entry = worker.celery_app.conf.beat_schedule["daily-report"]
    assert entry["task"] == "category.maintenance.daily_report"
    assert entry["schedule"] == crontab(minute=0, hour=6)  # beat runs in UTC; 09:00 GMT+3
    route = worker.celery_app.amqp.router.route({}, entry["task"])
    assert route["queue"].name == "maintenance"  # the worker with the token, not worker-scan or worker-fast


def test_numbers_read_the_turkish_way():
    assert report_tasks.num(21432) == "21.432"
    assert report_tasks.tokens(6_100_000) == "6,1 milyon" and report_tasks.tokens(850_400) == "850 bin"
    assert report_tasks.share(1, 4) == "%25" and report_tasks.share(1, 0) == "—"
    assert report_tasks.reason_label("lookalike_of:garanti.com.tr") == "marka taklidi"
