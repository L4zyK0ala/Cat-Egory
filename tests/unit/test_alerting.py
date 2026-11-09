"""The alerting configuration: what is shipped must load and route as documented."""
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2] / "deployment"


def rules():
    return yaml.safe_load((ROOT / "prometheus/alerts.yml").read_text())["groups"][0]["rules"]


def test_every_alert_has_a_severity_and_a_summary():
    for rule in rules():
        assert rule["labels"]["severity"] in ("critical", "warning"), rule["alert"]
        assert rule["annotations"]["summary"], rule["alert"]


def test_only_outages_are_critical():
    critical = {r["alert"] for r in rules() if r["labels"]["severity"] == "critical"}
    assert critical == {"ApiDown", "DatabaseMetricsDown", "ApiErrors", "SchedulerStopped", "SchedulerNeverRan",
                        "CrawlerOutage", "BackupStale"}


def test_only_critical_alerts_reach_telegram():
    conf = yaml.safe_load((ROOT / "alertmanager/telegram.yml").read_text())
    assert conf["route"]["receiver"] == "nobody"
    assert [(r["matchers"], r["receiver"]) for r in conf["route"]["routes"]] == [(['severity="critical"'], "telegram")]
    telegram = next(r for r in conf["receivers"] if r["name"] == "telegram")["telegram_configs"][0]
    assert telegram["bot_token_file"].startswith("/tmp/") and "bot_token" not in telegram  # noqa: S108


def test_silent_config_has_no_receiver_settings():
    conf = yaml.safe_load((ROOT / "alertmanager/silent.yml").read_text())
    assert conf["receivers"] == [{"name": "nobody"}]
