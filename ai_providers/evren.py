"""EVREN LLM gateway adapter (https://evren.ssyz.org.tr/).

What is verified and what is not is tracked in documentation/EVREN_INTEGRATION.md. In short, as of
2026-10-31 a separate, working production client confirmed:

* OpenAI-compatible ``POST {base}/v1/chat/completions`` on ``https://evren-llmapi.ssyz.org.tr``
* ``Authorization: Bearer <key>``; ``Idempotency-Key`` header accepted
* image input as ``image_url`` data URIs on vision-capable models
* error statuses 401 auth, 402 credit, 403 permission, 429 rate; 502/503/504 are transient
* the response ``model`` may carry a vendor prefix (``qwen/...``)

NOT verified (therefore not relied upon): ``response_format`` / JSON mode (off by default, see
``evren_json_mode``), ``temperature`` support, a model-listing endpoint, rate limits, context sizes.
"""
from __future__ import annotations

import time
import uuid

import httpx

from .base import AIProvider, ProviderError
from .schemas import ClassificationInput, Completion, ProviderHealth

# Models seen working through the gateway on 2026-10-31. Availability can change without notice.
KNOWN_MODELS: dict[str, dict] = {
    "qwen3.8-flash-next": {"images": True, "max_output": 16384},
    "deepseek-v4.1-flash": {"images": True, "max_output": 65536},
    "mimo-v2.6-pro": {"images": True, "max_output": 16384},
    "gemma-4-31b": {"images": True, "max_output": 16384},
    "glm-5.3": {"images": False, "max_output": 16384},
}
_RETRY_STATUSES = (502, 503, 504)
_ERROR_KINDS = {401: "auth", 402: "credit", 403: "permission", 429: "rate"}


class EvrenProvider(AIProvider):
    name = "evren"

    def __init__(self, *, api_key: str | None, base_url: str, model: str, timeout: float = 120.0,
                 json_mode: bool = False, max_output_tokens: int = 1500, max_attempts: int = 3,
                 client: httpx.Client | None = None, **kwargs) -> None:
        super().__init__(**kwargs)
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self._model = model
        self._json_mode = json_mode
        self._max_output = min(max_output_tokens, KNOWN_MODELS.get(model, {}).get("max_output", max_output_tokens))
        self._max_attempts = max_attempts
        self._client = client or httpx.Client(timeout=httpx.Timeout(timeout, connect=15.0), follow_redirects=False)

    @property
    def model(self) -> str:
        return self._model

    @property
    def supports_images(self) -> bool:
        # Unknown models are treated as text-only until verified.
        return bool(KNOWN_MODELS.get(self._model, {}).get("images", False))

    def health(self) -> ProviderHealth:
        if not self._api_key:
            return ProviderHealth(self.name, "unconfigured", "CATEGORY_EVREN_API_KEY is not set")
        if self._model not in KNOWN_MODELS:
            return ProviderHealth(self.name, "unverified", f"model {self._model!r} has not been verified",
                                  [self._model])
        # No model-listing endpoint is verified; a real probe costs quota, so it is explicit (probe()).
        return ProviderHealth(self.name, "unverified", "configured; call probe() to test a live request",
                              [self._model])

    def probe(self) -> ProviderHealth:
        """Send a minimal live request. Consumes a small amount of quota."""
        try:
            completion = self._chat([{"role": "user", "content": 'Reply with {"ok": true}'}], max_tokens=20)
        except ProviderError as exc:
            return ProviderHealth(self.name, "error", str(exc), [self._model])
        return ProviderHealth(self.name, "ok", f"live request succeeded ({completion.model})", [completion.model])

    def _complete(self, system: str, user: str, image_data_url: str | None, data: ClassificationInput) -> Completion:
        content: str | list[dict] = user
        if image_data_url:
            content = [{"type": "text", "text": user}, {"type": "image_url", "image_url": {"url": image_data_url}}]
        messages = [{"role": "system", "content": system}, {"role": "user", "content": content}]
        return self._chat(messages, max_tokens=self._max_output)

    def _chat(self, messages: list[dict], *, max_tokens: int) -> Completion:
        if not self._api_key:
            raise ProviderError("config", "CATEGORY_EVREN_API_KEY is not set")
        body: dict = {"model": self._model, "messages": messages, "max_tokens": max_tokens, "stream": False}
        if self._json_mode:
            body["response_format"] = {"type": "json_object"}
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
            "Idempotency-Key": str(uuid.uuid4()),  # same key on retries -> gateway de-duplicates
        }
        url = f"{self._base_url}/v1/chat/completions"
        resp: httpx.Response | None = None
        for attempt in range(self._max_attempts):
            try:
                resp = self._client.post(url, json=body, headers=headers)
            except httpx.HTTPError as exc:
                if attempt == self._max_attempts - 1:
                    raise ProviderError("connection", type(exc).__name__) from exc
            else:
                if resp.status_code not in _RETRY_STATUSES or attempt == self._max_attempts - 1:
                    break
            time.sleep(min(2 + 3 * attempt, 10))
        assert resp is not None
        if resp.status_code != 200:
            raise ProviderError(_ERROR_KINDS.get(resp.status_code, "status"), _error_detail(resp), resp.status_code)
        try:
            payload = resp.json()
        except ValueError as exc:
            raise ProviderError("status", "response is not JSON") from exc
        choice = (payload.get("choices") or [{}])[0]
        usage = payload.get("usage") or {}
        model = str(payload.get("model") or self._model).rsplit("/", 1)[-1]
        return Completion(
            text=(choice.get("message") or {}).get("content") or "",
            model=model,
            input_tokens=usage.get("prompt_tokens"),
            output_tokens=usage.get("completion_tokens"),
            request_id=payload.get("id") or resp.headers.get("x-request-id"),
            finish_reason=choice.get("finish_reason"),
        )


def _error_detail(resp: httpx.Response) -> str:
    try:
        err = resp.json().get("error") or {}
        detail = err.get("message") or err.get("code") if isinstance(err, dict) else err
    except ValueError:
        detail = None
    return str(detail or resp.text[:200])[:300]
