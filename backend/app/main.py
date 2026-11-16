"""FastAPI application factory."""
from __future__ import annotations

import logging
import time
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import redis
from fastapi import Depends, FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from prometheus_client import (
    CONTENT_TYPE_LATEST,
    CollectorRegistry,
    Counter,
    Histogram,
    ProcessCollector,
    generate_latest,
    start_http_server,
)
from sqlalchemy import text

from common.config import get_settings
from common.logging import configure_logging
from database.session import get_engine

from . import errors
from .api import abuse, admin, changes, community, developer, feeds, reviews, session, threats, trends, v1, yasmama
from .body_limit import BodyLimitMiddleware, request_limit
from .deps import get_redis

log = logging.getLogger("category.api")

REQUESTS = Counter("category_http_requests_total", "HTTP requests", ["method", "route", "status"])
LATENCY = Histogram("category_http_request_seconds", "HTTP request latency", ["method", "route"])
# What the public API's metrics port serves: the request counters and the process's own memory and CPU, never the
# database numbers (those exist only on the admin instance).
HTTP_REGISTRY = CollectorRegistry()
HTTP_REGISTRY.register(REQUESTS)
HTTP_REGISTRY.register(LATENCY)
ProcessCollector(registry=HTTP_REGISTRY)

# The method is text the client chooses. Used as a label unchecked, every new spelling would add time series (and
# memory) for as long as the process lives.
METHODS = frozenset({"GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"})

SECURITY_HEADERS = {
    # Browsers act on this only when it arrives over HTTPS (through the tunnel); over plain HTTP on the LAN it is ignored.
    "Strict-Transport-Security": "max-age=31536000",
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Content-Security-Policy": "default-src 'none'; frame-ancestors 'none'",
}
DOCS_CSP = "default-src 'self'; script-src 'self' https://cdn.jsdelivr.net 'unsafe-inline'; " \
           "style-src 'self' https://cdn.jsdelivr.net 'unsafe-inline'; img-src 'self' data: https://fastapi.tiangolo.com"


def create_app() -> FastAPI:
    settings = get_settings()
    configure_logging(settings.log_level)

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        # The public API has no /metrics route (the tunnel would put it on the internet). Its request counters are
        # served on a second port instead, which is neither published nor forwarded, so Prometheus sees the errors
        # and latency of what visitors actually use.
        if settings.metrics_port and not settings.admin_routes:
            start_http_server(settings.metrics_port, registry=HTTP_REGISTRY)
        yield

    app = FastAPI(
        lifespan=lifespan,
        title="Cat-egory API",
        version="0.1.0",
        description="Open web categorisation database. Code: Apache-2.0. Own classification data: CC BY 4.0; "
                    "own threat confirmations (threat lists, security.rpz, threats.csv/json): CC0 1.0; "
                    "third-party data keeps its source licence.",
        license_info={"name": "Apache-2.0", "url": "https://www.apache.org/licenses/LICENSE-2.0"},
        openapi_url="/api/v1/openapi.json",
        docs_url="/api/docs",
        redoc_url=None,
    )

    # Added first, so it runs inside request_context: a refused body still gets a request id, the headers and a count.
    app.add_middleware(BodyLimitMiddleware, max_bytes=request_limit(settings))

    @app.middleware("http")
    async def request_context(request: Request, call_next):
        incoming = request.headers.get("X-Request-ID", "")
        request_id = incoming if incoming and len(incoming) <= 64 and incoming.isascii() and incoming.isprintable() \
            else uuid.uuid4().hex
        request.state.request_id = request_id
        started = time.perf_counter()
        try:
            response: Response = await call_next(request)
        except Exception:  # noqa: BLE001
            # Answered here and not by Starlette's outermost handler, which sits outside this middleware: its plain
            # "Internal Server Error" would carry no request id and no headers, and would never reach the counter
            # the ApiErrors alert reads.
            log.exception("unhandled error", extra={"request_id": request_id, "path": request.url.path})
            response = JSONResponse(errors.envelope(request, "internal_error", "internal server error"), 500)
        route = getattr(request.scope.get("route"), "path", "unmatched")
        method = request.method if request.method in METHODS else "OTHER"
        LATENCY.labels(method, route).observe(time.perf_counter() - started)
        REQUESTS.labels(method, route, response.status_code).inc()
        response.headers["X-Request-ID"] = request_id
        for key, value in SECURITY_HEADERS.items():
            response.headers.setdefault(key, value)
        if request.url.path == "/api/docs":
            response.headers["Content-Security-Policy"] = DOCS_CSP
        return response

    @app.get("/healthz", tags=["ops"], include_in_schema=False)
    def healthz() -> dict:
        return {"status": "ok"}

    @app.get("/readyz", tags=["ops"], include_in_schema=False)
    def readyz(client: redis.Redis = Depends(get_redis)) -> JSONResponse:
        checks: dict[str, str] = {}
        try:
            with get_engine().connect() as conn:
                conn.execute(text("SELECT 1"))
            checks["database"] = "ok"
        except Exception as exc:  # noqa: BLE001
            log.warning("database not ready", extra={"error": type(exc).__name__})
            checks["database"] = "error"
        try:
            client.ping()  # the shared client: a new connection per probe would let anyone open them at will
            checks["redis"] = "ok"
        except Exception as exc:  # noqa: BLE001
            log.warning("redis not ready", extra={"error": type(exc).__name__})
            checks["redis"] = "error"
        ok = all(v == "ok" for v in checks.values())
        return JSONResponse({"status": "ok" if ok else "degraded", "checks": checks}, status_code=200 if ok else 503)

    if settings.admin_routes:
        # Internal numbers are for the admin instance (and Prometheus) only, never on the public API.
        from . import metrics as business_metrics

        business_metrics.install()

        @app.get("/metrics", include_in_schema=False)
        def metrics() -> Response:
            return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)

    if settings.cors_origins:
        # Browser calls from the web UI. No cookies are used (API keys travel in the Authorization header),
        # so credentials stay disabled and there is no CSRF surface.
        app.add_middleware(CORSMiddleware, allow_origins=settings.cors_origins, allow_credentials=False,
                           allow_methods=["GET", "POST"], allow_headers=["Authorization", "Content-Type"],
                           expose_headers=["ETag", "RateLimit-Limit", "RateLimit-Remaining", "RateLimit-Reset",
                                           "Retry-After", "X-Request-ID", "X-Feed-Version"], max_age=600)
    errors.install(app)
    app.include_router(v1.router)
    app.include_router(changes.router)  # before feeds: /feeds/{name}/diff must not be read as a feed name
    app.include_router(feeds.router)
    app.include_router(community.public)
    app.include_router(developer.router)
    if settings.admin_routes:
        # Admin, review and moderation endpoints exist only on the admin instance. On the public API they are
        # absent (404), so a leaked admin key cannot be used from the internet.
        app.include_router(admin.router)
        app.include_router(reviews.router)
        app.include_router(community.moderation)
        app.include_router(threats.router)
        app.include_router(abuse.router)
        app.include_router(trends.router)
        app.include_router(yasmama.router)
        app.include_router(session.router)
    return app


app = create_app()
