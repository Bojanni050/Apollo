"""Tests for Architectural Consistency Checking & Cross-Entity Linking.

Covers:
- Question -> Decision relationships (link, retrieve, unlink, idempotence)
- Symmetrical Question <-> Decision retrieval endpoints
- Cross-workspace tenancy / authorization boundaries
- Consistency check tool and endpoints:
  - No apparent conflict
  - Potential conflict
  - Potential overlap
  - Insufficient evidence
  - Absolute non-persistence / approval boundary
- AI conversation agent tool integration with citations
"""
from __future__ import annotations

import datetime as dt
import uuid
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.llm.base import LLMProvider, LLMResponse, ToolCall
from app.models import Conversation, Decision, OpenQuestion, Repository, Workspace
from app.services.agent import Agent
from app.services.consistency import check_consistency
from app.services.tools import ToolContext, run_tool


class ScriptedProvider(LLMProvider):
    def __init__(self, turns: list[LLMResponse]) -> None:
        self.turns = list(turns)
        self.calls: list[list[dict[str, Any]]] = []

    def chat(self, messages, tools=None, temperature=None, max_output_tokens=None):
        self.calls.append(messages)
        if not self.turns:
            return LLMResponse(content="Default response")
        return self.turns.pop(0)


@pytest.fixture()
def ws_and_repo(client: TestClient, doc_repo: Any) -> dict:
    """A workspace with a registered documentation repository."""
    ws = client.post("/api/workspaces", json={"name": "Consistency WS"}).json()
    ws_id = ws["id"]
    client.post(
        f"/api/workspaces/{ws_id}/repositories",
        json={
            "name": "gaia-docs",
            "local_path": str(doc_repo),
            "kind": "documentation",
            "writable": True,
        },
    )
    return ws



# ==============================================================================
# 1. Question <-> Decision Linking & Boundaries
# ==============================================================================


def test_link_decision_to_question_api(client: TestClient, ws_and_repo: dict) -> None:
    ws_id = ws_and_repo["id"]

    # Create question
    q_resp = client.post(
        f"/api/workspaces/{ws_id}/questions",
        json={"title": "What should own architectural memory?", "status": "open"},
    )
    assert q_resp.status_code == 201
    q_data = q_resp.json()
    q_id = q_data["id"]

    # Create decision
    d_resp = client.post(
        f"/api/workspaces/{ws_id}/decisions",
        json={
            "title": "Memory belongs to Hindsight",
            "decision": "All long-term architectural memory is owned by Hindsight.",
            "status": "approved",
        },
    )
    assert d_resp.status_code == 201
    d_data = d_resp.json()
    d_id = d_data["id"]

    # Link decision to question via POST /questions/{q_id}/decisions/{d_id}
    link_resp = client.post(f"/api/workspaces/{ws_id}/questions/{q_id}/decisions/{d_id}")
    assert link_resp.status_code == 200
    assert q_id in link_resp.json()["related_questions"]

    # Verify duplicate linking is idempotent
    link_again = client.post(f"/api/workspaces/{ws_id}/questions/{q_id}/decisions/{d_id}")
    assert link_again.status_code == 200
    assert link_again.json()["related_questions"].count(q_id) == 1

    # Inspect linked decisions from OpenQuestion endpoint
    q_decisions_resp = client.get(f"/api/workspaces/{ws_id}/questions/{q_id}/decisions")
    assert q_decisions_resp.status_code == 200
    q_decisions = q_decisions_resp.json()
    assert len(q_decisions) == 1
    assert q_decisions[0]["id"] == d_id
    assert q_decisions[0]["title"] == "Memory belongs to Hindsight"

    # Inspect addressed questions from Decision endpoint
    d_questions_resp = client.get(f"/api/workspaces/{ws_id}/decisions/{d_id}/questions")
    assert d_questions_resp.status_code == 200
    d_questions = d_questions_resp.json()
    assert len(d_questions) == 1
    assert d_questions[0]["id"] == q_id
    assert d_questions[0]["title"] == "What should own architectural memory?"

    # Verify OpenQuestion detail has addressed_by populated
    q_detail = client.get(f"/api/workspaces/{ws_id}/questions/{q_id}").json()
    assert d_id in q_detail["addressed_by"]

    # Remove relationship
    unlink_resp = client.delete(f"/api/workspaces/{ws_id}/questions/{q_id}/decisions/{d_id}")
    assert unlink_resp.status_code == 200
    assert q_id not in unlink_resp.json()["related_questions"]

    # Verify link is removed
    q_decisions_empty = client.get(f"/api/workspaces/{ws_id}/questions/{q_id}/decisions").json()
    assert len(q_decisions_empty) == 0


