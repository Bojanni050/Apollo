"""The conversational agent.

A deliberately simple loop: send the conversation to the model, let it call
read-only tools, feed the results back, repeat until it answers. No autonomous
planning, no background cognition, no agent framework.

Three invariants matter here:

1. The agent holds only read tools. It cannot modify a file.
2. Switching mode never discards the conversation; only the system prompt
   changes, so an architectural discussion survives a move into Apply mode.
3. **Every request fits the configured context window.** The budget in
   :mod:`app.llm.context` decides what history and tool output are admitted, so
   the model is never sent a request it is bound to reject.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.llm.base import Citation, LLMProvider, LLMResponse
from app.llm.context import (
    ContextBudget,
    ContextBudgetError,
    Turn,
    history_turns,
)
from app.models import Conversation, Message, Repository
from app.prompts import DocumentFocus, system_prompt
from app.services.tools import ToolContext, run_tool, tool_schemas

logger = logging.getLogger("apollo")

MAX_TOOL_ITERATIONS = 8

#: A fetch bound only, so a very long conversation does not load an unbounded
#: number of rows from the database. This is NOT the context limit: which
#: messages actually reach the model is decided by the token budget, which is
#: the real constraint. Raising it costs nothing but memory.
MAX_HISTORY_FETCH = 200


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
        focus: DocumentFocus | None = None,
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
            db=db,
        )

        system = system_prompt(conversation.mode, focus)
        tools = tool_schemas([r.name for r in repositories])
        budget = self._budget()
        executed: list[dict[str, Any]] = []

        # History is loaded, but not yet committed to the request: the planner
        # decides how much of it actually fits alongside everything else.
        history = history_turns(
            _fetch_history(db, conversation.id), max_messages=MAX_HISTORY_FETCH
        )
        current = Turn(
            messages=[{"role": "user", "content": user_message}],
            kind="current",
            # Above every history turn, so it is admitted first and only
            # truncated when nothing else is left to give.
            priority=10**6,
            mandatory=True,
            label="current message",
        )

        turns: list[Turn] = [*history, current]

        for _ in range(MAX_TOOL_ITERATIONS):
            messages, report = budget.plan(
                system=system, turns=turns, tools=tools, current_query=user_message
            )
            _log_report(report)

            response: LLMResponse = self.provider.chat(messages, tools=tools)
            assistant_turn = _assistant_turn(response)
            turns.append(
                Turn(
                    messages=[assistant_turn],
                    # The model's own turn stays attached to its tool results.
                    kind="history",
                    priority=len(turns),
                    label="assistant turn",
                )
            )

            if not response.tool_calls:
                return self._finish(db, conversation, response, ctx, executed)

            # One turn holds the assistant's request plus every result, so the
            # pair can never be separated by the planner.
            results: list[dict[str, Any]] = []
            for call in response.tool_calls:
                result = run_tool(call.name, ctx, call.arguments)
                executed.append(
                    {
                        "tool": call.name,
                        "arguments": call.arguments,
                        "result_preview": result[:500],
                    }
                )
                results.append(
                    {
                        "role": "tool",
                        "tool_call_id": call.id,
                        "name": call.name,
                        "content": result,
                    }
                )

            turns[-1] = Turn(
                messages=[assistant_turn, *results],
                kind="tool",
                # Newest evidence is the most useful evidence, so tool turns
                # outrank old conversation history.
                priority=10**5 + len(turns),
                label="tool results",
            )

        # Iteration budget exhausted: ask for a final answer with what we have.
        turns.append(
            Turn(
                messages=[
                    {
                        "role": "user",
                        "content": (
                            "Provide your final answer now, based on the evidence "
                            "gathered so far."
                        ),
                    }
                ],
                kind="current",
                priority=10**6,
                mandatory=True,
                label="final-answer request",
            )
        )
        messages, report = budget.plan(
            system=system, turns=turns, tools=None, current_query=user_message
        )
        _log_report(report)
        final = self.provider.chat(messages, tools=None)
        return self._finish(db, conversation, final, ctx, executed)

    @staticmethod
    def _budget() -> ContextBudget:
        """The configured context budget for one model call.

        A misconfigured window is a configuration problem, not a user error, so
        it is reported as one rather than being silently clamped.
        """
        try:
            return ContextBudget.from_settings(settings)
        except ValueError as exc:
            raise ContextBudgetError(
                f"LLM context configuration is invalid: {exc}"
            ) from exc

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


def _fetch_history(db: Session, conversation_id: int, limit: int = MAX_HISTORY_FETCH):
    """Load the most recent stored turns, returned oldest-first.

    A plain bounded fetch. Which of these reach the model is decided later by the
    token budget, so this limit is about memory, not context.
    """
    rows = (
        db.scalars(
            select(Message)
            .where(Message.conversation_id == conversation_id)
            .order_by(Message.id.desc())
            .limit(limit)
        )
        .all()
    )
    # Queried newest-first for the LIMIT to be cheap, then reversed so the
    # transcript reads chronologically.
    return list(reversed(rows))


def _log_report(report) -> None:
    """Surface budget decisions: silent truncation would be a lie by omission."""
    if not report.notes:
        return
    logger.info(
        "Context budget: %d/%d tokens, kept %d turn(s), dropped %d, truncated %d. %s",
        report.total_tokens,
        report.input_budget,
        report.kept_turns,
        report.dropped_turns,
        report.truncated_turns,
        "; ".join(report.notes),
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
    best: dict[tuple[str, str, int | None, int | None], Citation] = {}
    for citation in citations:
        key = (
            citation.repository,
            citation.path,
            citation.decision_id,
            citation.question_id,
        )
        existing = best.get(key)
        if existing is None or priority.get(citation.evidence_type, 9) < priority.get(
            existing.evidence_type, 9
        ):
            best[key] = citation
    return list(best.values())
