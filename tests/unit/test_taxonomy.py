import pytest
import yaml

from classification.taxonomy import TaxonomyError, _validate, default_taxonomy


def test_bundled_taxonomy_is_valid():
    t = default_taxonomy()
    assert t.version
    codes = [c.code for c in t.categories.values()]
    assert len(codes) == len(set(codes))


def test_groups_are_not_assignable_and_risk_is_separate():
    t = default_taxonomy()
    content, risk = set(t.assignable_ids("content")), set(t.assignable_ids("risk"))
    assert "group-entertainment" not in content
    assert {"malware", "phishing", "scam", "fraud", "command-and-control"} <= risk
    assert not content & risk
    assert "cybersecurity" in content and "cybersecurity" not in risk


def test_requested_categories_present():
    ids = set(default_taxonomy().categories)
    for cid in ("banking", "artificial-intelligence", "social-networking", "online-radio", "libraries",
                "real-estate", "hate-and-discrimination", "url-shorteners", "software-downloads",
                "malicious-downloads"):
        assert cid in ids


def test_prompt_listing_contains_only_assignable():
    listing = default_taxonomy().prompt_listing("content")
    assert "- gambling:" in listing
    assert "- group-" not in listing


def _base(cats):
    return {"version": "9.9.9", "released": "2026-01-01", "categories": cats}


GROUP = {"id": "g", "code": 1, "kind": "content", "group": True, "name": "G", "description": "g"}


@pytest.mark.parametrize(
    "cats",
    [
        [GROUP, {"id": "g", "code": 2, "kind": "content", "parent": "g", "name": "x", "description": "x"}],
        [GROUP, {"id": "a", "code": 1, "kind": "content", "parent": "g", "name": "x", "description": "x"}],
        [GROUP, {"id": "a", "code": 2, "kind": "risk", "parent": "g", "name": "x", "description": "x"}],
        [GROUP, {"id": "a", "code": 2, "kind": "content", "parent": "nope", "name": "x", "description": "x"}],
        [GROUP, {"id": "a", "code": 2, "kind": "content", "name": "x", "description": "x"}],
        [GROUP, {"id": "a", "code": 2, "kind": "weird", "parent": "g", "name": "x", "description": "x"}],
    ],
)
def test_invalid_taxonomies(cats):
    with pytest.raises(TaxonomyError):
        _validate(yaml.safe_load(yaml.safe_dump(_base(cats))))


def test_deprecated_category_is_not_assignable():
    t = _validate(_base([GROUP,
        {"id": "old", "code": 2, "kind": "content", "parent": "g", "name": "o", "description": "o",
         "deprecated_in": "2.0.0", "replaced_by": "new"},
        {"id": "new", "code": 3, "kind": "content", "parent": "g", "name": "n", "description": "n"}]))
    assert t.assignable_ids("content") == ["new"]
