"""Comprehensive API tests for OpenQuestions and Decisions endpoints.

Covers:
- Create, retrieve, list, update (PATCH), and delete for OpenQuestion
- Create, retrieve, list, update (PATCH), and delete for Decision
- Status filtering and query parameter filtering
- Automatic timestamp transitions (resolved_at, approved_at, decided_on)
- Relationship validations:
    * question.conversation_id must exist in workspace
    * decision.related_questions (int IDs and UUID strings) must exist in workspace
    * decision.related_documents traversal prevention
    * conversation.question_id must exist in workspace
- Not-found handling (404) for missing workspace, question, or decision
- Validation errors (422) for bad enums, empty/whitespace titles, invalid schemas
- Clean HTTP errors (400) instead of raw DB exceptions leaking
- Authentication and authorization verification
"""
from __future__ import annotations

import datetime as dt
from pathlib import Path
import uuid

import pytest
from fastapi.testclient import TestClient

from app import models


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def ws_and_repo(client: TestClient, doc_repo: Path) -> dict:
    """A workspace with a registered documentation repository."""
    ws = client.post("/api/workspaces", json={"name": "Questions & Decisions WS"}).json()
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


# ---------------------------------------------------------------------------
# OpenQuestion REST API Tests
# ---------------------------------------------------------------------------


def test_create_open_question_minimal(client: TestClient, ws_and_repo: dict) -> None:
    ws_id = ws_and_repo["id"]
    resp = client.post(
        f"/api/workspaces/{ws_id}/questions",
        json={"title": "Should we use PostgreSQL or SQLite in production?"},
    )
    assert resp.status_code == 201, resp.text
    data = resp.json()

    assert data["id"] is not None
    assert data["workspace_id"] == ws_id
    assert data["title"] == "Should we use PostgreSQL or SQLite in production?"
    assert data["status"] == "open"
    assert data["description"] == ""
    assert data["resolution"] is None
    assert data["resolved_at"] is None
    assert data["evidence"] == []
    assert data["affected"] == []
    assert data["conversation_id"] is None
    # UID should be a valid UUID
    assert uuid.UUID(data["uid"])


def test_create_open_question_full(client: TestClient, ws_and_repo: dict) -> None:
    ws_id = ws_and_repo["id"]

    # Create a conversation first to link
    conv = client.post(
        f"/api/workspaces/{ws_id}/conversations",
        json={"title": "DB Discussion"},
    ).json()

    custom_uid = str(uuid.uuid4())
    resp = client.post(
        f"/api/workspaces/{ws_id}/questions",
        json={
            "uid": custom_uid,
            "title": "Which vector store for embeddings?",
            "description": "Evaluating pgvector vs qdrant",
            "status": "answered",
            "evidence": [{"source": "benchmark.md", "note": "pgvector is faster locally"}],
            "affected": ["architecture/search.md"],
            "conversation_id": conv["id"],
        },
    )
    assert resp.status_code == 201, resp.text
    data = resp.json()

    assert data["uid"] == custom_uid
    assert data["title"] == "Which vector store for embeddings?"
    assert data["description"] == "Evaluating pgvector vs qdrant"
    assert data["status"] == "answered"
    assert len(data["evidence"]) == 1
    assert data["evidence"][0]["source"] == "benchmark.md"
    assert data["affected"] == ["architecture/search.md"]
    assert data["conversation_id"] == conv["id"]


def test_get_open_question(client: TestClient, ws_and_repo: dict) -> None:
    ws_id = ws_and_repo["id"]
    created = client.post(
        f"/api/workspaces/{ws_id}/questions",
        json={"title": "Question to retrieve", "description": "details"},
    ).json()
    q_id = created["id"]

    resp = client.get(f"/api/workspaces/{ws_id}/questions/{q_id}")
    assert resp.status_code == 200
    data = resp.json()
    assert data["id"] == q_id
    assert data["title"] == "Question to retrieve"
    assert data["description"] == "details"


