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
    {"secondary_categories": "software"}, {"evidence": "lots"},
])
def test_schema_violations(over):
    assert parse_answer(answer(**over), TAX, "example.com").status is ValidationStatus.INVALID_SCHEMA


@pytest.mark.parametrize("over", [
    {"primary_category": "Technology"},  # display name instead of id
    {"primary_category": "group-business-technology"},  # groups are not assignable
    {"primary_category": "phishing"},  # risk id used as content
    {"secondary_categories": ["made-up"]},
    {"risk_categories": ["gambling"]},  # content id used as risk
    {"domain": "other.com"},
])
def test_taxonomy_violations(over):
    parsed = parse_answer(answer(**over), TAX, "example.com")
    assert parsed.status is ValidationStatus.INVALID_CATEGORY
    assert parsed.errors


def test_extra_keys_ignored():
    assert parse_answer(answer(note="extra"), TAX, "example.com").status is ValidationStatus.VALID


def test_odd_evidence_labels_do_not_reject_a_classification():
    """Seen live: "title" instead of "metadata" made 3 of 35 correct answers invalid."""
    parsed = parse_answer(answer(evidence=[
        {"type": "title", "observation": "Page title is 'Benim Bankam'."},
        {"type": "Structured-Data", "observation": "BankOrCreditUnion"},
        {"type": "rumour", "observation": "dropped"}, {"type": "text", "observation": "  "},
        {"type": "text", "observation": "x" * 900}]), TAX, "example.com")
    assert parsed.status is ValidationStatus.VALID
    assert [e.type.value for e in parsed.output.evidence] == ["metadata", "metadata", "text"]
    assert parsed.output.evidence[0].observation == "Page title is 'Benim Bankam'."
    assert len(parsed.output.evidence[2].observation) == 500


def test_secondary_categories_are_tidied_not_rejected():
    parsed = parse_answer(answer(secondary_categories=[
        "information-technology", "software", "software", "business", "cloud-computing", "artificial-intelligence"]),
        TAX, "example.com")
    assert parsed.status is ValidationStatus.VALID
    assert parsed.output.secondary_categories == ["software", "business", "cloud-computing"]  # primary removed, max 3
    unknown = parse_answer(answer(secondary_categories=["made-up", "software"]), TAX, "example.com")
    assert unknown.status is ValidationStatus.INVALID_CATEGORY  # unknown IDs are still never guessed
