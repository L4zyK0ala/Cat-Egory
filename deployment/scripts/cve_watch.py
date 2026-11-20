#!/usr/bin/env python3
"""Daily CVE watch over every image the stack runs; new findings go to Telegram (run as root from the repository).

    sudo python3 deployment/scripts/cve_watch.py             # scan, send what is new, remember it
    sudo python3 deployment/scripts/cve_watch.py --dry-run   # scan and print the message; send and remember nothing

Installed as a daily timer by ``make cve-timer``. How it works:

* The images are the ones the compose project's containers use (ours and third-party ones alike).
* Each image is read once into a package list (CycloneDX SBOM, kept per image ID under ``STATE``); after that the
  daily scan only compares those lists with Trivy's fresh vulnerability database, so no image is exported again
  until it changes. Trivy runs in its own container, pinned by digest, without the Docker socket and without
  capabilities; it sees an exported image tar (read-only) or a package list, never the running stack.
* Reported: every CRITICAL finding and every HIGH one that has a fixed version. A finding (CVE, package, installed
  version) is sent once; ``/etc/cat-egory/cve-ignore.txt`` (one CVE ID per line, ``#`` comments) mutes accepted ones.
* Nothing new means no message. The Telegram bot and chat are the alert ones (CATEGORY_ALERT_TELEGRAM_TOKEN /
  _CHAT_ID in ``.env``); they are read here and never printed.
* A successful run records the ``security.cve_scan`` heartbeat: the daily summary shows it and CveScanStale fires
  after three days without one.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import shutil
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path

TRIVY = "aquasec/trivy:0.76.2@sha256:8f6bb947dbc528902e11f2f5be52f7b519f6a39d5d148f41166f2040499768fb"
CACHE_VOLUME = "cat-egory-trivy-cache"   # vulnerability database, refreshed on every run
STATE = Path("/var/lib/cat-egory/cve-watch")
IGNORE = Path("/etc/cat-egory/cve-ignore.txt")
DC = ["docker", "compose", "-f", "deployment/docker-compose.yml", "--env-file", ".env"]
HEARTBEAT = "security.cve_scan"
MAX_LINES = 12                           # per Telegram message; the rest is in STATE/last.txt
RANK = {"CRITICAL": 0, "HIGH": 1}


@dataclass(frozen=True)
class Finding:
    cve: str
    severity: str
    package: str
    installed: str
    fixed: str
    image: str

    @property
    def key(self) -> str:
        return f"{self.cve}|{self.package}|{self.installed}"


def run(cmd: list[str], **kw) -> subprocess.CompletedProcess:
    # argument lists, no shell; image names come from our own compose project
    return subprocess.run(cmd, check=True, stdin=subprocess.DEVNULL, capture_output=True, text=True, **kw)  # noqa: S603


def trivy(args: list[str], mounts: dict[str, str] | None = None) -> str:
    cmd = ["docker", "run", "--rm", "--cap-drop", "ALL", "--security-opt", "no-new-privileges",
           "-v", f"{CACHE_VOLUME}:/root/.cache/trivy"]
    for host, inside in (mounts or {}).items():
        cmd += ["-v", f"{host}:{inside}:ro"]
    return run([*cmd, TRIVY, *args]).stdout


def stack_images() -> list[str]:
    out = run([*DC, "ps", "-a", "--format", "{{.Image}}"]).stdout
    return sorted({line.strip() for line in out.splitlines() if line.strip()})


def image_id(image: str) -> str:
    return run(["docker", "image", "inspect", "-f", "{{.Id}}", image]).stdout.strip().removeprefix("sha256:")


def sbom(image: str) -> Path:
    """The image's package list, made once per image ID."""
    folder = STATE / "sbom"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{image_id(image)}.json"
    if not path.exists():
        tmp = Path(tempfile.mkdtemp(dir="/var/tmp", prefix="cve-watch-"))
        try:
            run(["docker", "save", "-o", str(tmp / "image.tar"), image])
            data = trivy(["image", "--input", "/scan/image.tar", "--format", "cyclonedx", "--quiet",
                          "--skip-db-update"], {str(tmp): "/scan"})
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
        path.write_text(data)
    return path


def findings(report: dict, image: str) -> list[Finding]:
    """CRITICAL findings, and HIGH ones with a fix, from one Trivy JSON report."""
    out = []
    for result in report.get("Results") or []:
        for v in result.get("Vulnerabilities") or []:
            sev, fixed = v.get("Severity", ""), v.get("FixedVersion") or ""
            if sev == "CRITICAL" or (sev == "HIGH" and fixed):
                out.append(Finding(v.get("VulnerabilityID", "?"), sev, v.get("PkgName", "?"),
                                   v.get("InstalledVersion", "?"), fixed, image))
    return out


def scan(image: str) -> list[Finding]:
    path = sbom(image)
    raw = trivy(["sbom", "--format", "json", "--quiet", "--skip-db-update", "--scanners", "vuln",
                 "--severity", "HIGH,CRITICAL", f"/sbom/{path.name}"], {str(path.parent): "/sbom"})
    return findings(json.loads(raw), image)


def ignored(path: Path | None = None) -> set[str]:
    path = path or IGNORE
    if not path.exists():
        return set()
    return {line.split("#", 1)[0].strip().upper() for line in path.read_text().splitlines()} - {""}


def short(image: str) -> str:
    """cat-egory/backend:dev -> backend, redis:8.6.8-alpine -> redis, a/b:1@sha256:... -> b."""
    return image.split("@")[0].rsplit("/", 1)[-1].split(":")[0]