def test_link_question_from_decision_endpoint(client: TestClient, ws_and_repo: dict) -> None:
    ws_id = ws_and_repo["id"]

    q = client.post(
        f"/api/workspaces/{ws_id}/questions",
        json={"title": "How to handle database failover?"},
    ).json()
    d = client.post(
        f"/api/workspaces/{ws_id}/decisions",
        json={"title": "Adopt Patroni for HA"},
    ).json()

    # Link from decision endpoint
    link_resp = client.post(f"/api/workspaces/{ws_id}/decisions/{d['id']}/questions/{q['id']}")
    assert link_resp.status_code == 200
    assert q["id"] in link_resp.json()["related_questions"]

    # Unlink from decision endpoint
    unlink_resp = client.delete(f"/api/workspaces/{ws_id}/decisions/{d['id']}/questions/{q['id']}")
    assert unlink_resp.status_code == 200
    assert q["id"] not in unlink_resp.json()["related_questions"]


def test_link_invalid_entities_and_cross_workspace_isolation(client: TestClient, ws_and_repo: dict) -> None:
    ws1_id = ws_and_repo["id"]

    # Create second workspace
    ws2_resp = client.post("/api/workspaces", json={"name": "Workspace 2"})
    assert ws2_resp.status_code == 201
    ws2_id = ws2_resp.json()["id"]

    q1 = client.post(f"/api/workspaces/{ws1_id}/questions", json={"title": "WS1 Question"}).json()
    d1 = client.post(f"/api/workspaces/{ws1_id}/decisions", json={"title": "WS1 Decision"}).json()

    q2 = client.post(f"/api/workspaces/{ws2_id}/questions", json={"title": "WS2 Question"}).json()
    d2 = client.post(f"/api/workspaces/{ws2_id}/decisions", json={"title": "WS2 Decision"}).json()

    # Linking non-existent entities returns 404
    resp_bad_d = client.post(f"/api/workspaces/{ws1_id}/questions/{q1['id']}/decisions/999999")
    assert resp_bad_d.status_code == 404

    resp_bad_q = client.post(f"/api/workspaces/{ws1_id}/questions/999999/decisions/{d1['id']}")
    assert resp_bad_q.status_code == 404

    # Cross-workspace linking is blocked: WS1 cannot link WS2 decision
    resp_cross1 = client.post(f"/api/workspaces/{ws1_id}/questions/{q1['id']}/decisions/{d2['id']}")
    assert resp_cross1.status_code == 404

    # WS1 cannot link WS2 question
    resp_cross2 = client.post(f"/api/workspaces/{ws1_id}/decisions/{d1['id']}/questions/{q2['id']}")
    assert resp_cross2.status_code == 404


# ==============================================================================
# 2. Consistency Checking Core & Endpoints
# ==============================================================================


