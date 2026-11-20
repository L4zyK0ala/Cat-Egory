import pytest

from classification.confidence import DEFAULT_POLICY, ConfidencePolicy, Decision


@pytest.mark.parametrize(("c", "d"), [
    (0.95, Decision.AUTO_APPROVE_CANDIDATE), (0.90, Decision.AUTO_APPROVE_CANDIDATE),
    (0.89, Decision.EXTRA_VALIDATION), (0.70, Decision.EXTRA_VALIDATION),
    (0.69, Decision.HUMAN_REVIEW), (0.50, Decision.HUMAN_REVIEW),
    (0.49, Decision.REANALYZE), (0.0, Decision.REANALYZE),
])
def test_bands(c, d):
    assert DEFAULT_POLICY.decide(c) is d


@pytest.mark.parametrize("flag", ["requires_human_review", "has_risk", "injection_suspected"])
def test_flags_force_human(flag):
    assert DEFAULT_POLICY.decide(0.99, **{flag: True}) is Decision.HUMAN_REVIEW
    assert DEFAULT_POLICY.decide(0.2, **{flag: True}) is Decision.REANALYZE


def test_invalid_thresholds():
    with pytest.raises(ValueError):
        ConfidencePolicy(auto_approve=0.5, extra_validation=0.7)


def test_ordinary_categories_publish_from_a_lower_confidence():
    policy = ConfidencePolicy(auto_approve=0.90, auto_approve_ordinary=0.80)
    assert policy.decide(0.85, categories=["news"]) == Decision.AUTO_APPROVE_CANDIDATE
    assert policy.decide(0.85, categories=["gambling"]) == Decision.EXTRA_VALIDATION
    assert policy.decide(0.85, categories=["news", "adult-content"]) == Decision.EXTRA_VALIDATION  # a sensitive secondary
    assert policy.decide(0.85, categories=["banking"]) == Decision.EXTRA_VALIDATION
    assert policy.decide(0.79, categories=["news"]) == Decision.EXTRA_VALIDATION
    assert policy.decide(0.85) == Decision.AUTO_APPROVE_CANDIDATE                    # no category known: ordinary
    assert policy.decide(0.95, categories=["news"], has_risk=True) == Decision.HUMAN_REVIEW
    assert DEFAULT_POLICY.decide(0.85, categories=["news"]) == Decision.EXTRA_VALIDATION  # default: one threshold
    with pytest.raises(ValueError):
        ConfidencePolicy(auto_approve=0.90, auto_approve_ordinary=0.95)
    with pytest.raises(ValueError):
        ConfidencePolicy(auto_approve=0.90, extra_validation=0.70, auto_approve_ordinary=0.60)
