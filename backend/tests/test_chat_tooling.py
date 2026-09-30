"""Tests for Decision & Question Chat Tooling.

Covers:
- Tool execution: search_questions, search_decisions, get_question, get_decision
- Authorization boundaries across workspaces
- Drafting tools (draft_question, draft_decision) and proof of non-persistence
- Citation capture for questions and decisions
- End-to-end conversation flow and operator saving boundary
"""
from __future__ import annotations

import datetime as dt
import uuid
from typing import Any

import pytest
from sqlalchemy.orm import Session

from app.llm.base import LLMResponse, ToolCall
from app.models import Conversation, Decision, Message, OpenQuestion, Repository, Workspace
from app.services.agent import Agent
from app.services.tools import ToolContext, run_tool
from tests.conftest import ScriptedProvider


def _tool_context(session: Session, workspace: dict) -> ToolContext:
    ws = session.get(Workspace, workspace["id"])
    repos = session.query(Repository).filter(Repository.workspace_id == ws.id).all()
    return ToolContext(
        workspace=ws,
        repositories=repos,
        citations=[],
        db=session,
    )


# --------------------------------------------------------------------------
# 1. Retrieval & Search Tools
# --------------------------------------------------------------------------


def test_search_questions_tool(session: Session, workspace: dict) -> None:
    ws_id = workspace["id"]

    # Questions in workspace 1
    q1 = OpenQuestion(
        workspace_id=ws_id,
        uid=uuid.uuid4(),
        title="Cache layer selection",
        description="Should we adopt Redis or Memcached?",
        status="open",
        affected=["cache", "backend"],
    )
    q2 = OpenQuestion(
        workspace_id=ws_id,
        uid=uuid.uuid4(),
        title="Database engine",
        description="PostgreSQL was chosen for metadata.",
        status="answered",
        affected=["db"],
    )
    # Question in another workspace (must not be visible)
    other_ws = Workspace(name="Other Workspace")
    session.add(other_ws)
    session.flush()
    q_other = OpenQuestion(
        workspace_id=other_ws.id,
        uid=uuid.uuid4(),
        title="Cache layer secret",
        description="Hidden cache question in other workspace",
        status="open",
    )
    session.add_all([q1, q2, q_other])
    session.commit()

    ctx = _tool_context(session, workspace)

    # Search by query
    res = run_tool("search_questions", ctx, {"query": "Cache"})
    assert f"#{q1.id} [open] Cache layer selection" in res
    assert "Redis or Memcached" in res
    # Must NOT include question from another workspace
    assert "Hidden cache question" not in res
    assert len(ctx.citations) == 1
    assert ctx.citations[0].question_id == q1.id
    assert ctx.citations[0].evidence_type == "uncertainty"

    # Search by status
    ctx_status = _tool_context(session, workspace)
    res_status = run_tool("search_questions", ctx_status, {"status": "answered"})
    assert f"#{q2.id} [answered] Database engine" in res_status
    assert f"#{q1.id}" not in res_status


def test_search_decisions_tool(session: Session, workspace: dict) -> None:
    ws_id = workspace["id"]

    d1 = Decision(
        workspace_id=ws_id,
        title="Use PostgreSQL for metadata",
        context="Need robust relational storage.",
        decision="Adopt PostgreSQL with Alembic.",
        rationale="ACID compliance and relational queries.",
        consequences="Requires PG instance.",
        status="approved",
        markdown_path="architecture/decisions/adr-001-use-postgresql.md",
    )
    d2 = Decision(
        workspace_id=ws_id,
        title="Adopt gRPC for internal RPC",
        context="High-performance inter-service communication.",
        decision="Use gRPC.",
        rationale="Protobuf typing and performance.",
        consequences="Proto schema maintenance.",
        status="proposed",
    )
    other_ws = Workspace(name="Other WS")
    session.add(other_ws)
    session.flush()
    d_other = Decision(
        workspace_id=other_ws.id,
        title="Secret internal decision",
        decision="Do not reveal to other workspace",
        status="approved",
    )
    session.add_all([d1, d2, d_other])
    session.commit()

    ctx = _tool_context(session, workspace)

    # Search query
    res = run_tool("search_decisions", ctx, {"query": "PostgreSQL"})
    assert f"#{d1.id} [approved] Use PostgreSQL for metadata" in res
    assert "adr-001-use-postgresql.md" in res
    assert "Secret internal decision" not in res
    assert len(ctx.citations) == 1
    assert ctx.citations[0].decision_id == d1.id
    assert ctx.citations[0].evidence_type == "explicit_decision"

    # Search status
    ctx_status = _tool_context(session, workspace)
    res_status = run_tool("search_decisions", ctx_status, {"status": "proposed"})
    assert f"#{d2.id} [proposed] Adopt gRPC" in res_status
    assert f"#{d1.id}" not in res_status


