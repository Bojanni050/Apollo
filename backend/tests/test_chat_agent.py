"""Tests for the chat agent using a scripted fake provider.

No network access: the fake provider replays a fixed sequence of turns so the
tool-calling loop, citation collection and mode handling can be verified
deterministically.
"""
from __future__ import annotations

from typing import Any

import pytest
from sqlalchemy.orm import Session

from app.llm.base import LLMError, LLMProvider, LLMResponse, ToolCall
from app.models import Conversation, Message, Repository, Workspace
from app.services.agent import Agent
from app.services.tools import ToolContext, run_tool, tool_schemas


class ScriptedProvider(LLMProvider):
    """Replays prepared turns; records the messages it was given."""

    def __init__(self, turns: list[LLMResponse]) -> None:
        self.turns = list(turns)
        self.calls: list[list[dict[str, Any]]] = []
        self.tools_offered: list[Any] = []

    def chat(self, messages, tools=None, temperature=None, max_output_tokens=None):
        self.calls.append(messages)
        self.tools_offered.append(tools)
        if not self.turns:
            return LLMResponse(content="done")
        return self.turns.pop(0)


def tool_turn(name: str, arguments: dict[str, Any], call_id: str = "c1") -> LLMResponse:
    return LLMResponse(
        content="",
        tool_calls=[ToolCall(id=call_id, name=name, arguments=arguments)],
    )


@pytest.fixture()
def repos(session: Session, workspace: dict) -> list[Repository]:
    return (
        session.query(Repository)
        .filter(Repository.workspace_id == workspace["id"])
        .order_by(Repository.id)
        .all()
    )


def _context(session: Session, workspace: dict) -> ToolContext:
    ws = session.get(Workspace, workspace["id"])
    return ToolContext(
        workspace=ws,
        repositories=session.query(Repository)
        .filter(Repository.workspace_id == ws.id)
        .all(),
        citations=[],
    )


def _conversation(session: Session, workspace: dict, mode: str = "explore") -> Conversation:
    conversation = Conversation(workspace_id=workspace["id"], title="t", mode=mode)
    session.add(conversation)
    session.commit()
    return conversation


# --------------------------------------------------------------------------
# Tool behaviour
# --------------------------------------------------------------------------


def test_read_document_tool_returns_numbered_lines(
    session: Session, workspace: dict
) -> None:
    out = run_tool(
        "read_document", _context(session, workspace), {"repository": "gaia-docs", "path": "notes.md"}
    )
    assert "1| # Scratch" in out
    assert "lines 1-" in out


def test_read_document_tool_blocks_traversal(session: Session, workspace: dict) -> None:
    out = run_tool(
        "read_document",
        _context(session, workspace),
        {"repository": "gaia-docs", "path": "../../../Windows/win.ini"},
    )
    assert out.startswith("Error:")
    assert "traversal" in out.lower()


def test_read_code_tool_records_verified_evidence(
    session: Session, workspace: dict
) -> None:
    ctx = _context(session, workspace)
    run_tool("read_code", ctx, {"repository": "gaia-service", "path": "src/memory.py"})
    assert ctx.citations
    assert ctx.citations[0].evidence_type == "verified_implementation"
    assert ctx.citations[0].repository == "gaia-service"
    assert ctx.citations[0].revision  # Git revision captured


def test_unknown_tool_is_reported_not_fatal(session: Session, workspace: dict) -> None:
    out = run_tool("delete_everything", _context(session, workspace), {})
    assert "unknown tool" in out.lower()


def test_hallucinated_arguments_are_ignored(session: Session, workspace: dict) -> None:
    """An invented argument must not raise; unknown keys are dropped."""
    out = run_tool(
        "read_document",
        _context(session, workspace),
        {"repository": "gaia-docs", "path": "notes.md", "sudo": True, "rm": "-rf /"},
    )
    assert "1| # Scratch" in out


def test_unknown_repository_is_reported(session: Session, workspace: dict) -> None:
    out = run_tool(
        "read_document", _context(session, workspace), {"repository": "nope", "path": "notes.md"}
    )
    assert "Unknown repository" in out


def test_tool_schemas_only_expose_read_tools() -> None:
    names = {t["function"]["name"] for t in tool_schemas(["gaia-docs"])}
    assert names == {
        "list_documents",
        "read_document",
        "search_documents",
        "read_code",
        "list_decisions",
        "structure",
        "search_code",
        "read_source",
        "list_source_files",
        "source_structure",
        "search_questions",
        "search_decisions",
        "get_question",
        "get_decision",
        "draft_question",
        "draft_decision",
        "check_architectural_consistency",
        "semantic_search_code",
        "semantic_search_documents",
        "hybrid_search",
    }
    # No tool may be capable of writing. This is the structural guarantee that
    # the AI cannot modify a file or database, whatever it is told.
    assert not any(
        verb in name
        for name in names
        for verb in ("write", "move", "delete", "create", "edit", "remove", "apply")
    )

# --------------------------------------------------------------------------
# Agent loop
# --------------------------------------------------------------------------


