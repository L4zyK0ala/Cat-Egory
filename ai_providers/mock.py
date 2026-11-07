"""Deterministic offline provider for development and tests.

It is a keyword matcher, not an AI. Its confidence is capped at 0.60 so its answers can never pass the
auto-approval policy and cannot reach public feeds without a human decision.
"""
from __future__ import annotations

import json

from .base import AIProvider
from .schemas import ClassificationInput, Completion, ProviderHealth

MAX_CONFIDENCE = 0.60

# (category id, keywords) — first match wins for primary, the rest become secondary.
_RULES: list[tuple[str, tuple[str, ...]]] = [
    ("gambling", ("casino", "betting", "poker", "bahis", "slot")),
    ("adult-content", ("porn", "xxx", "adult only")),
    ("banking", ("bank", "banka", "online banking")),
    ("cryptocurrency", ("bitcoin", "crypto", "blockchain", "ethereum")),
    ("artificial-intelligence", ("artificial intelligence", "machine learning", "llm", "yapay zeka", "yapay zekâ")),
    ("news", ("news", "haber", "breaking")),
    ("e-commerce", ("add to cart", "sepete ekle", "free shipping", "checkout")),
    ("education", ("university", "üniversite", "course", "students")),
    ("government", ("ministry", "bakanlığı", ".gov", "gov.tr")),
    ("software", ("software", "download", "saas", "yazılım")),
    ("social-networking", ("followers", "friends", "social network")),
    ("parked-domains", ("domain is for sale", "this domain may be for sale", "parked")),
]


class MockProvider(AIProvider):
    name = "mock"

    @property
    def model(self) -> str:
        return "mock-keyword-v1"

    @property
    def supports_images(self) -> bool:
        return False

    def health(self) -> ProviderHealth:
        return ProviderHealth(provider=self.name, status="ok", detail="offline keyword matcher (not an AI model)",
                              models=[self.model])

    def _complete(self, system: str, user: str, image_data_url: str | None, data: ClassificationInput) -> Completion:
        haystack = " ".join(filter(None, [data.domain, data.title, data.meta_description, data.text])).lower()
        matches = [cid for cid, words in _RULES if any(w in haystack for w in words)]
        primary = matches[0] if matches else "business"
        confidence = MAX_CONFIDENCE if matches else 0.30
        answer = {
            "domain": data.domain,
            "primary_category": primary,
            "secondary_categories": matches[1:4],
            "risk_categories": [],
            "confidence": confidence,
            "language": data.language_hint,
            "site_type": "parked" if primary == "parked-domains" else "unknown",
            "description": "Mock classification from keyword matching; not produced by an AI model.",
            "evidence": [{"type": "text", "observation": f"keyword rule matched: {primary}"}] if matches else [],
            "requires_human_review": True,
        }
        return Completion(text=json.dumps(answer), model=self.model, input_tokens=0, output_tokens=0)