def test_consistency_check_insufficient_evidence(client: TestClient, ws_and_repo: dict) -> None:
    ws_id = ws_and_repo["id"]

    # No approved decisions exist in workspace
    resp = client.post(
        f"/api/workspaces/{ws_id}/decisions/consistency-check",
        json={
            "title": "Use Kafka for event streams",
            "decision": "Adopt Apache Kafka for high-throughput messaging.",
        },
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "Insufficient evidence"
    assert "No approved" in data["summary"] or "Insufficient evidence" in data["summary"]
    assert len(data["findings"]) == 0


def test_consistency_check_potential_conflict(client: TestClient, ws_and_repo: dict) -> None:
    ws_id = ws_and_repo["id"]

    # Pre-populate existing approved decision: Memory owned by Hindsight
    d_existing = client.post(
        f"/api/workspaces/{ws_id}/decisions",
        json={
            "title": "Long-term memory is owned by Hindsight",
            "context": "Architectural memory and reasoning storage.",
            "decision": "Long-term memory is owned by Hindsight.",
            "rationale": "Centralized agent memory repository.",
            "status": "approved",
        },
    ).json()

    # Proposal: Assigning long-term memory to Component X
    resp = client.post(
        f"/api/workspaces/{ws_id}/decisions/consistency-check",
        json={
            "title": "Component X owns long-term memory",
            "context": "Agent memory management.",
            "decision": "Component X owns long-term memory.",
            "rationale": "Local memory storage.",
        },
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "Potential conflict"
    assert any(f["type"] == "conflict" for f in data["findings"])

    conflict_finding = next(f for f in data["findings"] if f["type"] == "conflict")
    assert conflict_finding["decision_id"] == d_existing["id"]
    assert "memory" in conflict_finding["reason"].lower()


def test_consistency_check_potential_overlap(client: TestClient, ws_and_repo: dict) -> None:
    ws_id = ws_and_repo["id"]

    d_existing = client.post(
        f"/api/workspaces/{ws_id}/decisions",
        json={
            "title": "Use PostgreSQL for metadata",
            "context": "Relational data requirements.",
            "decision": "Adopt PostgreSQL database for application metadata.",
            "rationale": "ACID compliance.",
            "status": "approved",
        },
    ).json()

    # Proposal: Highly overlapping decision on PostgreSQL for metadata
    resp = client.post(
        f"/api/workspaces/{ws_id}/decisions/consistency-check",
        json={
            "title": "Use PostgreSQL for metadata storage",
            "context": "Need relational database.",
            "decision": "Adopt PostgreSQL database.",
        },
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] in ("Potential overlap", "Potential conflict")
    assert any(f["decision_id"] == d_existing["id"] for f in data["findings"])


def test_consistency_check_no_apparent_conflict(client: TestClient, ws_and_repo: dict) -> None:
    ws_id = ws_and_repo["id"]

    # Existing decision on PostgreSQL
    client.post(
        f"/api/workspaces/{ws_id}/decisions",
        json={
            "title": "Use PostgreSQL for metadata",
            "decision": "Adopt PostgreSQL database.",
            "status": "approved",
        },
    )

    # Proposal for a completely orthogonal feature that touches architecture
    resp = client.post(
        f"/api/workspaces/{ws_id}/decisions/consistency-check",
        json={
            "title": "Add Prometheus metrics endpoint",
            "context": "System observability and monitoring.",
            "decision": "Expose /metrics using prometheus_client library.",
            "rationale": "Standard Kubernetes scrape target.",
        },
    )
    assert resp.status_code == 200
    data = resp.json()
    # No conflict with database decisions
    assert data["status"] in ("No apparent conflict", "Insufficient evidence")


def test_consistency_check_approval_boundary_guarantee(client: TestClient, ws_and_repo: dict, session: Session) -> None:
    ws_id = ws_and_repo["id"]

    # Create a proposed decision in DB
    created = client.post(
        f"/api/workspaces/{ws_id}/decisions",
        json={
            "title": "Adopt WebSockets for chat streaming",
            "status": "proposed",
        },
    ).json()
    d_id = created["id"]

    # Run consistency check on existing decision
    check_resp = client.post(f"/api/workspaces/{ws_id}/decisions/{d_id}/consistency-check")
    assert check_resp.status_code == 200

    # CRITICAL: Verify the decision remains strictly in 'proposed' status, with approved_at=None, markdown_path=None
    reloaded = client.get(f"/api/workspaces/{ws_id}/decisions/{d_id}").json()
    assert reloaded["status"] == "proposed"
    assert reloaded["approved_at"] is None
    assert reloaded["markdown_path"] is None


# ==============================================================================
# 3. AI Agent Tool Execution & Citing
# ==============================================================================


def test_agent_tool_check_architectural_consistency(session: Session, workspace: dict) -> None:
    ws = session.get(Workspace, workspace["id"])
    repos = session.query(Repository).filter(Repository.workspace_id == ws.id).all()

    # Pre-populate approved decision
    decision = Decision(
        workspace_id=ws.id,
        title="Long-term memory is owned by Hindsight",
        context="Memory ownership.",
        decision="Hindsight owns all architectural memory.",
        rationale="Agentic memory separation.",
        status="approved",
        markdown_path="architecture/decisions/adr-014-memory.md",
    )
    session.add(decision)
    session.commit()

    ctx = ToolContext(workspace=ws, repositories=repos, citations=[], db=session)

    # Run tool directly
    out = run_tool(
        "check_architectural_consistency",
        ctx,
        {
            "title": "Component X owns long-term memory",
            "decision": "Component X is responsible for architectural memory.",
            "context": "Agent memory discussion.",
        },
    )

    assert "Architectural Consistency Check Result:" in out
    assert "Decision #14" in out or f"Decision #{decision.id}" in out
    assert "adr-014-memory.md" in out
    assert len(ctx.citations) >= 1
    assert any(c.decision_id == decision.id for c in ctx.citations)
    assert "does NOT automatically approve, reject, or modify" in out