def test_agent_executes_tool_then_answers(
    session: Session, workspace: dict, repos: list
) -> None:
    conversation = _conversation(session, workspace, "investigate")
    provider = ScriptedProvider(
        [
            tool_turn("search_documents", {"query": "memory"}),
            LLMResponse(content="Memory is documented in architecture/components."),
        ]
    )
    result = Agent(provider).run(session, conversation, "where is memory?", repos)

    assert "Memory is documented" in result.content
    assert len(result.tool_calls) == 1
    assert result.tool_calls[0]["tool"] == "search_documents"
    assert result.citations


def test_agent_persists_both_messages(
    session: Session, workspace: dict, repos: list
) -> None:
    conversation = _conversation(session, workspace)
    Agent(ScriptedProvider([LLMResponse(content="hello")])).run(
        session, conversation, "hi", repos
    )

    messages = (
        session.query(Message).filter_by(conversation_id=conversation.id).all()
    )
    assert [m.role for m in messages] == ["user", "assistant"]
    assert messages[0].content == "hi"
    assert messages[1].mode == "explore"


def test_agent_offers_tools_on_every_turn(
    session: Session, workspace: dict, repos: list
) -> None:
    conversation = _conversation(session, workspace, "investigate")
    provider = ScriptedProvider(
        [tool_turn("list_documents", {"repository": "gaia-docs"}), LLMResponse(content="ok")]
    )
    Agent(provider).run(session, conversation, "q", repos)
    assert all(tools for tools in provider.tools_offered)


def test_agent_dedupes_citations(session: Session, workspace: dict, repos: list) -> None:
    conversation = _conversation(session, workspace, "investigate")
    provider = ScriptedProvider(
        [
            tool_turn("read_document", {"repository": "gaia-docs", "path": "README.md"}, "a"),
            tool_turn("read_code", {"repository": "gaia-service", "path": "README.md"}, "b"),
            LLMResponse(content="done"),
        ]
    )
    result = Agent(provider).run(session, conversation, "q", repos)
    # README.md in two different repositories remains two distinct citations.
    assert len(result.citations) == 2


def test_agent_keeps_strongest_evidence_type(
    session: Session, workspace: dict, repos: list
) -> None:
    conversation = _conversation(session, workspace, "investigate")
    provider = ScriptedProvider(
        [
            tool_turn("read_document", {"repository": "gaia-docs", "path": "notes.md"}, "a"),
            tool_turn("read_code", {"repository": "gaia-docs", "path": "notes.md"}, "b"),
            LLMResponse(content="done"),
        ]
    )
    result = Agent(provider).run(session, conversation, "q", repos)
    assert len(result.citations) == 1
    assert result.citations[0].evidence_type == "verified_implementation"


def test_agent_stops_at_iteration_limit(
    session: Session, workspace: dict, repos: list
) -> None:
    """A model that loops forever must not hang the request."""
    conversation = _conversation(session, workspace)
    # Exactly MAX_TOOL_ITERATIONS tool calls, then a final answer. The scripted
    # list must not contain a spare tool turn, or the post-loop fallback would
    # itself be a tool call and return no content.
    looping = [
        tool_turn("list_documents", {"repository": "gaia-docs"}, f"c{i}")
        for i in range(8)
    ]
    provider = ScriptedProvider(looping + [LLMResponse(content="final answer")])
    result = Agent(provider).run(session, conversation, "q", repos)

    assert result.content == "final answer"
    assert len(result.tool_calls) == 8
    # The fallback request must not offer tools again.
    assert provider.tools_offered[-1] is None


def test_user_message_survives_provider_failure(
    session: Session, workspace: dict, repos: list
) -> None:
    """If the LLM errors, the user's turn is already committed."""

    class Broken(LLMProvider):
        def chat(self, *args, **kwargs):
            raise LLMError("boom")

    conversation = _conversation(session, workspace)
    with pytest.raises(LLMError):
        Agent(Broken()).run(session, conversation, "important question", repos)

    saved = session.query(Message).filter_by(conversation_id=conversation.id).all()
    assert [m.role for m in saved] == ["user"]
    assert saved[0].content == "important question"


def test_mode_changes_system_prompt_only(
    session: Session, workspace: dict, repos: list
) -> None:
    """Switching mode must change the prompt without discarding the transcript."""
    conversation = _conversation(session, workspace, "explore")
    Agent(ScriptedProvider([LLMResponse(content="first")])).run(
        session, conversation, "an idea", repos
    )

    conversation.mode = "apply"
    session.commit()

    provider = ScriptedProvider([LLMResponse(content="second")])
    Agent(provider).run(session, conversation, "now apply it", repos)

    second_prompt = provider.calls[0][0]["content"]
    assert "MODE: APPLY" in second_prompt
    # The earlier exchange is still present in the context.
    assert any(m.get("content") == "an idea" for m in provider.calls[0])


def test_tool_failure_does_not_break_the_loop(
    session: Session, workspace: dict, repos: list
) -> None:
    conversation = _conversation(session, workspace, "investigate")
    provider = ScriptedProvider(
        [
            tool_turn("read_document", {"repository": "gaia-docs", "path": "../../escape.md"}),
            LLMResponse(content="I could not read that file."),
        ]
    )
    result = Agent(provider).run(session, conversation, "q", repos)

    assert result.content == "I could not read that file."
    # The model was shown the error and could recover.
    blob = " ".join(
        str(m.get("content", "")) for m in provider.calls[1]
    ).lower()
    assert "traversal" in blob
