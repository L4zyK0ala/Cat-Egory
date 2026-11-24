"""deployment/scripts/cve_watch.py: what is reported, once, and how the message reads (no Docker needed)."""
import importlib.util
import json
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "deployment" / "scripts" / "cve_watch.py"
spec = importlib.util.spec_from_file_location("cve_watch", SCRIPT)
cve = importlib.util.module_from_spec(spec)
sys.modules["cve_watch"] = cve  # dataclasses look the module up while the class is made
spec.loader.exec_module(cve)


def vuln(cve_id, sev, pkg="openssl", installed="3.0.1", fixed=""):
    return {"VulnerabilityID": cve_id, "Severity": sev, "PkgName": pkg, "InstalledVersion": installed,
            "FixedVersion": fixed}


REPORT = {"Results": [
    {"Target": "alpine", "Vulnerabilities": [vuln("CVE-1", "CRITICAL"), vuln("CVE-2", "HIGH", fixed="3.0.2"),
                                             vuln("CVE-3", "HIGH"), vuln("CVE-4", "MEDIUM", fixed="1")]},
    {"Target": "python-pkg", "Vulnerabilities": None},
]}


def test_critical_always_high_only_with_a_fix():
    found = cve.findings(REPORT, "redis:8.6.8-alpine")
    assert [f.cve for f in found] == ["CVE-1", "CVE-2"]
    assert cve.findings({"Results": None}, "x") == []


def test_short_image_names():
    assert cve.short("cat-egory/backend:dev") == "backend"
    assert cve.short("redis:8.6.8-alpine") == "redis"
    assert cve.short("0rickyy0/certstream-server-go:v1.10.1@sha256:abc") == "certstream-server-go"


def test_same_finding_in_two_images_is_one_line():
    found = cve.findings(REPORT, "cat-egory/backend:dev") + cve.findings(REPORT, "cat-egory/crawler:dev")
    groups = cve.grouped(found)
    assert len(groups) == 2 and groups[0][0].severity == "CRITICAL"
    assert groups[0][1] == ["backend", "crawler"]
    assert "CRITICAL CVE-1: openssl 3.0.1 (düzeltme yok) · backend, crawler" in cve.line(*groups[0])
    assert "(düzeltme: 3.0.2)" in cve.line(*groups[1])


def test_message_is_capped_and_says_where_the_rest_is():
    many = [cve.Finding(f"CVE-{i:03}", "HIGH", "pkg", "1", "2", "redis:8") for i in range(20)]
    text = cve.message(many, 25, first=False)
    assert text.startswith("🛡️ Cat-egory · 20 yeni güvenlik açığı (0 CRITICAL)")
    assert text.count("\n• ") == cve.MAX_LINES and "8 tane daha" in text and "last.txt" in text
    assert "ilk CVE taraması" in cve.message(many[:1], 1, first=True)


def test_ignore_file(tmp_path):
    f = tmp_path / "ignore.txt"
    assert cve.ignored(f) == {}
    f.write_text("# accepted\ncve-2026-1  # no fix, not reachable\n\nCVE-2026-2\n")
    assert cve.ignored(f) == {"CVE-2026-1": set(), "CVE-2026-2": set()}


def test_ignore_can_be_scoped_to_images(tmp_path):
    f = tmp_path / "ignore.txt"
    f.write_text("CVE-2026-6653 crawler  # only LLVM in Mesa uses it\nCVE-2026-7 Crawler backend\n"
                 "CVE-2026-8 grafana\nCVE-2026-8\n")
    rules = cve.ignored(f)
    assert rules == {"CVE-2026-6653": {"crawler"}, "CVE-2026-7": {"crawler", "backend"}, "CVE-2026-8": set()}

    def finding(cve_id, image):
        return cve.Finding(cve_id, "CRITICAL", "libxml2", "1", "", image)

    assert cve.accepted(finding("cve-2026-6653", "cat-egory/crawler:dev"), rules)
    assert not cve.accepted(finding("CVE-2026-6653", "cat-egory/postgres:18.7"), rules)
    assert cve.accepted(finding("CVE-2026-8", "redis:8.6.8-alpine"), rules)      # bare line: every image
    assert not cve.accepted(finding("CVE-2026-9", "cat-egory/crawler:dev"), rules)