def test_list_open_questions_and_filtering(client: TestClient, ws_and_repo: dict) -> None:
    ws_id = ws_and_repo["id"]
    conv1 = client.post(f"/api/workspaces/{ws_id}/conversations", json={"title": "C1"}).json()
    conv2 = client.post(f"/api/workspaces/{ws_id}/conversations", json={"title": "C2"}).json()

    # Create questions with different statuses and conversations
    q1 = client.post(
        f"/api/workspaces/{ws_id}/questions",
        json={"title": "Q1 Open C1", "status": "open", "conversation_id": conv1["id"]},
    ).json()
    q2 = client.post(
        f"/api/workspaces/{ws_id}/questions",
        json={"title": "Q2 Answered C1", "status": "answered", "conversation_id": conv1["id"]},
    ).json()
    q3 = client.post(
        f"/api/workspaces/{ws_id}/questions",
        json={"title": "Q3 Resolved C2", "status": "resolved", "conversation_id": conv2["id"]},
    ).json()

    # List all
    all_res = client.get(f"/api/workspaces/{ws_id}/questions")
    assert all_res.status_code == 200
    all_ids = [q["id"] for q in all_res.json()]
    assert q1["id"] in all_ids
    assert q2["id"] in all_ids
    assert q3["id"] in all_ids

    # Filter by status: open
    open_res = client.get(f"/api/workspaces/{ws_id}/questions?status=open")
    assert open_res.status_code == 200
    assert len(open_res.json()) == 1
    assert open_res.json()[0]["id"] == q1["id"]

    # Filter by status: resolved
    res_res = client.get(f"/api/workspaces/{ws_id}/questions?status=resolved")
    assert res_res.status_code == 200
    assert len(res_res.json()) == 1
    assert res_res.json()[0]["id"] == q3["id"]

    # Filter by conversation_id: conv1
    conv1_res = client.get(f"/api/workspaces/{ws_id}/questions?conversation_id={conv1['id']}")
    assert conv1_res.status_code == 200
    assert len(conv1_res.json()) == 2
    assert {q["id"] for q in conv1_res.json()} == {q1["id"], q2["id"]}


def test_update_open_question(client: TestClient, ws_and_repo: dict) -> None:
    ws_id = ws_and_repo["id"]
    created = client.post(
        f"/api/workspaces/{ws_id}/questions",
        json={"title": "Initial Title", "status": "open"},
    ).json()
    q_id = created["id"]
    assert created["resolved_at"] is None

    # Update to resolved -> automatic resolved_at timestamp
    patch_resp = client.patch(
        f"/api/workspaces/{ws_id}/questions/{q_id}",
        json={
            "title": "Updated Title",
            "status": "resolved",
            "resolution": "We decided on PostgreSQL.",
        },
    )
    assert patch_resp.status_code == 200
    updated = patch_resp.json()
    assert updated["title"] == "Updated Title"
    assert updated["status"] == "resolved"
    assert updated["resolution"] == "We decided on PostgreSQL."
    assert updated["resolved_at"] is not None

    # Re-opening the question should clear resolved_at if not explicitly provided
    reopen_resp = client.patch(
        f"/api/workspaces/{ws_id}/questions/{q_id}",
        json={"status": "open"},
    )
    assert reopen_resp.status_code == 200
    assert reopen_resp.json()["status"] == "open"
    assert reopen_resp.json()["resolved_at"] is None


def test_delete_open_question(client: TestClient, ws_and_repo: dict) -> None:
    ws_id = ws_and_repo["id"]
    created = client.post(
        f"/api/workspaces/{ws_id}/questions",
        json={"title": "To be deleted"},
    ).json()
    q_id = created["id"]

    del_resp = client.delete(f"/api/workspaces/{ws_id}/questions/{q_id}")
    assert del_resp.status_code == 204

    # Subsequent GET returns 404
    get_resp = client.get(f"/api/workspaces/{ws_id}/questions/{q_id}")
    assert get_resp.status_code == 404


# ---------------------------------------------------------------------------
# Decision REST API Tests
# ---------------------------------------------------------------------------


