"""Parse and validate a provider's raw answer.

Order: extract JSON → JSON-Schema/Pydantic validation → taxonomy validation. Unknown category IDs are
rejected, not "fixed": guessing a mapping would hide model errors and let injected text pick categories.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

from pydantic import ValidationError

from ai_providers.schemas import ClassificationOutput, ValidationStatus

from .taxonomy import Taxonomy

_THINK_RE = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)
_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL | re.IGNORECASE)


@dataclass(slots=True)
class ParsedAnswer:
    status: ValidationStatus
    output: ClassificationOutput | None = None
    raw_json: dict | None = None
    errors: list[str] = field(default_factory=list)


def extract_json(text: str) -> dict:
    """Return the first JSON object in ``text`` (tolerates reasoning blocks and markdown fences)."""
    cleaned = _THINK_RE.sub("", text or "").strip()
    if m := _FENCE_RE.search(cleaned):
        cleaned = m.group(1).strip()
    start = cleaned.find("{")
    if start < 0:
        raise ValueError("no JSON object found")
    obj, _ = json.JSONDecoder().raw_decode(cleaned[start:])
    if not isinstance(obj, dict):
        raise ValueError("top-level JSON value is not an object")
    return obj


def check_taxonomy(output: ClassificationOutput, taxonomy: Taxonomy, domain: str) -> list[str]:
    errors: list[str] = []
    content = set(taxonomy.assignable_ids("content"))
    risk = set(taxonomy.assignable_ids("risk"))
    if output.primary_category not in content:
        errors.append(f"primary_category: unknown content category {output.primary_category!r}")
    for cid in output.secondary_categories:
        if cid not in content:
            errors.append(f"secondary_categories: unknown content category {cid!r}")
    if output.primary_category in output.secondary_categories:
        errors.append("secondary_categories: repeats primary_category")
    if len(set(output.secondary_categories)) != len(output.secondary_categories):
        errors.append("secondary_categories: duplicates")
    for cid in output.risk_categories:
        if cid not in risk:
            errors.append(f"risk_categories: unknown security-threat category {cid!r}")
    if output.domain.lower().rstrip(".") != domain.lower():
        errors.append(f"domain: answer is about {output.domain!r}, expected {domain!r}")
    return errors


def parse_answer(text: str, taxonomy: Taxonomy, domain: str) -> ParsedAnswer:
    try:
        raw = extract_json(text)
    except ValueError as exc:
        return ParsedAnswer(ValidationStatus.INVALID_JSON, errors=[str(exc)])
    try:
        output = ClassificationOutput.model_validate(raw)
    except ValidationError as exc:
        errors = [f"{'.'.join(map(str, e['loc']))}: {e['msg']}" for e in exc.errors()]
        return ParsedAnswer(ValidationStatus.INVALID_SCHEMA, raw_json=raw, errors=errors)
    if errors := check_taxonomy(output, taxonomy, domain):
        return ParsedAnswer(ValidationStatus.INVALID_CATEGORY, output=output, raw_json=raw, errors=errors)
    return ParsedAnswer(ValidationStatus.VALID, output=output, raw_json=raw)
