"""Crawl result contract between the crawler (no DB access) and the core worker (persists it)."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

# error codes that are worth retrying later
RETRYABLE = frozenset({"timeout", "connection_failed", "proxy_unavailable", "browser_crashed", "internal_error",
                       "robots_unavailable", "dns_timeout", "dns_failure", "storage_error"})
# Codes the egress proxy returns when the target is unreachable (as opposed to forbidden by policy).
PROXY_NETWORK_CODES = {"dns_failure": "dns_failure", "dns_no_answer": "dns_failure", "connect_failed": "connection_failed"}


@dataclass
class Artifact:
    key: str
    sha256: str
    size: int
    mime_type: str
    width: int | None = None
    height: int | None = None


@dataclass
class CrawlResult:
    job_id: str
    domain: str
    requested_url: str
    final_url: str | None = None
    http_status: int | None = None
    redirect_chain: list[dict[str, Any]] = field(default_factory=list)
    response_headers: dict[str, str] = field(default_factory=dict)
    tls: dict[str, Any] | None = None
    content_type: str | None = None
    content_length: int | None = None
    page_title: str | None = None
    meta_description: str | None = None
    open_graph: dict[str, str] = field(default_factory=dict)
    language_hint: str | None = None
    link_count: int | None = None
    word_count: int | None = None
    text_excerpt: str | None = None
    robots_allowed: bool | None = None
    blocked_reason: str | None = None
    error_code: str | None = None
    error_detail: str | None = None
    dns: dict[str, Any] = field(default_factory=dict)
    resolved_ips: list[str] = field(default_factory=list)
    timings: dict[str, int] = field(default_factory=dict)
    screenshot: Artifact | None = None
    html: Artifact | None = None
    text: Artifact | None = None
    sandboxed: bool | None = None

    @property
    def ok(self) -> bool:
        return self.error_code is None and self.blocked_reason is None

    @property
    def retryable(self) -> bool:
        return self.error_code in RETRYABLE

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CrawlResult:
        data = dict(data)
        for name in ("screenshot", "html", "text"):
            if data.get(name):
                data[name] = Artifact(**data[name])
        known = cls.__dataclass_fields__
        return cls(**{k: v for k, v in data.items() if k in known})
