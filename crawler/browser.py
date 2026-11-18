"""Visit a website in Chromium (Playwright) and capture what the classifier needs.

Isolation (see documentation/ARCHITECTURE.md §5.2-5.3):
* every request goes through the SSRF egress proxy (``--proxy-server`` with loopback NOT bypassed);
  in Docker the crawler container has no other route out
* QUIC and non-proxied WebRTC UDP are disabled so nothing can skip the proxy
* downloads are refused, service workers blocked, non-http(s) requests aborted, request count capped
* a fresh browser per job; the profile lives in a throw-away temp directory
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import Route, sync_playwright
from playwright.sync_api import TimeoutError as PlaywrightTimeout

from common.config import Settings

from .egress_proxy import proxy_block_code
from .result import PROXY_NETWORK_CODES
from .ssrf import BlockedError, check_url

log = logging.getLogger("category.crawler")

CHROME_ARGS = [
    "--disable-quic",
    "--force-webrtc-ip-handling-policy=disable_non_proxied_udp",
    "--disable-background-networking",
    "--disable-component-update",
    "--disable-default-apps",
    "--disable-sync",
    "--disable-dev-shm-usage",
    "--no-pings",
    "--no-first-run",
    "--disable-features=DnsOverHttps,InterestFeedContentSuggestions,Translate,MediaRouter",
]
KEPT_HEADERS = {"server", "content-type", "content-language", "content-length", "x-powered-by", "location",
                "strict-transport-security", "x-frame-options", "via", "cf-ray"}
METADATA_JS = """() => {
  const meta = (sel) => { const el = document.querySelector(sel); return el ? el.getAttribute('content') : null; };
  const og = {};
  document.querySelectorAll('meta[property^="og:"], meta[name^="twitter:"]').forEach(el => {
    const k = el.getAttribute('property') || el.getAttribute('name');
    if (k && Object.keys(og).length < 30) og[k] = (el.getAttribute('content') || '').slice(0, 500);
  });
  return {
    title: document.title || null,
    description: meta('meta[name="description" i]') || meta('meta[property="og:description"]'),
    lang: document.documentElement.getAttribute('lang'),
    og: og,
    links: document.querySelectorAll('a[href]').length,
    text: document.body ? document.body.innerText : '',
  };
}"""


class SandboxUnavailable(RuntimeError):
    pass


@dataclass
class PageCapture:
    requested_url: str
    final_url: str | None = None
    http_status: int | None = None
    redirect_chain: list[dict[str, Any]] = field(default_factory=list)
    response_headers: dict[str, str] = field(default_factory=dict)
    tls: dict[str, Any] | None = None
    title: str | None = None
    description: str | None = None
    language: str | None = None
    open_graph: dict[str, str] = field(default_factory=dict)
    link_count: int | None = None
    text: str = ""
    html: bytes = b""
    screenshot: bytes | None = None
    blocked_reason: str | None = None
    error_code: str | None = None
    error_detail: str | None = None
    sandboxed: bool | None = None
    requests_aborted: int = 0
    navigation_ms: int = 0


def classify_error(message: str) -> str:
    m = message.upper()
    if "TIMEOUT" in m:
        return "timeout"
    if "ERR_CERT" in m or "SSL" in m:
        return "tls_error"
    if "ERR_TUNNEL_CONNECTION_FAILED" in m or "ERR_PROXY" in m:
        return "connection_failed"  # proxy refused (blocked) or upstream unreachable
    if "ERR_NAME_NOT_RESOLVED" in m:
        return "dns_failure"
    if "ERR_CONNECTION" in m or "ERR_ADDRESS" in m or "ERR_EMPTY_RESPONSE" in m or "ERR_SOCKET" in m:
        return "connection_failed"
    if "DOWNLOAD" in m:
        return "download_refused"
    if "ERR_ABORTED" in m:
        return "aborted"  # e.g. the URL triggered a download, which we refuse
    if "ERR_TOO_MANY_REDIRECTS" in m:
        return "too_many_redirects"
    if "TARGET" in m and "CLOSED" in m:
        return "browser_crashed"
    return "navigation_error"


def _launch(p, settings: Settings):
    proxy = {"server": settings.crawler_proxy_url, "bypass": "<-loopback>"} if settings.crawler_proxy_url else None
    if settings.crawler_chromium_sandbox:
        try:
            return p.chromium.launch(chromium_sandbox=True, proxy=proxy, args=CHROME_ARGS), True
        except PlaywrightError as exc:
            if not settings.crawler_allow_no_sandbox:
                raise SandboxUnavailable(
                    "Chromium sandbox could not start; set CATEGORY_CRAWLER_ALLOW_NO_SANDBOX=true to accept "
                    "container-only isolation") from exc
            log.error("chromium sandbox unavailable, continuing without it", extra={"error": str(exc)[:200]})
    return p.chromium.launch(chromium_sandbox=False, proxy=proxy, args=CHROME_ARGS), False


def _chain(response) -> list[dict[str, Any]]:
    """Server-side redirect chain of the main navigation, first request to last, with statuses."""
    requests = []
    req = response.request
    while req is not None:
        requests.append(req)
        req = req.redirected_from
    requests.reverse()
    hops = []
    for i, req in enumerate(requests):
        status = response.status if i == len(requests) - 1 else None
        if status is None:
            try:
                r = req.response()
                status = r.status if r else None
            except PlaywrightError:
                status = None
        hops.append({"url": req.url, "status": status})
    return hops


def capture(domain: str, settings: Settings, url: str | None = None) -> PageCapture:
    """Try https://domain/, then http://domain/ — or only ``url``, one page a threat list named (its scheme as
    given; the query string was already dropped). Returns a PageCapture (never raises for site errors)."""
    agent_token = settings.crawler_user_agent_token
    timeout = settings.crawler_navigation_timeout_ms
    with sync_playwright() as p:
        browser, sandboxed = _launch(p, settings)
        try:
            probe = browser.new_context()
            user_agent = probe.new_page().evaluate("() => navigator.userAgent").replace("HeadlessChrome", "Chrome")
            probe.close()
            user_agent = f"{user_agent} {agent_token}"  # honest bot token; robots.txt rules use it
            last: PageCapture | None = None
            attempts = ((url, False), (url, True)) if url else (
                (f"https://{domain}/", False), (f"https://{domain}/", True), (f"http://{domain}/", False))
            for target, ignore_tls in attempts:
                if ignore_tls and (last is None or last.error_code != "tls_error"):
                    continue
                result = _visit(browser, target, user_agent=user_agent, ignore_tls=ignore_tls, timeout=timeout,
                                settings=settings)
                result.sandboxed = sandboxed
                if ignore_tls and result.tls is not None:
                    result.tls["valid"] = False
                if result.error_code is None:
                    return result
                last = result
                if result.error_code in ("timeout", "aborted", "download_refused"):
                    break
            if last is None:  # the loop runs at least once
                raise RuntimeError("no capture attempt was made")
            return last
        finally:
            browser.close()


def _visit(browser, url: str, *, user_agent: str, ignore_tls: bool, timeout: int, settings: Settings) -> PageCapture:
    out = PageCapture(requested_url=url)
    context = browser.new_context(
        user_agent=user_agent, accept_downloads=False, ignore_https_errors=ignore_tls, service_workers="block",
        viewport={"width": settings.crawler_viewport_width, "height": settings.crawler_viewport_height},
    )
    budget = {"left": settings.crawler_max_requests_per_page}

    def route(r: Route) -> None:
        req = r.request
        if not req.url.startswith(("http://", "https://")) or req.resource_type in ("media",):
            out.requests_aborted += 1
            r.abort()
            return
        budget["left"] -= 1
        if budget["left"] < 0:
            out.requests_aborted += 1
            r.abort()
            return
        r.continue_()

    context.route("**/*", route)
    page = context.new_page()
    started = time.monotonic()
    try:
        response = page.goto(url, wait_until="load", timeout=timeout)
        try:
            page.wait_for_load_state("networkidle", timeout=3000)
        except PlaywrightTimeout:
            pass
        out.navigation_ms = int((time.monotonic() - started) * 1000)
        if response is None:
            out.error_code = "no_response"
            return out
        out.http_status = response.status
        headers = response.all_headers()
        out.response_headers = {k: v[:500] for k, v in headers.items() if k.lower() in KEPT_HEADERS}
        out.redirect_chain = _chain(response)
        token = settings.egress_proxy_token.get_secret_value() if settings.egress_proxy_token else None
        if code := proxy_block_code(headers, token):
            # The proxy answered instead of the site: record why, but never treat its page as site content.
            if code in PROXY_NETWORK_CODES:
                out.error_code, out.error_detail = PROXY_NETWORK_CODES[code], f"egress proxy: {code}"
            else:
                out.blocked_reason = code
            out.final_url = page.url
            return out
        out.final_url = page.url
        if out.redirect_chain and out.redirect_chain[-1]["url"] != page.url:
            out.redirect_chain.append({"url": page.url, "status": None, "client_side": True})
        details = response.security_details()
        if details:
            out.tls = {"protocol": details.get("protocol"), "issuer": details.get("issuer"),
                       "subject": details.get("subjectName"), "valid_from": details.get("validFrom"),
                       "valid_to": details.get("validTo"), "valid": not ignore_tls}
        for hop in out.redirect_chain:  # post-hoc: the proxy already enforced this at connect time
            try:
                check_url(hop["url"], allowed_ports={80, 443})
            except BlockedError as exc:
                out.blocked_reason = out.blocked_reason or f"redirect_{exc.code}"
        meta = page.evaluate(METADATA_JS)
        out.title, out.description, out.language = meta["title"], meta["description"], meta["lang"]
        out.open_graph, out.link_count = meta["og"], meta["links"]
        out.text = (meta["text"] or "")[: settings.crawler_max_text_chars]
        out.html = page.content().encode("utf-8", errors="replace")[: settings.crawler_max_html_bytes]
        out.screenshot = page.screenshot(type="png", full_page=False, timeout=10_000)
    except PlaywrightTimeout as exc:
        out.error_code, out.error_detail = "timeout", str(exc).splitlines()[0][:300]
    except PlaywrightError as exc:
        message = str(exc).splitlines()[0][:300]
        out.error_code, out.error_detail = classify_error(message), message
    finally:
        out.navigation_ms = out.navigation_ms or int((time.monotonic() - started) * 1000)
        context.close()
    return out