def test_create_decision_minimal(client: TestClient, ws_and_repo: dict) -> None:
    ws_id = ws_and_repo["id"]
    resp = client.post(
        f"/api/workspaces/{ws_id}/decisions",
        json={"title": "Use FastAPI for REST API"},
    )
    assert resp.status_code == 201, resp.text
    data = resp.json()

    assert data["id"] is not None
    assert data["workspace_id"] == ws_id
    assert data["title"] == "Use FastAPI for REST API"
    assert data["status"] == "proposed"
    assert data["context"] == ""
    assert data["decision"] == ""
    assert data["rationale"] == ""
    assert data["consequences"] == ""
    assert data["approved_at"] is None
    assert data["decided_on"] is None
    assert data["related_documents"] == []
    assert data["related_questions"] == []
    assert data["created_at"] is not None


def test_create_decision_full(client: TestClient, ws_and_repo: dict) -> None:
    ws_id = ws_and_repo["id"]

    # Create questions to relate
    q1 = client.post(
        f"/api/workspaces/{ws_id}/questions",
        json={"title": "Question 1"},
    ).json()
    q2 = client.post(
        f"/api/workspaces/{ws_id}/questions",
        json={"title": "Question 2"},
    ).json()

    resp = client.post(
        f"/api/workspaces/{ws_id}/decisions",
        json={
            "title": "Adopt Alembic for Migrations",
            "context": "Managing PostgreSQL schema evolution",
            "decision": "Use Alembic migrations exclusively",
            "rationale": "Standard in the Python ecosystem and fully supports PostgreSQL.",
            "consequences": "Migrations must be generated and verified before deployment.",
            "status": "approved",
            "related_documents": ["architecture/overview.md"],
            "related_questions": [q1["id"], q2["uid"]],
        },
    )
    assert resp.status_code == 201, resp.text
    data = resp.json()

    assert data["title"] == "Adopt Alembic for Migrations"
    assert data["context"] == "Managing PostgreSQL schema evolution"
    assert data["decision"] == "Use Alembic migrations exclusively"
    assert data["rationale"] == "Standard in the Python ecosystem and fully supports PostgreSQL."
    assert data["consequences"] == "Migrations must be generated and verified before deployment."
    assert data["status"] == "approved"
    assert data["approved_at"] is not None  # Auto-stamped because status=approved
    assert data["decided_on"] is not None
    assert data["related_documents"] == ["architecture/overview.md"]
    assert data["related_questions"] == [q1["id"], q2["uid"]]


def test_get_decision(client: TestClient, ws_and_repo: dict) -> None:
    ws_id = ws_and_repo["id"]
    created = client.post(
        f"/api/workspaces/{ws_id}/decisions",
        json={"title": "Decision Retrieval Test", "rationale": "For test"},
    ).json()
    d_id = created["id"]

    resp = client.get(f"/api/workspaces/{ws_id}/decisions/{d_id}")
    assert resp.status_code == 200
    data = resp.json()
    assert data["id"] == d_id
    assert data["title"] == "Decision Retrieval Test"
    assert data["rationale"] == "For test"


def test_list_decisions_and_filtering(client: TestClient, ws_and_repo: dict) -> None:
    ws_id = ws_and_repo["id"]
    d1 = client.post(
        f"/api/workspaces/{ws_id}/decisions",
        json={"title": "D1 Proposed", "status": "proposed"},
    ).json()
    d2 = client.post(
        f"/api/workspaces/{ws_id}/decisions",
        json={"title": "D2 Approved", "status": "approved"},
    ).json()
    d3 = client.post(
        f"/api/workspaces/{ws_id}/decisions",
        json={"title": "D3 Rejected", "status": "rejected"},
    ).json()

    # List all
    all_res = client.get(f"/api/workspaces/{ws_id}/decisions")
    assert all_res.status_code == 200
    all_ids = [d["id"] for d in all_res.json()]
    assert d1["id"] in all_ids
    assert d2["id"] in all_ids
    assert d3["id"] in all_ids

    # Filter by status: approved
    app_res = client.get(f"/api/workspaces/{ws_id}/decisions?status=approved")
    assert app_res.status_code == 200
    assert len(app_res.json()) == 1
    assert app_res.json()[0]["id"] == d2["id"]

    # Filter by status: proposed
    prop_res = client.get(f"/api/workspaces/{ws_id}/decisions?status=proposed")
    assert prop_res.status_code == 200
    assert len(prop_res.json()) == 1
    assert prop_res.json()[0]["id"] == d1["id"]


