"""The EVREN probe: reads the gateway, never prints the key, refuses retired models in comparisons."""
import json

import httpx
import pytest

from ai_providers import evren_probe
from ai_providers.evren import KNOWN_MODELS, RETIRED_MODELS, EvrenProvider
from classification.model_compare import check_models
from common.config import get_settings

KEY = "test-evren-key-not-real"


@pytest.fixture
def key(monkeypatch):
    monkeypatch.setenv("CATEGORY_EVREN_API_KEY", KEY)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def client(seen, answer):
    def handle(request: httpx.Request):
        seen.append(request)
        return answer(request)
    return httpx.Client(transport=httpx.MockTransport(handle))


def test_models_lists_what_the_gateway_serves(key, capsys):
    seen = []
    def answer(_):
        return httpx.Response(200, json={"data": [{"id": "qwen3.8-flash-next"}, {"id": "clef", "owned_by": "evren"}]})
    with client(seen, answer) as http:
        assert evren_probe.models(http) == 0
    out = capsys.readouterr().out
    assert "- qwen3.8-flash-next" in out.splitlines() and "- clef" in out
    assert seen[0].url.path == "/v1/models" and seen[0].headers["Authorization"] == f"Bearer {KEY}"
    assert KEY not in out


def test_moderation_sends_only_the_fixed_samples_and_shows_usage_headers(key, capsys):
    seen = []
    def answer(_):
        return httpx.Response(200, headers={"x-ratelimit-remaining": "1199", "x-request-id": "abc"},
                              json={"results": [{"flagged": False}] * 3, "usage": {"total_tokens": 0}})
    with client(seen, answer) as http:
        assert evren_probe.moderation(http) == 0
    sent = json.loads(seen[0].content)
    assert seen[0].url.path == "/v1/moderations"
    assert sent == {"model": "qwen3-guard-4b", "input": evren_probe.SAMPLES}
    out = capsys.readouterr().out
    assert "header x-ratelimit-remaining: 1199" in out and "x-request-id" not in out
    assert '"total_tokens": 0' in out and KEY not in out


def test_without_a_key_it_says_where_to_run(monkeypatch):
    monkeypatch.setenv("CATEGORY_EVREN_API_KEY", "")
    get_settings.cache_clear()
    with pytest.raises(SystemExit, match="worker"):
        evren_probe._auth()
    get_settings.cache_clear()


def test_a_retired_model_is_refused_with_the_reason():
    assert "mimo-v2.6-pro" in RETIRED_MODELS and "mimo-v2.6-pro" not in KNOWN_MODELS
    with pytest.raises(ValueError, match="deepseek-v4.1-flash until 2027-01-01"):
        check_models("qwen3.8-flash-next,mimo-v2.6-pro")
    health = EvrenProvider(api_key="k", base_url="https://x.example", model="mimo-v2.6-pro").health()
    assert health.status == "error" and "retired" in health.detail