def test_env_value_reads_one_line_and_strips_quotes(tmp_path):
    env = tmp_path / ".env"
    env.write_text('CATEGORY_ALERT_TELEGRAM_TOKEN="abc:def"\nCATEGORY_ALERT_TELEGRAM_CHAT_ID=-100\nOTHER=1')
    assert cve.env_value("CATEGORY_ALERT_TELEGRAM_TOKEN", env) == "abc:def"
    assert cve.env_value("CATEGORY_ALERT_TELEGRAM_CHAT_ID", env) == "-100"
    assert cve.env_value("MISSING", env) == ""


@pytest.fixture
def stack(tmp_path, monkeypatch):
    """main() with Docker, Trivy and Telegram replaced: two images, a fixed report, messages collected."""
    monkeypatch.setattr(cve, "STATE", tmp_path / "state")
    monkeypatch.setattr(cve, "IGNORE", tmp_path / "ignore.txt")
    monkeypatch.setattr(cve.os, "chdir", lambda _: None)
    monkeypatch.setattr(cve, "trivy", lambda *a, **k: "")
    monkeypatch.setattr(cve, "stack_images", lambda: ["cat-egory/backend:dev", "redis:8.6.8-alpine"])
    monkeypatch.setattr(cve, "image_id", lambda image: image.replace("/", "_").replace(":", "_"))
    reports = {"cat-egory/backend:dev": REPORT, "redis:8.6.8-alpine": {"Results": []}}
    monkeypatch.setattr(cve, "scan", lambda image: cve.findings(reports[image], image))
    sent, beats = [], []
    monkeypatch.setattr(cve, "send", sent.append)
    monkeypatch.setattr(cve, "heartbeat", lambda: beats.append(1))
    return reports, sent, beats


def test_first_run_summarises_then_only_new_findings_are_sent(stack, tmp_path):
    reports, sent, beats = stack
    assert cve.main([]) == 0
    assert len(sent) == 1 and "ilk CVE taraması: 2 açık (1 CRITICAL)" in sent[0] and beats == [1]
    assert "CVE-1" in (tmp_path / "state" / "last.txt").read_text()

    assert cve.main([]) == 0
    assert len(sent) == 1 and beats == [1, 1]  # nothing new: no message, still a heartbeat

    reports["redis:8.6.8-alpine"] = {"Results": [{"Vulnerabilities": [vuln("CVE-9", "CRITICAL", "redis", "8.6.8")]}]}
    assert cve.main([]) == 0
    assert len(sent) == 2 and "1 yeni güvenlik açığı (1 CRITICAL)" in sent[1] and "CVE-9" in sent[1]
    assert "CVE-1" not in sent[1]

    (tmp_path / "ignore.txt").write_text("CVE-10\n")
    reports["redis:8.6.8-alpine"]["Results"][0]["Vulnerabilities"].append(vuln("CVE-10", "CRITICAL", "redis", "8.6.8"))
    assert cve.main([]) == 0
    assert len(sent) == 2  # muted


def test_dry_run_sends_and_remembers_nothing(stack, tmp_path, capsys):
    _, sent, beats = stack
    assert cve.main(["--dry-run"]) == 0
    assert sent == [] and beats == [] and not (tmp_path / "state" / "seen.json").exists()
    assert "ilk CVE taraması" in capsys.readouterr().out


def test_failed_send_remembers_nothing(stack, tmp_path, monkeypatch):
    _, _, beats = stack

    def down(_text):
        raise RuntimeError("Telegram unreachable: TimeoutError")
    monkeypatch.setattr(cve, "send", down)
    assert cve.main([]) == 1
    assert beats == [] and not (tmp_path / "state" / "seen.json").exists()


def test_an_unscanned_image_keeps_its_findings_and_skips_the_heartbeat(stack, tmp_path, monkeypatch):
    reports, sent, beats = stack
    assert cve.main([]) == 0
    seen = json.loads((tmp_path / "state" / "seen.json").read_text())

    def broken(image):
        if image == "cat-egory/backend:dev":
            raise cve.subprocess.CalledProcessError(1, ["trivy"], stderr="FATAL boom")
        return cve.findings(reports[image], image)
    monkeypatch.setattr(cve, "scan", broken)
    assert cve.main([]) == 1
    assert beats == [1] and len(sent) == 1
    assert json.loads((tmp_path / "state" / "seen.json").read_text()) == seen
