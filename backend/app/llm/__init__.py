"""Provider selection.

Keeps provider-specific knowledge in one place so the rest of the application
only ever sees the :class:`LLMProvider` protocol.
"""
from __future__ import annotations

from app.config import settings
from app.llm.base import LLMError, LLMNotConfigured, LLMProvider
from app.llm.openai_compat import OpenAICompatibleProvider

# Registry of available providers. Adding one here is enough to make it
# selectable; no changes to the agent or API layer are required.
PROVIDERS = {
    "openai_compatible": OpenAICompatibleProvider,
}

DEFAULT_PROVIDER = "openai_compatible"


def get_provider(name: str | None = None, role: str = "primary") -> LLMProvider:
    """Return the configured provider for a role.

    ``role="primary"`` is the strong model for the interactive chat and ADR
    checks; ``role="background"`` is the cheap model for bulk work, falling
    back to the primary settings when no background model is configured.
    

    Raises :class:`LLMNotConfigured` rather than failing at import time, so the
    app still starts (and the document explorer still works) without an LLM.
    """
    key = (name or DEFAULT_PROVIDER).strip().lower()
    factory = PROVIDERS.get(key)
    if factory is None:
        available = ", ".join(sorted(PROVIDERS))
        raise LLMError(f"Unknown LLM provider {key!r}. Available: {available}.")

    if role == "background" and settings.background_llm_model:
        return factory(
            base_url=settings.background_llm_base_url or settings.llm_base_url,
            api_key=settings.background_llm_api_key or settings.llm_api_key,
            model=settings.background_llm_model,
        )
    return factory()


class BackgroundSettingsView:
    """Settings as the *background* model sees them.

    ``ContextBudget.from_settings`` reads ``llm_context_tokens`` and
    ``llm_max_output_tokens``; for the background role those must be the
    BACKGROUND_LLM_* values (with primary fallback), because a cheap model
    typically has a smaller window than the chat model.
    """

    def __init__(self) -> None:
        self.llm_context_tokens = (
            settings.background_llm_context_tokens or settings.llm_context_tokens
        )
        self.llm_max_output_tokens = (
            settings.background_llm_max_output_tokens or settings.llm_max_output_tokens
        )


def is_configured(role: str = "primary") -> bool:
    """True when an LLM endpoint and model are available for the role."""
    if role == "background" and settings.background_llm_model:
        return bool(
            (settings.background_llm_base_url or settings.llm_base_url)
            and settings.background_llm_model
        )
    return bool(settings.llm_base_url and settings.llm_model)


def available_providers() -> list[str]:
    return sorted(PROVIDERS)
