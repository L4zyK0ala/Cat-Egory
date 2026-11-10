"""Crawler worker (queue ``crawl``). Runs in the crawler container, which has no database credentials and no
direct internet route: it talks only to Redis, the object store and the egress proxy.

    celery -A crawler.tasks:celery_app worker -Q crawl-fast,crawl --concurrency 2
"""
from __future__ import annotations

import logging
import time
from collections.abc import Callable
from datetime import UTC, datetime

from celery.exceptions import SoftTimeLimitExceeded

from backend.app.worker import celery_app
from common.config import Settings, get_settings

from .result import CrawlResult
from .robots import RobotsDecision, fetch_robots
from .storage import ArtifactStore, S3Store, StorageError, save

log = logging.getLogger("category.crawler")


def crawl(payload: dict, settings: Settings, store: ArtifactStore, *,
          capture_fn: Callable | None = None,
          robots_fn: Callable[..., RobotsDecision] = fetch_robots) -> CrawlResult:
    job_id, domain = payload["job_id"], payload["domain"]
    result = CrawlResult(job_id=job_id, domain=domain, requested_url=f"https://{domain}/",
                         dns=payload.get("dns", {}), resolved_ips=payload.get("resolved_ips", []))
    started = time.monotonic()
    try:
        if settings.crawler_respect_robots:
            t = time.monotonic()
            token = settings.egress_proxy_token.get_secret_value() if settings.egress_proxy_token else None
            decision = robots_fn(domain, agent=settings.crawler_user_agent_token, proxy_url=settings.crawler_proxy_url,
                                 proxy_token=token)
            result.timings["robots_ms"] = int((time.monotonic() - t) * 1000)
            result.robots_allowed = decision.allowed
            if not decision.allowed:
                if decision.reason.startswith("proxy:"):  # robots.txt host refused by the SSRF policy
                    result.blocked_reason = decision.reason.removeprefix("proxy:")
                elif decision.reason == "unavailable":
                    result.error_code = "robots_unavailable"
                    result.error_detail = "robots.txt unreachable (RFC 9309: assume disallow); site may be down"
                else:
                    result.blocked_reason = "robots_disallowed"
                return result

        if capture_fn is None:
            from .browser import capture as capture_fn  # imported lazily: Playwright exists only in this image
        page = capture_fn(domain, settings)
        result.requested_url = page.requested_url
        result.final_url, result.http_status = page.final_url, page.http_status
        result.redirect_chain, result.response_headers, result.tls = page.redirect_chain, page.response_headers, page.tls
        result.content_type = page.response_headers.get("content-type")
        length = page.response_headers.get("content-length", "")
        result.content_length = int(length) if length.isdigit() else None
        result.page_title = (page.title or "")[:1000] or None
        result.meta_description = (page.description or "")[:2000] or None
        result.open_graph, result.language_hint = page.open_graph, (page.language or "")[:16] or None
        result.link_count, result.sandboxed = page.link_count, page.sandboxed
        result.blocked_reason, result.error_code, result.error_detail = (
            page.blocked_reason, page.error_code, page.error_detail)
        result.timings["navigation_ms"] = page.navigation_ms

        prefix = f"scans/{datetime.now(UTC):%Y/%m}/{job_id}"
        if page.screenshot:
            result.screenshot = save(store, f"{prefix}/screenshot.png", page.screenshot, "image/png",
                                     width=settings.crawler_viewport_width, height=settings.crawler_viewport_height)
        if page.html:
            result.html = save(store, f"{prefix}/page.html.gz", page.html, "text/html", compress=True)
        if page.text:
            result.text = save(store, f"{prefix}/text.txt.gz", page.text.encode("utf-8"), "text/plain; charset=utf-8",
                               compress=True)
            result.text_excerpt = page.text[: settings.max_text_chars]
            result.word_count = len(page.text.split())
    except SoftTimeLimitExceeded:
        result.error_code, result.error_detail = "timeout", "job time limit exceeded"
    except StorageError as exc:
        log.error("artifact storage failed", extra={"job_id": job_id, "operation": exc.operation, "code": exc.code})
        result.error_code, result.error_detail = "storage_error", str(exc)[:300]
    except Exception as exc:  # noqa: BLE001 - always hand a result back so the job is finalised
        from .browser import SandboxUnavailable

        if isinstance(exc, SandboxUnavailable):
            result.error_code, result.error_detail = "sandbox_unavailable", str(exc)[:300]
        else:
            log.exception("crawl failed", extra={"job_id": job_id, "domain": domain})
            result.error_code, result.error_detail = "internal_error", type(exc).__name__
    finally:
        result.timings["total_ms"] = int((time.monotonic() - started) * 1000)
    return result


@celery_app.task(name="category.crawl.run", ignore_result=True, soft_time_limit=150, time_limit=180)
def run(payload: dict) -> dict:
    settings = get_settings()
    result = crawl(payload, settings, S3Store(settings))
    log.info("crawl finished", extra={"job_id": result.job_id, "domain": result.domain, "status": result.http_status,
                                      "error": result.error_code, "blocked": result.blocked_reason,
                                      "ms": result.timings.get("total_ms")})
    return result.to_dict()  # passed to the linked persist task on the core worker
