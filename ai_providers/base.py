"""Provider interface. A new provider implements ``_complete`` (one model call) and ``health``; prompt
building, parsing and validation are shared so every provider is held to the same contract."""
from __future__ import annotations

import abc
import base64
import time

from classification.taxonomy import Taxonomy, default_taxonomy
from classification.validation import parse_answer

from .prompting import PROMPT_VERSION, build_prompt
from .schemas import ClassificationInput, Completion, ProviderHealth, ProviderResult, ValidationStatus


class ProviderError(Exception):
    """A provider call failed. ``kind`` is stable: auth, credit, permission, rate, connection, status, config."""

    def __init__(self, kind: str, detail: str = "", status_code: int | None = None) -> None:
        super().__init__(f"{kind}: {detail}" if detail else kind)
        self.kind, self.detail, self.status_code = kind, detail, status_code

    @property
    def retryable(self) -> bool:
        return self.kind in {"rate", "connection"} or (self.status_code or 0) >= 500


class AIProvider(abc.ABC):
    name: str = "base"

    def __init__(self, *, taxonomy: Taxonomy | None = None, max_text_chars: int = 12_000,
                 max_html_chars: int = 6_000) -> None:
        self.taxonomy = taxonomy or default_taxonomy()
        self.max_text_chars = max_text_chars
        self.max_html_chars = max_html_chars

    @property
    @abc.abstractmethod
    def model(self) -> str: ...

    @property
    @abc.abstractmethod
    def supports_images(self) -> bool: ...

    @abc.abstractmethod
    def _complete(self, system: str, user: str, image_data_url: str | None, data: ClassificationInput) -> Completion:
        """One model call. Raise ProviderError on failure."""

    @abc.abstractmethod
    def health(self) -> ProviderHealth: ...

    def classify(self, data: ClassificationInput) -> ProviderResult:
        system, user, signals = build_prompt(
            data, self.taxonomy, max_text_chars=self.max_text_chars, max_html_chars=self.max_html_chars
        )
        image = None
        if data.screenshot is not None and self.supports_images:
            image = f"data:{data.screenshot.mime_type};base64,{base64.b64encode(data.screenshot.data).decode()}"
        result = ProviderResult(
            provider=self.name, model=self.model, prompt_version=PROMPT_VERSION,
            taxonomy_version=self.taxonomy.version, status=ValidationStatus.PROVIDER_ERROR,
            used_screenshot=image is not None, injection_signals=signals,
        )
        started = time.monotonic()
        try:
            completion = self._complete(system, user, image, data)
        except ProviderError as exc:
            result.errors = [str(exc)]
            result.latency_ms = int((time.monotonic() - started) * 1000)
            return result
        result.latency_ms = int((time.monotonic() - started) * 1000)
        result.model = completion.model or self.model
        result.raw_text = completion.text
        result.input_tokens, result.output_tokens = completion.input_tokens, completion.output_tokens
        result.request_id = completion.request_id

        parsed = parse_answer(completion.text, self.taxonomy, data.domain)
        result.status, result.output, result.raw_json, result.errors = (
            parsed.status, parsed.output, parsed.raw_json, parsed.errors
        )
        if completion.finish_reason == "length":
            result.errors.append("answer truncated (finish_reason=length)")
        if result.output is not None and signals:
            result.output.requires_human_review = True  # injection attempts always go to a person
        return result
