"""Provider-independent input/output contracts for website classification."""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


@dataclass(slots=True)
class RedirectHop:
    url: str
    status: int | None = None


@dataclass(slots=True)
class Screenshot:
    data: bytes
    mime_type: Literal["image/png", "image/jpeg", "image/webp"] = "image/png"


@dataclass(slots=True)
class ClassificationInput:
    """Everything the crawler observed. All string fields are untrusted website content."""

    url: str
    domain: str
    final_url: str | None = None
    http_status: int | None = None
    redirect_chain: list[RedirectHop] = field(default_factory=list)
    response_headers: dict[str, str] = field(default_factory=dict)
    title: str | None = None
    meta_description: str | None = None
    open_graph: dict[str, str] = field(default_factory=dict)
    text: str | None = None
    html: str | None = None
    language_hint: str | None = None
    screenshot: Screenshot | None = None


class EvidenceKind(StrEnum):
    HTML = "html"
    TEXT = "text"
    SCREENSHOT = "screenshot"
    METADATA = "metadata"
    REDIRECT = "redirect"


class EvidenceItem(BaseModel):
    model_config = ConfigDict(extra="ignore")

    type: EvidenceKind
    observation: str = Field(min_length=1, max_length=500)


class ClassificationOutput(BaseModel):
    """The JSON object every provider must return. Category values are taxonomy IDs (slugs)."""

    model_config = ConfigDict(extra="ignore", str_strip_whitespace=True)

    domain: str = Field(min_length=1, max_length=253)
    primary_category: str = Field(min_length=1, max_length=64)
    secondary_categories: list[str] = Field(default_factory=list, max_length=5)
    risk_categories: list[str] = Field(default_factory=list, max_length=7)
    confidence: float = Field(ge=0.0, le=1.0)
    language: str | None = Field(default=None, max_length=16)
    site_type: Literal[
        "business", "personal", "government", "education", "non_profit", "media", "community", "parked", "unknown"
    ] = "unknown"
    description: str = Field(default="", max_length=600)
    evidence: list[EvidenceItem] = Field(default_factory=list, max_length=10)
    requires_human_review: bool = False


class ValidationStatus(StrEnum):
    VALID = "valid"
    INVALID_JSON = "invalid_json"
    INVALID_SCHEMA = "invalid_schema"
    INVALID_CATEGORY = "invalid_category"
    PROVIDER_ERROR = "provider_error"


@dataclass(slots=True)
class Completion:
    """Raw answer from a provider before parsing."""

    text: str
    model: str
    input_tokens: int | None = None
    output_tokens: int | None = None
    request_id: str | None = None
    finish_reason: str | None = None


@dataclass(slots=True)
class ProviderResult:
    provider: str
    model: str
    prompt_version: str
    taxonomy_version: str
    status: ValidationStatus
    output: ClassificationOutput | None = None
    errors: list[str] = field(default_factory=list)
    raw_text: str | None = None
    raw_json: dict | None = None
    used_screenshot: bool = False
    injection_signals: list[str] = field(default_factory=list)
    input_tokens: int | None = None
    output_tokens: int | None = None
    latency_ms: int | None = None
    request_id: str | None = None

    @property
    def ok(self) -> bool:
        return self.status is ValidationStatus.VALID


@dataclass(slots=True)
class ProviderHealth:
    provider: str
    status: Literal["ok", "unconfigured", "unverified", "error"]
    detail: str = ""
    models: list[str] = field(default_factory=list)
