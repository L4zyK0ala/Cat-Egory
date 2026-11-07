"""Crawler container self-test: ``python -m crawler.selftest``.

Checks (1) whether Chromium starts WITH its sandbox, (2) that the container has no direct internet route,
(3) that the egress proxy refuses an internal target and (4) allows a public site. Prints one line per check.
"""
from __future__ import annotations

import socket
import sys

import httpx

from common.config import get_settings


def main() -> int:
    settings = get_settings()
    failures = 0

    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        try:
            p.chromium.launch(chromium_sandbox=True).close()
            print("chromium sandbox      : OK (renderer processes are sandboxed)")
        except Exception as exc:  # noqa: BLE001
            print(f"chromium sandbox      : UNAVAILABLE ({str(exc).splitlines()[0][:120]})")
            try:
                p.chromium.launch(chromium_sandbox=False).close()
                print("chromium (no sandbox) : starts")
            except Exception as exc2:  # noqa: BLE001
                print(f"chromium (no sandbox) : FAILED ({str(exc2).splitlines()[0][:120]})")
                failures += 1

    try:
        socket.create_connection(("1.1.1.1", 443), timeout=3).close()
        print("direct internet       : REACHABLE  <-- isolation problem: crawler must only reach the proxy")
        failures += 1
    except OSError:
        print("direct internet       : blocked (good)")

    proxy = settings.crawler_proxy_url
    if not proxy:
        print("egress proxy          : CATEGORY_CRAWLER_PROXY_URL not set")
        return failures + 1
    with httpx.Client(proxy=proxy, timeout=10) as client:
        r = client.get("http://169.254.169.254/latest/meta-data/")
        ok = r.status_code == 403 and r.headers.get("x-category-blocked")
        print(f"proxy blocks metadata : {'OK' if ok else 'NOT BLOCKED'} ({r.status_code} {r.headers.get('x-category-blocked')})")
        failures += 0 if ok else 1
        try:
            r = client.get("https://example.com/")
            print(f"proxy allows public   : OK (example.com -> {r.status_code})")
        except httpx.HTTPError as exc:
            print(f"proxy allows public   : FAILED ({type(exc).__name__})")
            failures += 1
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
