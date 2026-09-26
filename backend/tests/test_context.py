"""Tests for context budgeting.

The central property, asserted repeatedly below: **whatever the conversation
contains, the request handed to the provider fits the configured window.**

These tests deliberately use small, artificial limits (a few thousand tokens)
rather than the production default. A budget that only fails at 128k is a budget
that is never actually exercised.
"""
from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy.orm import Session

from app.config import MAX_CONTEXT_TOKENS, MIN_CONTEXT_TOKENS, Settings
from app.llm.base import LLMResponse
from app.llm.context import (
    ContextBudget,
    ContextBudgetError,
    Turn,
    count_message_tokens,
    count_tokens,
    truncate_document,
    truncate_text,
)
from app.models import Conversation, Repository
from app.services.agent import Agent
from tests.test_chat_agent import ScriptedProvider, tool_turn

# A small window so the interesting paths (dropping, truncating) actually run.
SMALL = 4_000
OUTPUT = 500


def budget(context: int = SMALL, output: int = OUTPUT) -> ContextBudget:
    return ContextBudget(context, output)


def user_turn(text: str, priority: int = 0) -> Turn:
    return Turn(messages=[{"role": "user", "content": text}], kind="history",
                priority=priority)


def current_turn(text: str) -> Turn:
    return Turn(
        messages=[{"role": "user", "content": text}],
        kind="current",
        priority=10**6,
        mandatory=True,
        label="current",
    )


def assert_within(context_budget: ContextBudget, messages, tools=None) -> None:
    """The invariant every other test leans on."""
    total = context_budget.total_tokens(messages, tools)
    assert total + context_budget.max_output_tokens <= context_budget.context_tokens, (
        f"request of {total:,} tokens + {context_budget.max_output_tokens:,} output "
        f"exceeds the {context_budget.context_tokens:,} token window"
    )


# ---------------------------------------------------------------------------
# Token estimation
# ---------------------------------------------------------------------------


def test_token_estimate_is_proportional_to_length() -> None:
    assert count_tokens("") == 0
    assert count_tokens("word " * 100) > count_tokens("word " * 10)


def test_cjk_is_counted_more_densely_than_latin() -> None:
    """CJK tokenizes roughly per character, so it must cost more per char."""
    assert count_tokens("日本語のテキスト") * 2 > count_tokens("abcd")


def test_message_overhead_is_charged() -> None:
    bare = count_message_tokens({"role": "user", "content": "hi"})
    assert bare > count_tokens("hi")


def test_tool_call_arguments_are_charged() -> None:
    """A tool call carries name and arguments, not just visible content."""
    without = count_message_tokens({"role": "assistant", "content": None})
    with_call = count_message_tokens(
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": "c1",
                    "type": "function",
                    "function": {"name": "read_document", "arguments": '{"path": "a.md"}'},
                }
            ],
        }
    )
    assert with_call > without


def test_list_content_blocks_are_counted() -> None:
    """Providers allow structured content; it must not count as zero."""
    blocks = [{"type": "text", "text": "hello world " * 20}]
    assert count_message_tokens({"role": "user", "content": blocks}) > 0


# ---------------------------------------------------------------------------
# 1. Small conversation -- everything fits untouched
# ---------------------------------------------------------------------------


def test_small_conversation_is_kept_whole() -> None:
    b = budget()
    turns = [user_turn("what is the memory component?", 0),
             current_turn("and how does it work?")]

    messages, report = b.plan(system="You are an assistant.", turns=turns)

    assert_within(b, messages)
    assert report.dropped_turns == 0
    assert report.truncated_turns == 0
    assert messages[0]["role"] == "system"
    assert "memory component" in messages[1]["content"]
    assert "how does it work" in messages[2]["content"]


def test_system_prompt_and_tools_are_always_kept() -> None:
    b = budget()
    tools = [{"type": "function",
              "function": {"name": "read_document", "description": "x" * 200}}]
    messages, _ = b.plan(system="SYSTEM" * 50, turns=[current_turn("hello")], tools=tools)
    assert messages[0]["content"] == "SYSTEM" * 50