def test_get_question_tool(session: Session, workspace: dict) -> None:
    ws_id = workspace["id"]
    q = OpenQuestion(
        workspace_id=ws_id,
        uid=uuid.uuid4(),
        title="Message broker choice",
        description="Evaluate RabbitMQ vs Kafka.",
        status="open",
        affected=["events", "streaming"],
    )
    session.add(q)
    session.commit()

    ctx = _tool_context(session, workspace)
    out = run_tool("get_question", ctx, {"question_id": q.id})
    assert f"Question #{q.id}" in out
    assert "Message broker choice" in out
    assert "RabbitMQ vs Kafka" in out
    assert "events, streaming" in out
    assert len(ctx.citations) == 1
    assert ctx.citations[0].question_id == q.id
    assert ctx.citations[0].evidence_type == "uncertainty"

    # Non-existent ID returns error message without crashing
    out_missing = run_tool("get_question", ctx, {"question_id": 99999})
    assert "Error:" in out_missing
    assert "not found in workspace" in out_missing


def test_get_decision_tool(session: Session, workspace: dict) -> None:
    ws_id = workspace["id"]
    d = Decision(
        workspace_id=ws_id,
        title="Use Redis for caching",
        context="Session state needs fast storage.",
        decision="Adopt Redis 7.",
        rationale="Sub-millisecond latency and TTL support.",
        consequences="Need Redis cluster.",
        status="approved",
        markdown_path="architecture/decisions/adr-002-redis.md",
    )
    session.add(d)
    session.commit()

    ctx = _tool_context(session, workspace)
    out = run_tool("get_decision", ctx, {"decision_id": d.id})
    assert f"Decision #{d.id}" in out
    assert "Use Redis for caching" in out
    assert "Adopt Redis 7." in out
    assert "adr-002-redis.md" in out
    assert len(ctx.citations) == 1
    assert ctx.citations[0].decision_id == d.id
    assert ctx.citations[0].evidence_type == "explicit_decision"

    # Non-existent ID returns error message without crashing
    out_missing = run_tool("get_decision", ctx, {"decision_id": 99999})
    assert "Error:" in out_missing
    assert "not found in workspace" in out_missing


def test_empty_search_results(session: Session, workspace: dict) -> None:
    ctx = _tool_context(session, workspace)
    out_q = run_tool("search_questions", ctx, {"query": "nonexistent_term_xyz"})
    assert "No open questions found" in out_q

    out_d = run_tool("search_decisions", ctx, {"query": "nonexistent_term_xyz"})
    assert "No decisions found" in out_d


def test_cross_workspace_isolation_for_get_tools(session: Session, workspace: dict) -> None:
    # Create another workspace with private records
    other_ws = Workspace(name="Foreign Workspace")
    session.add(other_ws)
    session.flush()

    foreign_q = OpenQuestion(
        workspace_id=other_ws.id,
        uid=uuid.uuid4(),
        title="Foreign secret question",
        description="Private information",
        status="open",
    )
    foreign_d = Decision(
        workspace_id=other_ws.id,
        title="Foreign secret decision",
        decision="Top secret decision",
        status="approved",
    )
    session.add_all([foreign_q, foreign_d])
    session.commit()

    ctx = _tool_context(session, workspace)

    # Attempting to fetch foreign question by ID using current workspace context
    res_q = run_tool("get_question", ctx, {"question_id": foreign_q.id})
    assert "Error:" in res_q
    assert f"Question #{foreign_q.id} not found in workspace" in res_q
    assert len(ctx.citations) == 0

    # Attempting to fetch foreign decision by ID using current workspace context
    res_d = run_tool("get_decision", ctx, {"decision_id": foreign_d.id})
    assert "Error:" in res_d
    assert f"Decision #{foreign_d.id} not found in workspace" in res_d
    assert len(ctx.citations) == 0



# --------------------------------------------------------------------------
# 2. Drafting & Non-Persistence Proof
# --------------------------------------------------------------------------


