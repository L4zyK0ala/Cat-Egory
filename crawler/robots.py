"""robots.txt handling (RFC 9309).

* 2xx  → parse and obey the rules for our user-agent token
* 4xx  → no restrictions (RFC 9309 §2.3.1.3)
* 5xx / network error → treat as complete disallow (§2.3.1.4); the scan is retried later
All fetches go through the egress proxy like every other request the crawler makes.
"""
from __future__ import annotations

from dataclasses import dataclass
from urllib.robotparser import RobotFileParser

import httpx

from .egress_proxy import proxy_block_code
from .result import PROXY_NETWORK_CODES

MAX_ROBOTS_BYTES = 512 * 1024


@dataclass(frozen=True, slots=True)
class RobotsDecision:
    allowed: bool
    reason: str  # parsed | not_found | unavailable | proxy:<code>
    url: str | None = None


def fetch_robots(domain: str, *, agent: str, proxy_url: str | None, proxy_token: str | None = None,
                 timeout: float = 8.0, transport: httpx.BaseTransport | None = None, path: str = "/") -> RobotsDecision:
    """Whether our agent may fetch ``path`` (the home page unless a one-page scan names another) on ``domain``."""
    last_error = "unavailable"
    for scheme in ("https", "http"):
        url = f"{scheme}://{domain}/robots.txt"
        try:
            with httpx.Client(proxy=proxy_url, timeout=timeout, follow_redirects=True, max_redirects=5,
                              transport=transport, headers={"User-Agent": agent}) as client:
                with client.stream("GET", url) as resp:
                    if code := proxy_block_code(resp.headers, proxy_token):  # the proxy answered, not the site
                        if code in PROXY_NETWORK_CODES:
                            continue
                        return RobotsDecision(False, f"proxy:{code}", url)
                    if 400 <= resp.status_code < 500:
                        return RobotsDecision(True, "not_found", url)
                    if resp.status_code >= 500:
                        last_error = "unavailable"
                        continue
                    body = b""
                    for chunk in resp.iter_bytes():
                        body += chunk
                        if len(body) > MAX_ROBOTS_BYTES:  # RFC 9309: parse at least 500 KiB, ignore the rest
                            break
        except httpx.HTTPError:
            continue
        parser = RobotFileParser()
        parser.parse(body[:MAX_ROBOTS_BYTES].decode("utf-8", errors="replace").splitlines())
        token = agent.split("/", 1)[0]
        return RobotsDecision(parser.can_fetch(token, f"{scheme}://{domain}{path}"), "parsed", url)
    return RobotsDecision(False, last_error)
