"""Provider-independent input/output contracts for website classification."""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


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


# Labels models use for evidence instead of ours (seen 2026-11-08: "title"). Evidence explains a decision but does
# not make it, so an odd label is mapped or the item dropped instead of rejecting a correct classification.
EVIDENCE_ALIASES: dict[str, EvidenceKind] = {
    **{k: EvidenceKind.METADATA for k in (
        "title", "page_title", "meta", "meta_description", "description", "open_graph", "og", "headers",
        "http_headers", "header", "structured_data", "json_ld", "jsonld", "schema", "domain", "url", "whois",
        "certificate")},
    **{k: EvidenceKind.REDIRECT for k in ("redirects", "redirect_chain", "final_url")},
    **{k: EvidenceKind.SCREENSHOT for k in ("image", "visual", "logo")},
    **{k: EvidenceKind.TEXT for k in ("content", "body", "heading", "headings", "page_text", "visible_text")},
    **{k: EvidenceKind.HTML for k in ("markup", "source", "dom", "link", "links", "script")},
}
MAX_SECONDARY = 3  # asked for in the prompt; extra ones are dropped, most relevant first
MAX_EVIDENCE = 10


def _evidence_kind(value) -> EvidenceKind | None:
    key = str(value or "").strip().lower().replace("-", "_").replace(" ", "_")
    try:
        return EvidenceKind(key)
    except ValueError:
        return EVIDENCE_ALIASES.get(key)


class ClassificationOutput(BaseModel):
    """The JSON object every provider must return. Category values are taxonomy IDs (slugs).

    Harmless form problems are normalised before validation: evidence labels are mapped (unknown ones dropped,
    long observations cut), secondary categories are de-duplicated, the primary is removed from them and only the
    first ``MAX_SECONDARY`` are kept. Category IDs themselves are never mapped: unknown IDs stay errors.
    """

    model_config = ConfigDict(extra="ignore", str_strip_whitespace=True)

    @model_validator(mode="before")
    @classmethod
    def _normalise(cls, data):
        if not isinstance(data, dict):
            return data
        data = dict(data)
        evidence = data.get("evidence")
        if isinstance(evidence, list):
            kept = []
            for item in evidence:
                if not isinstance(item, dict) or not isinstance(item.get("observation"), str):
                    continue
                kind, text = _evidence_kind(item.get("type")), item["observation"].strip()
                if kind is not None and text:
                    kept.append({"type": kind.value, "observation": text[:500]})
            data["evidence"] = kept[:MAX_EVIDENCE]
        secondary, primary = data.get("secondary_categories"), data.get("primary_category")
        if isinstance(secondary, list) and all(isinstance(c, str) for c in secondary):
            seen: list[str] = []
            for cid in (c.strip() for c in secondary):
                if cid and cid != primary and cid not in seen:
                    seen.append(cid)
            data["secondary_categories"] = seen[:MAX_SECONDARY]
        return data

    domain: str = Field(min_length=1, max_length=253)
    primary_category: str = Field(min_length=1, max_length=64)
    secondary_categories: list[str] = Field(default_factory=list, max_length=MAX_SECONDARY)
    risk_categories: list[str] = Field(default_factory=list, max_length=7)
    confidence: float = Field(ge=0.0, le=1.0)
    language: str | None = Field(default=None, max_length=16)
    site_type: Literal[
        "business", "personal", "government", "education", "non_profit", "media", "community", "parked", "unknown"
    ] = "unknown"
    description: str = Field(default="", max_length=600)
    evidence: list[EvidenceItem] = Field(default_factory=list, max_length=MAX_EVIDENCE)
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
