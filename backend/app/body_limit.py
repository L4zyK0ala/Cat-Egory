"""Refuse request bodies that are too large, before they are held in memory.

FastAPI reads the whole body before it runs any dependency, so the rate limit comes too late: without this, one
anonymous POST of a few hundred megabytes fills the container's memory. A declared size (Content-Length) is refused
at once; a body without one (chunked) is counted while it is read.
"""
from __future__ import annotations

from fastapi import HTTPException
from fastapi.responses import JSONResponse
from starlette.requests import Request
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from common.config import Settings

from .errors import envelope

_UPLOAD_OVERHEAD = 64 * 1024  # multipart boundaries and the other form fields around an uploaded file
MESSAGE = "request body too large"


def request_limit(settings: Settings) -> int:
    """The public API takes small JSON only. The admin instance also takes file uploads (list imports)."""
    if settings.admin_routes:
        return max(settings.max_request_bytes, settings.max_upload_bytes + _UPLOAD_OVERHEAD)
    return settings.max_request_bytes


class BodyLimitMiddleware:
    def __init__(self, app: ASGIApp, max_bytes: int) -> None:
        self.app, self.max_bytes = app, max_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        declared = next((v for k, v in scope["headers"] if k == b"content-length"), None)
        if declared is not None and declared.isdigit() and int(declared) > self.max_bytes:
            # The body is not read, so the connection cannot be reused.
            response = JSONResponse(envelope(Request(scope), "payload_too_large", MESSAGE), 413,
                                    headers={"Connection": "close"})
            await response(scope, receive, send)
            return

        seen = 0

        async def limited() -> Message:
            nonlocal seen
            message = await receive()
            if message["type"] == "http.request":
                seen += len(message.get("body", b""))
                if seen > self.max_bytes:
                    # FastAPI passes an HTTPException raised while it reads the body on to the error handlers.
                    raise HTTPException(413, MESSAGE)
            return message

        await self.app(scope, limited, send)
