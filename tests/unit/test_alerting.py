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
    assert critical == {"ApiDown", "PublicApiDown", "SiteDown", "DatabaseMetricsDown", "ApiErrors", "SchedulerStopped",
                        "SchedulerNeverRan", "CrawlerOutage", "BackupStale", "FeedsStale"}


def test_what_visitors_use_is_watched():
    """The public API and the public site each have a scrape job, and an alert on that job being down."""
    jobs = {j["job_name"]: j for j in
            yaml.safe_load((ROOT / "prometheus/prometheus.yml").read_text())["scrape_configs"]}
    assert jobs["cat-egory-public-api"]["static_configs"][0]["targets"] == ["api:9100"]
    assert jobs["cat-egory-site"]["metrics_path"] == "/healthz"
    watched = {r["alert"]: r["expr"] for r in rules()}
    assert 'job="cat-egory-public-api"' in watched["PublicApiDown"] and 'job="cat-egory-site"' in watched["SiteDown"]
    compose = yaml.safe_load((ROOT / "docker-compose.yml").read_text())
    assert compose["services"]["api"]["environment"]["CATEGORY_METRICS_PORT"] == "9100"
    assert "9100" not in str(compose["services"]["api"]["ports"])  # never published


def test_only_critical_alerts_reach_telegram():
    conf = yaml.safe_load((ROOT / "alertmanager/telegram.yml").read_text())
    assert conf["route"]["receiver"] == "nobody"
    assert [(r["matchers"], r["receiver"]) for r in conf["route"]["routes"]] == [(['severity="critical"'], "telegram")]
    telegram = next(r for r in conf["receivers"] if r["name"] == "telegram")["telegram_configs"][0]
    assert telegram["bot_token_file"].startswith("/tmp/") and "bot_token" not in telegram  # noqa: S108


def test_telegram_messages_are_in_turkish():
    for rule in rules():
        text = rule["annotations"].get("telegram", "")
        assert text and text != rule["annotations"]["summary"], rule["alert"]
    conf = yaml.safe_load((ROOT / "alertmanager/telegram.yml").read_text())
    message = next(r for r in conf["receivers"] if r["name"] == "telegram")["telegram_configs"][0]["message"]
    assert "or .Annotations.telegram .Annotations.summary" in message
    assert "sorun var" in message and "düzeldi" in message


def test_silent_config_has_no_receiver_settings():
    conf = yaml.safe_load((ROOT / "alertmanager/silent.yml").read_text())
    assert conf["receivers"] == [{"name": "nobody"}]