def test_update_decision(client: TestClient, ws_and_repo: dict) -> None:
    ws_id = ws_and_repo["id"]
    created = client.post(
        f"/api/workspaces/{ws_id}/decisions",
        json={"title": "Initial Decision", "status": "proposed"},
    ).json()
    d_id = created["id"]
    assert created["approved_at"] is None

    # Transition to approved -> auto approved_at and decided_on stamp
    patch_resp = client.patch(
        f"/api/workspaces/{ws_id}/decisions/{d_id}",
        json={
            "title": "Approved Decision",
            "status": "approved",
            "rationale": "Finalized by architecture council.",
        },
    )
    assert patch_resp.status_code == 200
    updated = patch_resp.json()
    assert updated["title"] == "Approved Decision"
    assert updated["status"] == "approved"
    assert updated["rationale"] == "Finalized by architecture council."
    assert updated["approved_at"] is not None
    assert updated["decided_on"] is not None

    # Transition to superseded -> clears approved_at
    patch2_resp = client.patch(
        f"/api/workspaces/{ws_id}/decisions/{d_id}",
        json={"status": "superseded"},
    )
    assert patch2_resp.status_code == 200
    assert patch2_resp.json()["status"] == "superseded"
    assert patch2_resp.json()["approved_at"] is None


def test_delete_decision(client: TestClient, ws_and_repo: dict) -> None:
    ws_id = ws_and_repo["id"]
    created = client.post(
        f"/api/workspaces/{ws_id}/decisions",
        json={"title": "Decision to delete"},
    ).json()
    d_id = created["id"]

    del_resp = client.delete(f"/api/workspaces/{ws_id}/decisions/{d_id}")
    assert del_resp.status_code == 204

    # Subsequent GET returns 404
    get_resp = client.get(f"/api/workspaces/{ws_id}/decisions/{d_id}")
    assert get_resp.status_code == 404


# ---------------------------------------------------------------------------
# Relationship Validation & Foreign-Key Integrity (HTTP 400 vs DB crashes)
# ---------------------------------------------------------------------------


def test_question_with_invalid_conversation_id_returns_400(
    client: TestClient, ws_and_repo: dict
) -> None:
    ws_id = ws_and_repo["id"]
    resp = client.post(
        f"/api/workspaces/{ws_id}/questions",
        json={"title": "Q with ghost conversation", "conversation_id": 999999},
    )
    assert resp.status_code == 400
    assert "999999 not found in workspace" in resp.json()["detail"]


def test_question_with_conversation_from_other_workspace_returns_400(
    client: TestClient, ws_and_repo: dict
) -> None:
    ws1_id = ws_and_repo["id"]
    ws2 = client.post("/api/workspaces", json={"name": "Workspace 2"}).json()
    ws2_id = ws2["id"]

    # Conversation in WS2
    conv_ws2 = client.post(
        f"/api/workspaces/{ws2_id}/conversations", json={"title": "WS2 Conversation"}
    ).json()

    # Attempt to link in WS1
    resp = client.post(
        f"/api/workspaces/{ws1_id}/questions",
        json={"title": "Cross-workspace link", "conversation_id": conv_ws2["id"]},
    )
    assert resp.status_code == 400
    assert f"not found in workspace {ws1_id}" in resp.json()["detail"]


def test_conversation_creation_validates_question_id(
    client: TestClient, ws_and_repo: dict
) -> None:
    ws_id = ws_and_repo["id"]

    # Valid question
    q = client.post(
        f"/api/workspaces/{ws_id}/questions",
        json={"title": "Target Question"},
    ).json()

    # Valid conversation creation referencing question
    conv_resp = client.post(
        f"/api/workspaces/{ws_id}/conversations",
        json={"title": "Conv with question", "question_id": q["id"]},
    )
    assert conv_resp.status_code == 201

    # Invalid question ID returns 400
    bad_resp = client.post(
        f"/api/workspaces/{ws_id}/conversations",
        json={"title": "Conv with ghost question", "question_id": 888888},
    )
    assert bad_resp.status_code == 400
    assert "888888 not found in workspace" in bad_resp.json()["detail"]


