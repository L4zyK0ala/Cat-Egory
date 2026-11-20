"""Crawler container self-test: ``python -m crawler.selftest``.

Checks (1) whether Chromium starts WITH its sandbox, (2) that the container has no direct internet route,
(3) that the egress proxy refuses an internal target and (4) allows a public site, and that its credentials are
the narrow ones: (5) its Redis user cannot read the core's keys and (6) its object-store key can write an
artefact but not read or list them. Prints one line per check.
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
    return 1 if (failures + credentials(settings)) else 0


def credentials(settings) -> int:
    """The crawler's Redis user and object-store key must be the narrow ones (deployment/redis/crawler.acl,
    the storage service's "crawler" identity). Returns the number of failed checks."""
    import redis

    failures = 0
    client = redis.Redis.from_url(settings.redis_url, socket_timeout=5)
    try:
        client.keys("category:*")
        print("redis: core keys      : READABLE  <-- the crawler must use its own Redis user")
        failures += 1
    except redis.exceptions.NoPermissionError:
        print("redis: core keys      : refused (good)")
    except redis.RedisError as exc:
        print(f"redis: core keys      : ERROR ({type(exc).__name__})")
        failures += 1

    from botocore.exceptions import BotoCoreError, ClientError

    from .storage import S3Store

    store = S3Store(settings)
    key = "selftest/crawler-write-check.txt"
    try:
        store.put(key, b"ok", "text/plain")
        print("s3: write artefact    : OK")
    except Exception as exc:  # noqa: BLE001 - StorageError or a client error: report and go on
        print(f"s3: write artefact    : FAILED ({exc})")
        failures += 1
    for label, call in (("read artefact", lambda: store.get(key)),
                        ("list bucket", lambda: store.client.list_objects_v2(Bucket=store.bucket, MaxKeys=1))):
        try:
            call()
            print(f"s3: {label:<17}: ALLOWED  <-- the crawler's key must be write-only")
            failures += 1
        except ClientError as exc:
            code = exc.response.get("Error", {}).get("Code", "")
            print(f"s3: {label:<17}: refused (good: {code})")
        except BotoCoreError as exc:
            print(f"s3: {label:<17}: ERROR ({type(exc).__name__})")
            failures += 1
    return failures


if __name__ == "__main__":
    sys.exit(main())
