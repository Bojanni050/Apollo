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


def get_provider(name: str | None = None) -> LLMProvider:
    """Return the configured provider.

    Raises :class:`LLMNotConfigured` rather than failing at import time, so the
    app still starts (and the document explorer still works) without an LLM.
    """
    key = (name or DEFAULT_PROVIDER).strip().lower()
    factory = PROVIDERS.get(key)
    if factory is None:
        available = ", ".join(sorted(PROVIDERS))
        raise LLMError(f"Unknown LLM provider {key!r}. Available: {available}.")
    return factory()


def is_configured() -> bool:
    """True when an LLM endpoint and model are available."""
    return bool(settings.llm_base_url and settings.llm_model)


def available_providers() -> list[str]:
    return sorted(PROVIDERS)