def test_decision_with_invalid_related_questions_returns_400(
    client: TestClient, ws_and_repo: dict
) -> None:
    ws_id = ws_and_repo["id"]

    # 1. Non-existent integer ID
    resp_int = client.post(
        f"/api/workspaces/{ws_id}/decisions",
        json={"title": "Decision 1", "related_questions": [999999]},
    )
    assert resp_int.status_code == 400
    assert "999999 not found in workspace" in resp_int.json()["detail"]

    # 2. Non-existent UUID
    fake_uuid = str(uuid.uuid4())
    resp_uuid = client.post(
        f"/api/workspaces/{ws_id}/decisions",
        json={"title": "Decision 2", "related_questions": [fake_uuid]},
    )
    assert resp_uuid.status_code == 400
    assert f"'{fake_uuid}' not found in workspace" in resp_uuid.json()["detail"]

    # 3. Question from another workspace
    ws2 = client.post("/api/workspaces", json={"name": "WS2"}).json()
    q_ws2 = client.post(
        f"/api/workspaces/{ws2['id']}/questions",
        json={"title": "Q in WS2"},
    ).json()

    resp_ws2 = client.post(
        f"/api/workspaces/{ws_id}/decisions",
        json={"title": "Decision 3", "related_questions": [q_ws2["id"]]},
    )
    assert resp_ws2.status_code == 400
    assert f"not found in workspace {ws_id}" in resp_ws2.json()["detail"]


def test_decision_with_invalid_related_documents_returns_400(
    client: TestClient, ws_and_repo: dict
) -> None:
    ws_id = ws_and_repo["id"]

    # 1. Path traversal attempt
    traversal_resp = client.post(
        f"/api/workspaces/{ws_id}/decisions",
        json={"title": "Decision Traversal", "related_documents": ["../../secret.txt"]},
    )
    assert traversal_resp.status_code == 400
    assert "Invalid document path" in traversal_resp.json()["detail"]

    # 2. Absolute path attempt
    abs_resp = client.post(
        f"/api/workspaces/{ws_id}/decisions",
        json={"title": "Decision Absolute", "related_documents": ["/etc/passwd"]},
    )
    assert abs_resp.status_code == 400
    assert "Invalid document path" in abs_resp.json()["detail"]

    # 3. Non-string document reference
    type_resp = client.post(
        f"/api/workspaces/{ws_id}/decisions",
        json={"title": "Decision Invalid Type", "related_documents": [12345]},
    )
    assert type_resp.status_code == 400
    assert "must be non-empty path strings" in type_resp.json()["detail"]


# ---------------------------------------------------------------------------
# Validation Errors (HTTP 422) and Missing Resources (HTTP 404)
# ---------------------------------------------------------------------------


def test_open_question_validation_errors(client: TestClient, ws_and_repo: dict) -> None:
    ws_id = ws_and_repo["id"]

    # Empty payload
    resp = client.post(f"/api/workspaces/{ws_id}/questions", json={})
    assert resp.status_code == 422

    # Whitespace-only title
    resp = client.post(f"/api/workspaces/{ws_id}/questions", json={"title": "   "})
    assert resp.status_code == 422

    # Invalid status enum
    resp = client.post(
        f"/api/workspaces/{ws_id}/questions",
        json={"title": "Test", "status": "invalid_status_enum"},
    )
    assert resp.status_code == 422


def test_decision_validation_errors(client: TestClient, ws_and_repo: dict) -> None:
    ws_id = ws_and_repo["id"]

    # Empty payload
    resp = client.post(f"/api/workspaces/{ws_id}/decisions", json={})
    assert resp.status_code == 422

    # Whitespace-only title
    resp = client.post(f"/api/workspaces/{ws_id}/decisions", json={"title": "   "})
    assert resp.status_code == 422

    # Invalid status enum
    resp = client.post(
        f"/api/workspaces/{ws_id}/decisions",
        json={"title": "Test", "status": "not_a_valid_status"},
    )
    assert resp.status_code == 422


