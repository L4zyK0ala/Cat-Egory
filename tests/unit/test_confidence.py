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
