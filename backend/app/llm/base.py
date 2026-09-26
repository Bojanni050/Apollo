"""LLM provider abstraction.

Application code depends only on the :class:`LLMProvider` protocol, never on a
vendor SDK. Adding a provider later means implementing this interface and
registering a factory -- no changes to the agent or the API layer.

No provider is hardcoded, and no model identifier is hardcoded: both come from
configuration.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

# Evidence categories an assistant claim may carry. These are deliberately
# coarse and honest: an AI interpretation must never masquerade as verified
# implementation.
EVIDENCE_TYPES = (
    "verified_implementation",
    "explicit_decision",
    "documented_intention",
    "ai_interpretation",
    "uncertainty",
)


class LLMError(RuntimeError):
    """Raised when the provider call fails in a way the user should see."""


class LLMNotConfigured(LLMError):
    """Raised when no provider is configured, so the app can degrade clearly."""


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any]


@dataclass
class LLMResponse:
    content: str
    tool_calls: list[ToolCall] = field(default_factory=list)
    finish_reason: str | None = None
    raw_usage: dict[str, Any] | None = None


@dataclass
class Citation:
    """A traceable reference backing an architectural claim."""

    repository: str
    path: str
    start_line: int | None = None
    end_line: int | None = None
    revision: str | None = None
    evidence_type: str = "ai_interpretation"
    note: str | None = None
    decision_id: int | None = None
    question_id: int | None = None

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "repository": self.repository,
            "path": self.path,
            "start_line": self.start_line,
            "end_line": self.end_line,
            "revision": self.revision,
            "evidence_type": self.evidence_type,
            "note": self.note,
        }
        if self.decision_id is not None:
            d["decision_id"] = self.decision_id
        if self.question_id is not None:
            d["question_id"] = self.question_id
        return d


@runtime_checkable
class LLMProvider(Protocol):
    """Minimal surface the application needs from any provider."""

    def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        temperature: float | None = None,
        max_output_tokens: int | None = None,
    ) -> LLMResponse:
        """Return a single assistant turn, possibly requesting tool calls."""
        ...


def parse_json_arguments(raw: str) -> dict[str, Any]:
    """Tolerantly parse tool-call arguments.

    Providers are inconsistent about whether arguments arrive as a JSON object
    or a stringified object, and some emit trailing text. Failures degrade to
    an empty dict so a malformed call cannot crash the agent loop.
    """
    if not raw:
        return {}
    if isinstance(raw, dict):
        return raw
    try:
        parsed = json.loads(raw)
    except (TypeError, ValueError):
        return {}
    return parsed if isinstance(parsed, dict) else {}
