"""OpenAI-compatible chat provider.

Works with any endpoint that speaks the OpenAI ``/chat/completions`` protocol
(OpenAI itself, Ollama, vLLM, LM Studio, OpenRouter, Together, ...). Only the
standard library is used, so the application gains no vendor dependency.

Configuration (endpoint, key, model, limits) comes entirely from settings; no
model is hardcoded.
"""
from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any

from app.config import settings
from app.llm.base import LLMError, LLMNotConfigured, LLMResponse, ToolCall, parse_json_arguments


class OpenAICompatibleProvider:
    """A provider speaking the OpenAI chat-completions protocol."""

    def __init__(
        self,
        base_url: str | None = None,
        api_key: str | None = None,
        model: str | None = None,
        timeout: int | None = None,
    ) -> None:
        self.base_url = (base_url if base_url is not None else settings.llm_base_url or "").rstrip("/")
        self.api_key = api_key if api_key is not None else settings.llm_api_key
        self.model = model if model is not None else settings.llm_model
        self.timeout = timeout or settings.llm_timeout_seconds

        if not self.base_url or not self.model:
            raise LLMNotConfigured(
                "No LLM provider configured. Set LLM_BASE_URL and LLM_MODEL in .env "
                "(LLM_API_KEY only if your endpoint requires one)."
            )

    @property
    def chat_url(self) -> str:
        return f"{self.base_url}/chat/completions"

    def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        temperature: float | None = None,
        max_output_tokens: int | None = None,
    ) -> LLMResponse:
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "temperature": settings.llm_temperature if temperature is None else temperature,
            "max_tokens": max_output_tokens or settings.llm_max_output_tokens,
        }
        if tools:
            payload["tools"] = tools
            # Providers reject an explicit temperature alongside some tool modes.
            payload["tool_choice"] = "auto"

        request = urllib.request.Request(
            self.chat_url,
            data=json.dumps(payload).encode("utf-8"),
            headers=self._headers(),
            method="POST",
        )

        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                body = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")[:500]
            raise LLMError(f"LLM endpoint returned HTTP {exc.code}: {detail}") from exc
        except urllib.error.URLError as exc:
            raise LLMError(f"Could not reach the LLM endpoint: {exc.reason}") from exc
        except json.JSONDecodeError as exc:
            raise LLMError("LLM endpoint returned a non-JSON response.") from exc

        return self._parse(body)

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        return headers

    def _parse(self, body: dict[str, Any]) -> LLMResponse:
        choices = body.get("choices") or []
        if not choices:
            raise LLMError("LLM response contained no choices.")

        message = choices[0].get("message") or {}
        tool_calls: list[ToolCall] = []
        for raw in message.get("tool_calls") or []:
            function = raw.get("function") or {}
            tool_calls.append(
                ToolCall(
                    id=raw.get("id", ""),
                    name=function.get("name", ""),
                    arguments=parse_json_arguments(function.get("arguments", "")),
                )
            )

        return LLMResponse(
            content=message.get("content") or "",
            tool_calls=tool_calls,
            finish_reason=choices[0].get("finish_reason"),
            raw_usage=body.get("usage"),
        )
