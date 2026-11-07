import json

import pytest

from ai_providers.schemas import ValidationStatus
from classification.taxonomy import default_taxonomy
from classification.validation import extract_json, parse_answer

TAX = default_taxonomy()


def answer(**over):
    base = {
        "domain": "example.com", "primary_category": "information-technology",
        "secondary_categories": ["artificial-intelligence", "software"], "risk_categories": [],
        "confidence": 0.96, "language": "en", "site_type": "business", "description": "An AI company.",
        "evidence": [{"type": "html", "observation": "Website provides AI software services"}],
        "requires_human_review": False,
    }
    base.update(over)
    return json.dumps(base)


def test_valid_answer():
    parsed = parse_answer(answer(), TAX, "example.com")
    assert parsed.status is ValidationStatus.VALID
    assert parsed.output.secondary_categories == ["artificial-intelligence", "software"]


@pytest.mark.parametrize("wrap", [
    "```json\n{}\n```", "Here you go: {} thanks", "<think>hmm {not json}</think>\n{}",
])
def test_extract_json_tolerates_wrappers(wrap):
    assert extract_json(wrap.replace("{}", answer()))["domain"] == "example.com"


def test_invalid_json():
    assert parse_answer("I think it's a tech site", TAX, "example.com").status is ValidationStatus.INVALID_JSON
    assert parse_answer("[1, 2]", TAX, "example.com").status is ValidationStatus.INVALID_JSON


@pytest.mark.parametrize("over", [
    {"confidence": 1.7}, {"confidence": "high"}, {"primary_category": None}, {"site_type": "spaceship"},
    {"evidence": [{"type": "rumour", "observation": "x"}]}, {"secondary_categories": ["software"] * 6},
])
def test_schema_violations(over):
    assert parse_answer(answer(**over), TAX, "example.com").status is ValidationStatus.INVALID_SCHEMA


@pytest.mark.parametrize("over", [
    {"primary_category": "Technology"},  # display name instead of id
    {"primary_category": "group-business-technology"},  # groups are not assignable
    {"primary_category": "phishing"},  # risk id used as content
    {"secondary_categories": ["made-up"]},
    {"risk_categories": ["gambling"]},  # content id used as risk
    {"secondary_categories": ["information-technology"]},  # repeats primary
    {"domain": "other.com"},
])
def test_taxonomy_violations(over):
    parsed = parse_answer(answer(**over), TAX, "example.com")
    assert parsed.status is ValidationStatus.INVALID_CATEGORY
    assert parsed.errors


def test_extra_keys_ignored():
    assert parse_answer(answer(note="extra"), TAX, "example.com").status is ValidationStatus.VALID