def test_draft_question_does_not_persist(session: Session, workspace: dict) -> None:
    ctx = _tool_context(session, workspace)
    count_before = session.query(OpenQuestion).count()

    out = run_tool(
        "draft_question",
        ctx,
        {
            "title": "Should we use pgvector?",
            "description": "Evaluate pgvector for vector search.",
            "affected": ["db", "search"],
        },
    )

    assert "Drafted OpenQuestion: 'Should we use pgvector?'" in out
    assert "NOT yet saved in the database" in out
    # Verify no row was inserted into OpenQuestion table
    count_after = session.query(OpenQuestion).count()
    assert count_after == count_before


def test_draft_decision_does_not_persist_or_approve(session: Session, workspace: dict) -> None:
    ctx = _tool_context(session, workspace)
    count_before = session.query(Decision).count()

    out = run_tool(
        "draft_decision",
        ctx,
        {
            "title": "Adopt WebSockets for live status updates",
            "context": "Clients need real-time notifications.",
            "decision": "Use WebSockets endpoint.",
            "rationale": "Lower overhead than HTTP polling.",
            "consequences": "Requires connection management.",
        },
    )

    assert "Drafted Decision: 'Adopt WebSockets for live status updates'" in out
    assert "NOT yet saved in the database, and is NOT approved" in out
    # Verify no row was inserted into Decision table
    count_after = session.query(Decision).count()
    assert count_after == count_before


# --------------------------------------------------------------------------
# 3. Conversational Agent Flow & Approval Boundary
# --------------------------------------------------------------------------


def test_agent_flow_cites_decision_and_drafts_question(session: Session, workspace: dict) -> None:
    ws = session.get(Workspace, workspace["id"])
    repos = session.query(Repository).filter(Repository.workspace_id == ws.id).all()

    # Pre-populate an existing approved decision
    decision = Decision(
        workspace_id=ws.id,
        title="Adopt Single-Sign-On with OIDC",
        context="Need centralized identity.",
        decision="Use OIDC auth server.",
        rationale="Standard protocol supported by enterprise providers.",
        consequences="All internal services must validate JWTs.",
        status="approved",
        markdown_path="architecture/decisions/adr-005-oidc.md",
    )
    session.add(decision)
    session.commit()

    conversation = Conversation(workspace_id=ws.id, title="Auth architecture", mode="investigate")
    session.add(conversation)
    session.commit()

    # Scripted agent responses:
    # 1. Calls search_decisions
    # 2. Calls draft_question
    # 3. Final answer
    turn1 = LLMResponse(
        content="",
        tool_calls=[
            ToolCall(id="tc1", name="search_decisions", arguments={"query": "OIDC"})
        ],
    )
    turn2 = LLMResponse(
        content="",
        tool_calls=[
            ToolCall(
                id="tc2",
                name="draft_question",
                arguments={
                    "title": "How should token revocation be propagated?",
                    "description": "OIDC tokens are stateless JWTs, need revocation strategy.",
                    "affected": ["auth", "gateway"],
                },
            )
        ],
    )
    turn3 = LLMResponse(
        content=(
            "We previously decided to adopt OIDC (ADR-005). "
            "However, token revocation remains an open issue, so I have drafted an OpenQuestion for your review."
        )
    )

    provider = ScriptedProvider([turn1, turn2, turn3])
    agent = Agent(provider)

    result = agent.run(session, conversation, "How do we handle token invalidation with our auth system?", repos)

    # Verify final content
    assert "ADR-005" in result.content
    assert "drafted an OpenQuestion" in result.content

    # Verify decision citation was captured
    citations = result.citations
    assert any(c.decision_id == decision.id for c in citations)

    # Verify tool calls were recorded
    tool_names = [call["tool"] for call in result.tool_calls]
    assert "search_decisions" in tool_names
    assert "draft_question" in tool_names

    # CRITICAL: Verify the drafted question was NOT automatically persisted
    persisted_questions = (
        session.query(OpenQuestion)
        .filter(OpenQuestion.workspace_id == ws.id)
        .all()
    )
    assert len(persisted_questions) == 0

    # Simulate operator explicit review and save through domain/REST action:
    draft_call = next(c for c in result.tool_calls if c["tool"] == "draft_question")
    draft_args = draft_call["arguments"]

    saved_question = OpenQuestion(
        workspace_id=ws.id,
        uid=uuid.uuid4(),
        title=draft_args["title"],
        description=draft_args["description"],
        affected=draft_args["affected"],
        status="open",
        source="conversation",
        conversation_id=conversation.id,
    )
    session.add(saved_question)
    session.commit()

    # Now and only now does the question exist
    reloaded = session.get(OpenQuestion, saved_question.id)
    assert reloaded is not None
    assert reloaded.title == "How should token revocation be propagated?"