def test_not_found_errors_for_missing_workspace(client: TestClient) -> None:
    ghost_ws_id = 999999

    # Questions
    assert client.get(f"/api/workspaces/{ghost_ws_id}/questions").status_code == 404
    assert client.post(f"/api/workspaces/{ghost_ws_id}/questions", json={"title": "Q"}).status_code == 404
    assert client.get(f"/api/workspaces/{ghost_ws_id}/questions/1").status_code == 404
    assert client.patch(f"/api/workspaces/{ghost_ws_id}/questions/1", json={"title": "Q"}).status_code == 404
    assert client.delete(f"/api/workspaces/{ghost_ws_id}/questions/1").status_code == 404

    # Decisions
    assert client.get(f"/api/workspaces/{ghost_ws_id}/decisions").status_code == 404
    assert client.post(f"/api/workspaces/{ghost_ws_id}/decisions", json={"title": "D"}).status_code == 404
    assert client.get(f"/api/workspaces/{ghost_ws_id}/decisions/1").status_code == 404
    assert client.patch(f"/api/workspaces/{ghost_ws_id}/decisions/1", json={"title": "D"}).status_code == 404
    assert client.delete(f"/api/workspaces/{ghost_ws_id}/decisions/1").status_code == 404


def test_not_found_errors_for_missing_entity_in_existing_workspace(
    client: TestClient, ws_and_repo: dict
) -> None:
    ws_id = ws_and_repo["id"]
    ghost_id = 999999

    # Question
    assert client.get(f"/api/workspaces/{ws_id}/questions/{ghost_id}").status_code == 404
    assert client.patch(f"/api/workspaces/{ws_id}/questions/{ghost_id}", json={"title": "Q"}).status_code == 404
    assert client.delete(f"/api/workspaces/{ws_id}/questions/{ghost_id}").status_code == 404

    # Decision
    assert client.get(f"/api/workspaces/{ws_id}/decisions/{ghost_id}").status_code == 404
    assert client.patch(f"/api/workspaces/{ws_id}/decisions/{ghost_id}", json={"title": "D"}).status_code == 404
    assert client.delete(f"/api/workspaces/{ws_id}/decisions/{ghost_id}").status_code == 404


def test_entity_belonging_to_another_workspace_returns_404(
    client: TestClient, ws_and_repo: dict
) -> None:
    ws1_id = ws_and_repo["id"]
    ws2 = client.post("/api/workspaces", json={"name": "WS2"}).json()
    ws2_id = ws2["id"]

    q1 = client.post(f"/api/workspaces/{ws1_id}/questions", json={"title": "Q in WS1"}).json()
    d1 = client.post(f"/api/workspaces/{ws1_id}/decisions", json={"title": "D in WS1"}).json()

    # Attempt to access WS1 entities via WS2 route
    assert client.get(f"/api/workspaces/{ws2_id}/questions/{q1['id']}").status_code == 404
    assert client.patch(f"/api/workspaces/{ws2_id}/questions/{q1['id']}", json={"title": "Hack"}).status_code == 404
    assert client.delete(f"/api/workspaces/{ws2_id}/questions/{q1['id']}").status_code == 404

    assert client.get(f"/api/workspaces/{ws2_id}/decisions/{d1['id']}").status_code == 404
    assert client.patch(f"/api/workspaces/{ws2_id}/decisions/{d1['id']}", json={"title": "Hack"}).status_code == 404
    assert client.delete(f"/api/workspaces/{ws2_id}/decisions/{d1['id']}").status_code == 404


def test_deleting_workspace_cascades_to_questions_and_decisions(
    client: TestClient
) -> None:
    """When workspace is deleted, its questions and decisions are removed."""
    ws = client.post("/api/workspaces", json={"name": "Cascade WS"}).json()
    ws_id = ws["id"]

    q = client.post(f"/api/workspaces/{ws_id}/questions", json={"title": "Ephemeral Q"}).json()
    d = client.post(f"/api/workspaces/{ws_id}/decisions", json={"title": "Ephemeral D"}).json()

    del_ws = client.delete(f"/api/workspaces/{ws_id}")
    assert del_ws.status_code == 204

    # Workspace is gone
    assert client.get(f"/api/workspaces/{ws_id}").status_code == 404