def test_tool_schemas_are_charged_against_the_budget() -> None:
    """Tool definitions occupy the window even though they are not messages."""
    b = budget()
    big_tools = [
        {"type": "function", "function": {"name": f"tool_{i}", "description": "d" * 500}}
        for i in range(6)
    ]
    turns = [current_turn("q")] + [user_turn("history " * 200, i) for i in range(20)]

    messages, _ = b.plan(system="sys", turns=turns, tools=big_tools)
    assert_within(b, messages, big_tools)
    # A tighter window with the same content must not fit more.
    tight = ContextBudget(SMALL // 2, OUTPUT)
    tight_messages, _ = tight.plan(system="sys", turns=turns, tools=big_tools)
    assert_within(tight, tight_messages, big_tools)
    assert tight.total_tokens(tight_messages, big_tools) <= b.total_tokens(
        messages, big_tools
    )


# ---------------------------------------------------------------------------
# 2. Long conversation -- history reduced by budget, newest kept
# ---------------------------------------------------------------------------


def test_long_conversation_reduces_oldest_history() -> None:
    b = budget()
    turns = [user_turn(f"old message number {i} " + "filler " * 60, i) for i in range(60)]
    turns.append(current_turn("the current question"))

    messages, report = b.plan(system="sys", turns=turns, current_query="current question")

    assert_within(b, messages)
    assert report.dropped_turns > 0
    bodies = [m.get("content", "") for m in messages]
    # The newest history survives; the oldest does not.
    assert any("old message number 59" in body for body in bodies)
    assert not any("old message number 0 " in body for body in bodies)


def test_current_request_is_never_silently_removed() -> None:
    """Even under extreme pressure the user's question must survive."""
    b = ContextBudget(2_048, 256)
    turns = [user_turn("ancient " * 5_000, i) for i in range(30)]
    turns.append(current_turn("WHAT IS THE ANSWER TO MY QUESTION"))

    messages, _ = b.plan(system="sys", turns=turns, current_query="question")

    assert_within(b, messages)
    assert any("WHAT IS THE ANSWER TO MY QUESTION" in m.get("content", "") for m in messages)


def test_oversized_current_message_is_truncated_with_a_notice() -> None:
    """A single enormous message is cut, but visibly -- never silently."""
    b = budget()
    huge = "please analyse this. " * 20_000
    messages, report = b.plan(system="sys", turns=[current_turn(huge)])

    assert_within(b, messages)
    assert report.truncated_turns == 1
    assert "truncated" in messages[-1]["content"]


def test_history_is_reduced_by_tokens_not_by_message_count() -> None:
    """Many short messages fit where a few long ones do not.

    This is the property the old flat 40-message cut-off could not provide: the
    limit follows the content's actual cost.
    """
    b = budget()
    many_short = [user_turn("ok", i) for i in range(500)]
    messages, report = b.plan(system="sys", turns=[*many_short, current_turn("question")])
    assert_within(b, messages)
    assert report.dropped_turns == 0  # 500 tiny messages are genuinely affordable

    few_long = [user_turn("word " * 3_000, i) for i in range(10)]
    messages2, report2 = b.plan(system="sys", turns=[*few_long, current_turn("question")])
    assert_within(b, messages2)
    assert report2.dropped_turns > 0


def test_history_turns_helper_weights_recency() -> None:
    """Stored messages become turns, newest weighted highest."""
    from app.llm.context import history_turns

    turns = history_turns(
        [{"role": "user", "content": "one"},
         {"role": "assistant", "content": "two"},
         {"role": "user", "content": "three"}]
    )
    assert [t.messages[0]["content"] for t in turns] == ["one", "two", "three"]
    assert turns[-1].priority > turns[0].priority


# ---------------------------------------------------------------------------
# 3. Large document / retrieved content
# ---------------------------------------------------------------------------


def test_truncate_document_marks_truncation() -> None:
    text = "# Title\n" + ("A line of documentation.\n" * 20_000)
    out, truncated = truncate_document(text, 500, query="memory", budget=SMALL)

    assert truncated is True
    assert count_tokens(out) <= 500 * 1.3
    assert "truncated" in out
    # The model must not be able to mistake this for the whole document.
    assert len(out) < len(text)


def test_truncate_document_keeps_relevant_lines() -> None:
    body = ["irrelevant line"] * 5_000
    body.insert(4_000, "The memory component stores conversational context.")
    text = "\n".join(body)

    out, _ = truncate_document(text, 400, query="memory component stores context",
                               budget=SMALL)

    assert "memory component stores conversational context" in out


def test_small_document_is_not_truncated() -> None:
    text = "# Overview\nA short document."
    out, truncated = truncate_document(text, 1_000, query="overview", budget=SMALL)
    assert truncated is False
    assert out == text


def test_truncate_text_respects_its_budget() -> None:
    out, truncated = truncate_text("x" * 100_000, 300, reason="test", budget=SMALL)
    assert truncated is True
    assert count_tokens(out) <= 300 * 1.3
    assert "truncated" in out


def test_cjk_text_does_not_blow_the_budget() -> None:
    """CJK packs more per character, so a char-based cut must back off."""
    out, truncated = truncate_text("日本語" * 20_000, 300, reason="test", budget=SMALL)
    assert truncated is True
    assert count_tokens(out) <= 400


# ---------------------------------------------------------------------------
# 4. Tool results are budgeted as context
# ---------------------------------------------------------------------------


def test_large_tool_result_is_truncated() -> None:
    b = budget()
    turn = Turn(
        messages=[
            {
                "role": "tool",
                "tool_call_id": "c1",
                "name": "read_document",
                "content": "file line\n" * 50_000,
            }
        ],
        kind="tool",
        priority=10**5,
        label="tool results",
    )
    messages, _ = b.plan(system="sys", turns=[turn, current_turn("what does it say?")])

    assert_within(b, messages)
    tool_msg = next(m for m in messages if m.get("role") == "tool")
    assert "truncated" in tool_msg["content"]


def test_single_tool_result_cannot_swallow_the_budget() -> None:
    """A 500k-character file read must not crowd out the conversation."""
    b = budget()
    turn = Turn(
        messages=[
            {"role": "tool", "tool_call_id": "c1", "name": "read_document",
             "content": "x" * 500_000}
        ],
        kind="tool",
        priority=10**5,
    )
    messages, _ = b.plan(system="sys", turns=[turn, current_turn("the real question")])
    assert_within(b, messages)
    tool_tokens = count_message_tokens(next(m for m in messages if m.get("role") == "tool"))
    assert tool_tokens <= b.max_tool_result_tokens * 1.3


def test_assistant_and_its_tool_results_stay_together() -> None:
    """An orphaned tool result is invalid on the wire, so they are atomic."""
    b = budget()
    assistant = {
        "role": "assistant",
        "content": None,
        "tool_calls": [
            {"id": "c1", "type": "function",
             "function": {"name": "read_document", "arguments": "{}"}}
        ],
    }
    tool_turn = Turn(
        messages=[
            assistant,
            {"role": "tool", "tool_call_id": "c1", "name": "read_document",
             "content": "content " * 50_000},
        ],
        kind="tool",
        priority=10**5,
    )
    messages, _ = b.plan(system="sys", turns=[tool_turn, current_turn("q")])

    assert_within(b, messages)
    roles = [m.get("role") for m in messages]
    if "tool" in roles:
        # A tool result must be immediately preceded by its assistant request.
        assert roles[roles.index("tool") - 1] == "assistant"


def test_oldest_tool_results_are_dropped_before_recent_ones() -> None:
    b = budget()
    turns = [
        Turn(
            messages=[{"role": "tool", "tool_call_id": f"c{i}", "name": "read_document",
                       "content": "old " * 5_000}],
            kind="tool",
            priority=100 + i,
        )
        for i in range(10)
    ]
    messages, report = b.plan(system="sys", turns=[*turns, current_turn("q")])
    assert_within(b, messages)
    assert report.dropped_turns > 0


def test_search_result_lists_are_budgeted() -> None:
    """A long search listing is context too, not free."""
    b = budget()
    turn = Turn(
        messages=[
            {"role": "tool", "tool_call_id": "c1", "name": "search_documents",
             "content": "path/to/doc.md (line 1) - snippet\n" * 10_000}
        ],
        kind="tool",
        priority=10**5,
    )
    messages, _ = b.plan(system="sys", turns=[turn, current_turn("summarise")])
    assert_within(b, messages)
    assert "truncated" in next(m for m in messages if m.get("role") == "tool")["content"]


# ---------------------------------------------------------------------------
# 5. Failure handling -- exhaustion is an application error
# ---------------------------------------------------------------------------


def test_exhausted_budget_raises_an_application_error() -> None:
    """A request that cannot fit must not be sent to the provider."""
    b = ContextBudget(MIN_CONTEXT_TOKENS, 512)
    with pytest.raises(ContextBudgetError) as exc:
        b.plan(system="system " * 5_000, turns=[current_turn("question")])
    assert "context" in str(exc.value).lower()


def test_budget_error_does_not_leak_provider_internals() -> None:
    b = ContextBudget(MIN_CONTEXT_TOKENS, 512)
    with pytest.raises(ContextBudgetError) as exc:
        b.plan(system="system " * 5_000, turns=[current_turn("question")])
    message = str(exc.value)
    assert "LLM_CONTEXT_TOKENS" in message  # actionable
    assert "Traceback" not in message


def test_invalid_budget_is_rejected_at_construction() -> None:
    with pytest.raises(ValueError):
        ContextBudget(0, 100)
    with pytest.raises(ValueError):
        ContextBudget(1_000, 1_000)  # no room for input
    with pytest.raises(ValueError):
        ContextBudget(1_000, 2_000)  # reserve larger than the window


# ---------------------------------------------------------------------------
# 6. Configuration
# ---------------------------------------------------------------------------


def test_context_tokens_is_documented_and_bounded() -> None:
    """Unreasonable values are refused rather than silently accepted."""
    assert MIN_CONTEXT_TOKENS >= 1_000
    for bad in (0, -1, 10, MAX_CONTEXT_TOKENS + 1, 128_000_000):
        with pytest.raises(ValidationError):
            Settings(_env_file=None, llm_context_tokens=bad)


def test_output_reserve_larger_than_the_window_is_refused() -> None:
    with pytest.raises(ValidationError) as exc:
        Settings(_env_file=None, llm_context_tokens=4_000, llm_max_output_tokens=4_000)
    assert "LLM_MAX_OUTPUT_TOKENS" in str(exc.value)


def test_default_context_is_a_sane_window() -> None:
    config = Settings(_env_file=None)
    assert config.llm_context_tokens == 128_000
    assert config.llm_max_output_tokens < config.llm_context_tokens


def test_budget_is_built_from_settings() -> None:
    config = Settings(_env_file=None, llm_context_tokens=8_192, llm_max_output_tokens=1_024)
    b = ContextBudget.from_settings(config)
    assert b.context_tokens == 8_192
    # The output reserve is genuinely taken out of the window.
    assert b.input_budget + b.max_output_tokens + b.margin == 8_192


# ---------------------------------------------------------------------------
# Integration: the agent must never overrun the configured window
# ---------------------------------------------------------------------------


@pytest.fixture()
def small_window(monkeypatch: pytest.MonkeyPatch):
    """Shrink the configured window so the agent must actually budget."""
    from app.config import settings as global_settings

    monkeypatch.setattr(global_settings, "llm_context_tokens", 6_000)
    monkeypatch.setattr(global_settings, "llm_max_output_tokens", 500)


class BudgetSpyProvider(ScriptedProvider):
    """Records every request so the test can assert on what was sent."""

    def __init__(self, turns: list[LLMResponse]) -> None:
        super().__init__(turns)
        self.requests: list[list[dict]] = []
        self.tools_offered: list[Any] = []

    def chat(self, messages, tools=None, temperature=None, max_output_tokens=None):
        self.requests.append([dict(m) for m in messages])
        self.tools_offered.append(tools)
        return super().chat(messages, tools=tools, temperature=temperature,
                            max_output_tokens=max_output_tokens)


def _conversation(session: Session, workspace: dict, mode: str = "explore") -> Conversation:
    conversation = Conversation(workspace_id=workspace["id"], title="t", mode=mode)
    session.add(conversation)
    session.commit()
    return conversation


def _repos(session: Session, workspace: dict) -> list[Repository]:
    return (
        session.query(Repository)
        .filter(Repository.workspace_id == workspace["id"])
        .order_by(Repository.id)
        .all()
    )


def _budget() -> ContextBudget:
    from app.config import settings as global_settings

    return ContextBudget.from_settings(global_settings)


def _assert_requests_fit(provider: BudgetSpyProvider) -> None:
    b = _budget()
    assert provider.requests
    for index, request in enumerate(provider.requests):
        tools = provider.tools_offered[index] if index < len(provider.tools_offered) else None
        assert_within(b, request, tools)


def test_agent_request_fits_the_configured_window(
    session: Session, workspace: dict, small_window
) -> None:
    provider = BudgetSpyProvider([LLMResponse(content="a short answer")])
    conversation = _conversation(session, workspace)

    Agent(provider).run(session, conversation, "What is the memory component?",
                        _repos(session, workspace))

    _assert_requests_fit(provider)


def test_agent_keeps_budget_across_tool_iterations(
    session: Session, workspace: dict, small_window
) -> None:
    """Several large tool reads in one conversation must still fit."""
    turns = [
        tool_turn("read_document", {"repository": "gaia-docs", "path": "notes.md"}, f"c{i}")
        for i in range(5)
    ] + [LLMResponse(content="final answer")]
    provider = BudgetSpyProvider(turns)
    conversation = _conversation(session, workspace)

    Agent(provider).run(session, conversation, "Analyse the notes please",
                        _repos(session, workspace))

    _assert_requests_fit(provider)


def test_agent_survives_a_very_long_conversation(
    session: Session, workspace: dict, small_window
) -> None:
    """A conversation far longer than the old 40-message cap stays in budget."""
    from app.models import Message

    conversation = _conversation(session, workspace)
    for i in range(120):
        session.add(
            Message(
                conversation_id=conversation.id,
                role="user" if i % 2 == 0 else "assistant",
                content=f"turn {i} " + "discussion " * 40,
                mode="explore",
            )
        )
    session.commit()

    provider = BudgetSpyProvider([LLMResponse(content="still answering")])
    Agent(provider).run(session, conversation, "And what about the latest change?",
                        _repos(session, workspace))

    _assert_requests_fit(provider)
    # The most recent exchange is still present.
    assert any("turn 119" in m.get("content", "") for m in provider.requests[0])


def test_agent_never_drops_the_current_user_message(
    session: Session, workspace: dict, small_window
) -> None:
    provider = BudgetSpyProvider([LLMResponse(content="answer")])
    conversation = _conversation(session, workspace)
    question = "THIS IS THE ACTUAL QUESTION"

    Agent(provider).run(session, conversation, question, _repos(session, workspace))

    assert any(question in m.get("content", "") for m in provider.requests[0])


def test_agent_returns_a_useful_error_when_the_window_is_unusable(
    session: Session, workspace: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Too small to fit the system prompt: a clear error, not a provider call."""
    from app.config import settings as global_settings

    monkeypatch.setattr(global_settings, "llm_context_tokens", 1_024)
    monkeypatch.setattr(global_settings, "llm_max_output_tokens", 1_000)

    provider = BudgetSpyProvider([LLMResponse(content="never reached")])
    conversation = _conversation(session, workspace)

    with pytest.raises(ContextBudgetError):
        Agent(provider).run(session, conversation, "hello", _repos(session, workspace))

    # Crucially, nothing was sent to the provider.
    assert provider.requests == []


def test_api_reports_context_exhaustion_without_provider_errors(
    client: TestClient, workspace: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A too-large message is an application-level 4xx, not a bad gateway."""
    from app.config import settings as global_settings

    monkeypatch.setattr(global_settings, "llm_context_tokens", 1_024)
    monkeypatch.setattr(global_settings, "llm_max_output_tokens", 1_000)
    monkeypatch.setattr(
        "app.api.routes_chat.get_provider",
        lambda: BudgetSpyProvider([LLMResponse(content="x")]),
    )

    base = f"/api/workspaces/{workspace['id']}/conversations"
    cid = client.post(base, json={}).json()["id"]
    response = client.post(f"{base}/{cid}/messages", json={"content": "hello"})

    assert response.status_code == 413
    detail = response.json()["detail"]
    assert "context window" in detail
    # The user must not be shown a raw provider failure.
    assert "Traceback" not in detail


# ---------------------------------------------------------------------------
# Inventory documents participate in the budget too
# ---------------------------------------------------------------------------


def test_inventory_batches_respect_the_budget() -> None:
    from app.services.inventory import _batches

    b = ContextBudget(6_000, 500)
    documents = [{"path": f"doc{i}.md", "content": "content line\n" * 400} for i in range(60)]

    groups = _batches(documents, b)

    assert len(groups) > 1  # a fixed batch of 12 would not fit here
    for group in groups:
        assert sum(count_tokens(d["content"]) for d in group) < b.input_budget
    # Every document still appears exactly once: nothing is silently dropped.
    assert [d["path"] for g in groups for d in g] == [d["path"] for d in documents]


def test_inventory_marks_oversized_documents() -> None:
    """An oversized document is labelled as a digest, not passed off as whole."""
    from app.services.inventory import _user_prompt

    b = ContextBudget(3_000, 500)
    documents = [
        {"path": "huge.md", "content": "line\n" * 20_000},
        {"path": "small.md", "content": "# Small\n"},
    ]

    rendered, digests = _user_prompt(documents, b)

    # The model must be able to tell which document it saw only a digest of.
    assert digests == ["huge.md"]
    assert "STRUCTURAL DIGEST" in rendered
    # The small document is still fully present.
    assert "# Small" in rendered


def test_inventory_request_fits_the_configured_window() -> None:
    """The rendered batch plus the prompt must respect the window."""
    from app.services.inventory import _batches, _user_prompt

    b = ContextBudget(6_000, 500)
    documents = [{"path": f"doc{i}.md", "content": "content line\n" * 2_000} for i in range(20)]

    for group in _batches(documents, b):
        user_content, _ = _user_prompt(group, b)
        messages = [
            {"role": "system", "content": "system prompt " * 50},
            {"role": "user", "content": user_content},
        ]
        assert_within(b, messages)


def test_inventory_prompt_alone_that_cannot_fit_raises() -> None:
    from app.services.inventory import _batches

    b = ContextBudget(1_024, 900)
    with pytest.raises(ContextBudgetError):
        _batches([{"path": "a.md", "content": "x"}], b)
