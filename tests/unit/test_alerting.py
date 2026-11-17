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
                        "SchedulerNeverRan", "CrawlerOutage", "BackupStale", "FeedsStale",
                        "HostDiskFull"}  # a full system disk stops PostgreSQL: the outage just before it happens


def test_host_metrics_stay_private():
    """node-exporter shares the host's network, so it must listen only on the internal hostmetrics gateway."""
    compose = yaml.safe_load((ROOT / "docker-compose.yml").read_text())
    gateway = compose["networks"]["hostmetrics"]["ipam"]["config"][0]["gateway"]
    assert compose["networks"]["hostmetrics"]["internal"] is True
    node = compose["services"]["node-exporter"]
    assert node["network_mode"] == "host" and "ports" not in node
    assert f"--web.listen-address={gateway}:9101" in node["command"]
    assert node["read_only"] and node["cap_drop"] == ["ALL"] and node["volumes"] == ["/:/host:ro,rslave"]
    procs = compose["services"]["process-exporter"]
    assert procs["networks"] == ["hostmetrics"] and "ports" not in procs
    assert all(v.endswith(":ro") for v in procs["volumes"]) and "docker.sock" not in str(procs["volumes"])
    assert "hostmetrics" in compose["services"]["prometheus"]["networks"]
    jobs = {j["job_name"]: j["static_configs"][0]["targets"] for j in
            yaml.safe_load((ROOT / "prometheus/prometheus.yml").read_text())["scrape_configs"]}
    assert jobs["node"] == [f"{gateway}:9101"] and jobs["processes"] == ["process-exporter:9256"]
    groups = yaml.safe_load((ROOT / "prometheus/process-exporter.yml").read_text())["process_names"]
    assert groups[-1] == {"name": "{{.Comm}}", "cmdline": [".+"]}  # everything else under its own name


def test_docker_cleanup_only_removes_what_can_be_rebuilt():
    script = (ROOT / "scripts/docker-cleanup.sh").read_text()
    commands = [line.strip() for line in script.splitlines() if line.strip().startswith("docker ")]
    assert commands == ['docker builder prune -f --keep-storage "$KEEP_BUILD_CACHE" | tail -n 1',
                        "docker image prune -f | tail -n 1"]  # never volumes, containers, or tagged images (no -a)


def test_dashboards_load_and_use_the_provisioned_datasource():
    import json

    for path in (ROOT / "grafana/dashboards").glob("*.json"):
        dash = json.loads(path.read_text())
        assert dash["uid"] and dash["title"], path.name
        for panel in dash["panels"]:
            if panel["type"] != "row":
                assert panel["datasource"] == {"type": "prometheus", "uid": "prometheus"}, (path.name, panel["title"])
                assert panel["targets"] and all(t["expr"] for t in panel["targets"]), (path.name, panel["title"])


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
