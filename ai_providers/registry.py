"""Provider registry. Adding a provider = write an AIProvider subclass + register a factory here."""
from __future__ import annotations

from collections.abc import Callable

from common.config import Settings, get_settings

from .base import AIProvider
from .evren import EvrenProvider
from .mock import MockProvider

Factory = Callable[[Settings], AIProvider]
_FACTORIES: dict[str, Factory] = {}


def register(name: str) -> Callable[[Factory], Factory]:
    def deco(factory: Factory) -> Factory:
        _FACTORIES[name] = factory
        return factory
    return deco


@register("mock")
def _mock(settings: Settings) -> AIProvider:
    return MockProvider(max_text_chars=settings.max_text_chars, max_html_chars=settings.max_html_chars)


@register("evren")
def _evren(settings: Settings) -> AIProvider:
    return EvrenProvider(
        api_key=settings.evren_api_key.get_secret_value() if settings.evren_api_key else None,
        base_url=settings.evren_base_url, model=settings.evren_model, timeout=settings.evren_timeout_seconds,
        deadline=settings.evren_deadline_seconds,
        max_output_tokens=settings.evren_max_output_tokens,
        json_mode=settings.evren_json_mode, max_text_chars=settings.max_text_chars,
        max_html_chars=settings.max_html_chars,
    )


def available() -> list[str]:
    return sorted(_FACTORIES)


def get_provider(name: str | None = None, settings: Settings | None = None) -> AIProvider:
    settings = settings or get_settings()
    name = name or settings.ai_provider
    try:
        return _FACTORIES[name](settings)
    except KeyError:
        raise ValueError(f"unknown AI provider {name!r}; available: {', '.join(available())}") from None


def get_second_opinion(settings: Settings | None = None) -> AIProvider | None:
    """A different model for the extra-validation band, or None if not configured."""
    settings = settings or get_settings()
    if settings.ai_provider == "evren" and settings.evren_second_model \
            and settings.evren_second_model != settings.evren_model:
        return EvrenProvider(
            api_key=settings.evren_api_key.get_secret_value() if settings.evren_api_key else None,
            base_url=settings.evren_base_url, model=settings.evren_second_model,
            timeout=settings.evren_timeout_seconds,
            deadline=settings.evren_deadline_seconds,
            max_output_tokens=settings.evren_max_output_tokens, json_mode=settings.evren_json_mode,
            max_text_chars=settings.max_text_chars, max_html_chars=settings.max_html_chars,
        )
    return None


def report(settings: Settings | None = None, *, probe: bool = False) -> dict:
    """Status of the active provider as seen by the process that classifies (the worker holds the API key,
    the public API container does not). ``probe`` sends one tiny live request."""
    settings = settings or get_settings()
    active = get_provider(settings=settings)
    health = active.probe() if probe and hasattr(active, "probe") else active.health()
    second = get_second_opinion(settings)
    return {"provider": health.provider, "model": active.model, "status": health.status, "detail": health.detail,
            "supports_images": active.supports_images,
            "second_opinion": {"provider": second.name, "model": second.model} if second else None}