def grouped(items: list[Finding]) -> list[tuple[Finding, list[str]]]:
    by_key: dict[str, tuple[Finding, list[str]]] = {}
    for f in items:
        first, images = by_key.setdefault(f.key, (f, []))
        if short(f.image) not in images:
            images.append(short(f.image))
    return sorted(by_key.values(), key=lambda pair: (RANK.get(pair[0].severity, 9), pair[0].cve, pair[0].package))


def line(f: Finding, images: list[str]) -> str:
    fix = f"düzeltme: {f.fixed}" if f.fixed else "düzeltme yok"
    return f"• {f.severity} {f.cve}: {f.package} {f.installed} ({fix}) · {', '.join(sorted(images))}"


def message(new: list[Finding], total: int, *, first: bool) -> str:
    groups = grouped(new)
    crit = sum(1 for f, _ in groups if f.severity == "CRITICAL")
    head = (f"🛡️ Cat-egory · ilk CVE taraması: {len(groups)} açık ({crit} CRITICAL)" if first
            else f"🛡️ Cat-egory · {len(groups)} yeni güvenlik açığı ({crit} CRITICAL)")
    lines = [head, *(line(f, imgs) for f, imgs in groups[:MAX_LINES])]
    if len(groups) > MAX_LINES:
        lines.append(f"… ve {len(groups) - MAX_LINES} tane daha.")
    lines += ["", f"Toplam bilinen: {total}. Liste: sudo cat {STATE}/last.txt",
              "Ne yapılacak: düzeltmesi olan imajı/paketi güncelle, make up. "
              f"Kabul edilenler: {IGNORE}"]
    return "\n".join(lines)


def env_value(name: str, path: Path = Path(".env")) -> str:
    for raw in path.read_text().splitlines():
        if raw.startswith(f"{name}="):
            return raw.split("=", 1)[1].strip().strip("'\"")
    return ""


def send(text: str) -> None:
    token, chat = env_value("CATEGORY_ALERT_TELEGRAM_TOKEN"), env_value("CATEGORY_ALERT_TELEGRAM_CHAT_ID")
    if not token or not chat:
        raise RuntimeError("CATEGORY_ALERT_TELEGRAM_TOKEN / _CHAT_ID are not set in .env")
    body = json.dumps({"chat_id": chat, "text": text, "disable_web_page_preview": True}).encode()
    req = urllib.request.Request(f"https://api.telegram.org/bot{token}/sendMessage", data=body,
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:  # noqa: S310 (fixed https URL)
            if r.status != 200:
                raise RuntimeError(f"Telegram answered {r.status}")
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"Telegram answered {exc.code}") from None  # never the URL with the token
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Telegram unreachable: {type(exc.reason).__name__}") from None


def heartbeat() -> None:
    run([*DC, "exec", "-T", "redis", "sh", "-c",
         f'REDISCLI_AUTH="$REDIS_PASSWORD" redis-cli hset category:task-heartbeat {HEARTBEAT} "$(date +%s)"'])


def prune_sboms(keep: set[str]) -> None:
    for path in (STATE / "sbom").glob("*.json"):
        if path.stem not in keep:
            path.unlink()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("--dry-run", action="store_true", help="print the message; send and remember nothing")
    args = parser.parse_args(argv)
    os.chdir(Path(__file__).resolve().parents[2])
    STATE.mkdir(parents=True, exist_ok=True)
    os.chmod(STATE, 0o700)

    trivy(["image", "--download-db-only", "--quiet"])
    images, found, failed = stack_images(), [], []
    for image in images:
        try:
            found += scan(image)
        except (subprocess.CalledProcessError, json.JSONDecodeError) as exc:
            failed.append(image)
            detail = exc.stderr.strip().splitlines()[-1:] if isinstance(exc, subprocess.CalledProcessError) else []
            print(f"could not scan {image}: {' '.join(detail) or type(exc).__name__}", file=sys.stderr)
    skip = ignored()
    found = [f for f in found if f.cve.upper() not in skip]

    seen_file = STATE / "seen.json"
    first = not seen_file.exists()
    seen: dict[str, str] = {} if first else json.loads(seen_file.read_text())
    new = [f for f in found if f.key not in seen]
    total = len(grouped(found))
    report = "\n".join(line(f, imgs) for f, imgs in grouped(found)) or "(nothing)"
    print(f"{len(images)} images, {total} findings, {len(grouped(new))} new"
          + (f", {len(failed)} could not be scanned: {', '.join(failed)}" if failed else ""))

    text = message(new, total, first=first) if new else ""
    if failed and text:
        text += f"\nTaranamayan imaj: {', '.join(short(i) for i in failed)}"
    if args.dry_run:
        print(text or "(no message: nothing new)")
        print(report)
        return 0
    if text:
        try:
            send(text)
        except RuntimeError as exc:
            print(f"not sent: {exc}; nothing remembered, tomorrow's run sends it again", file=sys.stderr)
            return 1
    today = dt.date.today().isoformat()
    remembered = {f.key: seen.get(f.key, today) for f in found}
    if failed:  # keep what the unscanned images had, or it would be reported again once they scan
        remembered = {**seen, **remembered}
    seen_file.write_text(json.dumps(remembered, indent=0, sort_keys=True))
    (STATE / "last.txt").write_text(f"{dt.datetime.now():%Y-%m-%d %H:%M} · {len(images)} images\n{report}\n")
    try:
        prune_sboms({image_id(i) for i in images})
    except subprocess.CalledProcessError:
        pass  # an image vanished mid-run; its list is pruned next time
    if failed:
        return 1  # no heartbeat: a scan that keeps failing ends in CveScanStale
    heartbeat()
    return 0


if __name__ == "__main__":
    sys.exit(main())
