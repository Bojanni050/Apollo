"""The conversational agent.

A deliberately simple loop: send the conversation to the model, let it call
read-only tools, feed the results back, repeat until it answers. No autonomous
planning, no background cognition, no agent framework.

Two invariants matter here:

1. The agent holds only read tools. It cannot modify a file.
2. Switching mode never discards the conversation; only the system prompt
   changes, so an architectural discussion survives a move into Apply mode.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.llm.base import Citation, LLMProvider, LLMResponse
from app.models import Conversation, Message, Repository
from app.prompts import system_prompt
from app.services.tools import ToolContext, run_tool, tool_schemas

MAX_TOOL_ITERATIONS = 8
MAX_HISTORY_MESSAGES = 40


@dataclass
class AgentResult:
    content: str
    citations: list[Citation] = field(default_factory=list)
    tool_calls: list[dict[str, Any]] = field(default_factory=list)


class Agent:
    def __init__(self, provider: LLMProvider) -> None:
        self.provider = provider

    def run(
        self,
        db: Session,
        conversation: Conversation,
        user_message: str,
        repositories: list[Repository],
    ) -> AgentResult:
        # Persist the user's turn first so the transcript survives a provider
        # failure or a dropped connection.
        db.add(
            Message(
                conversation_id=conversation.id,
                role="user",
                content=user_message,
                mode=conversation.mode,
            )
        )
        db.commit()

        ctx = ToolContext(
            workspace=conversation.workspace,
            repositories=repositories,
            citations=[],
        )

        messages: list[dict[str, Any]] = [
            {"role": "system", "content": system_prompt(conversation.mode)}
        ]
        for message in _recent_history(db, conversation.id):
            messages.append({"role": message.role, "content": message.content})

        tools = tool_schemas([r.name for r in repositories])
        executed: list[dict[str, Any]] = []

        for _ in range(MAX_TOOL_ITERATIONS):
            response: LLMResponse = self.provider.chat(messages, tools=tools)
            messages.append(_assistant_turn(response))

            if not response.tool_calls:
                return self._finish(db, conversation, response, ctx, executed)

            for call in response.tool_calls:
                result = run_tool(call.name, ctx, call.arguments)
                executed.append(
                    {
                        "tool": call.name,
                        "arguments": call.arguments,
                        "result_preview": result[:500],
                    }
                )
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": call.id,
                        "name": call.name,
                        "content": result,
                    }
                )

        # Iteration budget exhausted: ask for a final answer with what we have.
        messages.append(
            {
                "role": "user",
                "content": (
                    "Provide your final answer now, based on the evidence gathered so far."
                ),
            }
        )
        final = self.provider.chat(messages, tools=None)
        return self._finish(db, conversation, final, ctx, executed)

    def _finish(
        self,
        db: Session,
        conversation: Conversation,
        response: LLMResponse,
        ctx: ToolContext,
        executed: list[dict[str, Any]],
    ) -> AgentResult:
        content = response.content or "(The model returned an empty response.)"
        citations = _dedupe(ctx.citations)

        db.add(
            Message(
                conversation_id=conversation.id,
                role="assistant",
                content=content,
                mode=conversation.mode,
                citations=[c.to_dict() for c in citations],
                tool_calls=executed,
            )
        )
        db.commit()
        return AgentResult(content=content, citations=citations, tool_calls=executed)


def _assistant_turn(response: LLMResponse) -> dict[str, Any]:
    """Render an assistant turn in OpenAI wire format, preserving tool calls."""
    turn: dict[str, Any] = {"role": "assistant", "content": response.content or None}
    if response.tool_calls:
        turn["tool_calls"] = [
            {
                "id": call.id,
                "type": "function",
                "function": {
                    "name": call.name,
                    "arguments": json.dumps(call.arguments),
                },
            }
            for call in response.tool_calls
        ]
    return turn


def _recent_history(db: Session, conversation_id: int, limit: int = MAX_HISTORY_MESSAGES):
    """The most recent turns, so long discussions stay within context limits."""
    total = db.scalar(
        select(func.count(Message.id)).where(Message.conversation_id == conversation_id)
    )
    if not total:
        return []
    first = max(0, total - limit)
    return (
        db.scalars(
            select(Message)
            .where(Message.conversation_id == conversation_id, Message.id > first)
            .order_by(Message.id)
        )
        .all()
    )


def _dedupe(citations: list[Citation]) -> list[Citation]:
    """Collapse repeated references, keeping the strongest evidence type.

    A file read both as code and as documentation is reported once, as
    verified implementation -- the stronger claim.
    """
    priority = {
        "verified_implementation": 0,
        "explicit_decision": 1,
        "documented_intention": 2,
        "ai_interpretation": 3,
        "uncertainty": 4,
    }
    best: dict[tuple[str, str], Citation] = {}
    for citation in citations:
        key = (citation.repository, citation.path)
        existing = best.get(key)
        if existing is None or priority.get(citation.evidence_type, 9) < priority.get(
            existing.evidence_type, 9
        ):
            best[key] = citation
    return list(best.values())
