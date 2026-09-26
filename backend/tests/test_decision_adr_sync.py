"""Tests for Decision -> ADR Markdown Synchronization.

Covers:
- Approving a Decision creates a durable ADR Markdown document.
- Generated ADR preserves all Decision fields (title, status, date, context, decision,
  rationale, consequences, related questions, related documents).
- Updating an existing ADR preserves non-managed / custom sections.
- Repeated approval is idempotent and does not create duplicate files.
- Refusing to overwrite unrelated documentation (conflict safety).
- Missing/invalid Decision and workspace return 404.
- Documentation repository unavailable returns 409.
- Read-only documentation repository returns 403.
- Ambiguous ADR directory locations return 409.
- No automatic Git commit or push occurs: working tree changes remain dirty for human review.
"""
from __future__ import annotations

from pathlib import Path
import re

import pytest
from fastapi.testclient import TestClient

from app.services import git


@pytest.fixture()
def ws_with_doc_repo(client: TestClient, doc_repo: Path) -> dict:
    """Workspace with standard documentation repository containing architecture/decisions."""
    ws = client.post("/api/workspaces", json={"name": "ADR Sync Workspace"}).json()
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


def test_approve_decision_generates_new_adr(
    client: TestClient, ws_with_doc_repo: dict, doc_repo: Path
) -> None:
    ws_id = ws_with_doc_repo["id"]

    # 1. Create a Question first
    q = client.post(
        f"/api/workspaces/{ws_id}/questions",
        json={"title": "Which database for relational state?"},
    ).json()

    # 2. Create a Decision referencing the Question
    created = client.post(
        f"/api/workspaces/{ws_id}/decisions",
        json={
            "title": "Adopt PostgreSQL for Relational State",
            "context": "We need durable transactions and rich JSONB indexing.",
            "decision": "Use PostgreSQL 16 as the primary database.",
            "rationale": "Proven stability, extensive Python ecosystem support, and native JSONB.",
            "consequences": "Requires running a PostgreSQL instance in production.",
            "related_documents": ["architecture/overview.md"],
            "related_questions": [q["id"]],
        },
    ).json()
    d_id = created["id"]
    assert created["status"] == "proposed"
    assert created["markdown_path"] is None

    head_before = git.head_revision(doc_repo)

    # 2. Explicitly approve the Decision
    resp = client.post(f"/api/workspaces/{ws_id}/decisions/{d_id}/approve")
    assert resp.status_code == 200, resp.text
    data = resp.json()

    # 3. Verify response structure
    assert data["approved"] is True
    assert data["sync_status"] == "created"
    assert data["markdown_path"] is not None
    assert data["markdown_path"].startswith("architecture/decisions/adr-")
    assert "adopt-postgresql-for-relational-state" in data["markdown_path"]
    assert data["diff"] is not None
    assert data["decision"]["status"] == "approved"
    assert data["decision"]["approved_at"] is not None
    assert data["decision"]["markdown_path"] == data["markdown_path"]

    # 4. Verify file exists on disk
    adr_file = doc_repo / Path(data["markdown_path"])
    assert adr_file.exists()
    content = adr_file.read_text(encoding="utf-8")

    # 5. Verify preserved fields in Markdown content
    assert "# ADR 002: Adopt PostgreSQL for Relational State" in content
    assert "- Status: approved" in content
    assert "- Date:" in content
    assert "## Context\nWe need durable transactions and rich JSONB indexing." in content
    assert "## Decision\nUse PostgreSQL 16 as the primary database." in content
    assert "## Rationale\nProven stability, extensive Python ecosystem support, and native JSONB." in content
    assert "## Consequences\nRequires running a PostgreSQL instance in production." in content
    assert "## Related Questions" in content
    assert f"- Question reference: {q['id']}" in content
    assert "## Related Documents" in content
    assert "- [architecture/overview.md](architecture/overview.md)" in content

    # 6. Verify Git state: no commit or push occurred, working tree has uncommitted change
    assert git.head_revision(doc_repo) == head_before
    entries = git.status(doc_repo)
    assert any(data["markdown_path"] in e.path for e in entries)


def test_repeated_approval_is_idempotent(
    client: TestClient, ws_with_doc_repo: dict, doc_repo: Path
) -> None:
    ws_id = ws_with_doc_repo["id"]

    created = client.post(
        f"/api/workspaces/{ws_id}/decisions",
        json={"title": "Idempotent Decision", "decision": "First version"},
    ).json()
    d_id = created["id"]

    # First approval creates the ADR
    first_resp = client.post(f"/api/workspaces/{ws_id}/decisions/{d_id}/approve")
    assert first_resp.status_code == 200
    first_data = first_resp.json()
    assert first_data["sync_status"] == "created"
    markdown_path = first_data["markdown_path"]

    # Second approval without changes reports unchanged and does not duplicate
    second_resp = client.post(f"/api/workspaces/{ws_id}/decisions/{d_id}/approve")
    assert second_resp.status_code == 200
    second_data = second_resp.json()
    assert second_data["sync_status"] == "unchanged"
    assert second_data["markdown_path"] == markdown_path
    assert second_data["diff"] is None

    # Verify no second file was created
    decisions_dir = doc_repo / "architecture" / "decisions"
    matching = list(decisions_dir.glob("*idempotent-decision.md"))
    assert len(matching) == 1


def test_updating_existing_adr_preserves_custom_sections(
    client: TestClient, ws_with_doc_repo: dict, doc_repo: Path
) -> None:
    ws_id = ws_with_doc_repo["id"]

    # Create and approve decision
    created = client.post(
        f"/api/workspaces/{ws_id}/decisions",
        json={
            "title": "Use Redis for Session Caching",
            "decision": "Initial decision content",
            "rationale": "Initial rationale",
        },
    ).json()
    d_id = created["id"]
    approve_data = client.post(f"/api/workspaces/{ws_id}/decisions/{d_id}/approve").json()
    adr_path = doc_repo / approve_data["markdown_path"]

    # Developer manually adds a custom section to the ADR on disk
    custom_content = (
        adr_path.read_text(encoding="utf-8")
        + "\n\n## Custom Performance Benchmarks\n"
        "Latency is under 1ms at p99.\n\n"
        "## Security Notes\n"
        "Ensure TLS authentication is enabled.\n"
    )
    adr_path.write_text(custom_content, encoding="utf-8")

    # Update decision via PATCH
    client.patch(
        f"/api/workspaces/{ws_id}/decisions/{d_id}",
        json={"rationale": "Updated rationale with cluster mode details."},
    )

    # Approve again
    update_resp = client.post(f"/api/workspaces/{ws_id}/decisions/{d_id}/approve")
    assert update_resp.status_code == 200
    update_data = update_resp.json()
    assert update_data["sync_status"] == "updated"
    assert update_data["diff"] is not None

    # Check file content on disk: updated rationale + preserved custom sections
    updated_file_text = adr_path.read_text(encoding="utf-8")
    assert "Updated rationale with cluster mode details." in updated_file_text
    assert "## Custom Performance Benchmarks\nLatency is under 1ms at p99." in updated_file_text
    assert "## Security Notes\nEnsure TLS authentication is enabled." in updated_file_text


def test_refuse_overwriting_unrelated_documentation_conflict(
    client: TestClient, ws_with_doc_repo: dict, doc_repo: Path
) -> None:
    """Safety: Refuse overwriting an existing document that does not correspond to the Decision."""
    ws_id = ws_with_doc_repo["id"]

    # Point markdown_path to principles.md (an unrelated existing document)
    created = client.post(
        f"/api/workspaces/{ws_id}/decisions",
        json={
            "title": "Sneaky Decision",
            "decision": "Malicious content",
            "markdown_path": "foundation/principles.md",
        },
    ).json()
    d_id = created["id"]

    principles_before = (doc_repo / "foundation" / "principles.md").read_text(encoding="utf-8")

    resp = client.post(f"/api/workspaces/{ws_id}/decisions/{d_id}/approve")
    assert resp.status_code == 409
    assert "does not correspond to Decision" in resp.json()["detail"]

    # File on disk is intact
    assert (doc_repo / "foundation" / "principles.md").read_text(encoding="utf-8") == principles_before


def test_documentation_repository_unavailable(client: TestClient) -> None:
    """Approving when no documentation repository is registered returns 409."""
    ws = client.post("/api/workspaces", json={"name": "Empty Workspace"}).json()
    ws_id = ws["id"]

    d = client.post(
        f"/api/workspaces/{ws_id}/decisions",
        json={"title": "No Repo Decision"},
    ).json()

    resp = client.post(f"/api/workspaces/{ws_id}/decisions/{d['id']}/approve")
    assert resp.status_code == 409
    assert "no documentation repository registered" in resp.json()["detail"]


def test_documentation_repository_readonly(
    client: TestClient, tmp_path: Path
) -> None:
    """Attempting to approve into a read-only repository returns 403."""
    ws = client.post("/api/workspaces", json={"name": "Readonly Repo WS"}).json()
    ws_id = ws["id"]
    repo_dir = tmp_path / "readonly-repo"
    repo_dir.mkdir()
    (repo_dir / "architecture" / "decisions").mkdir(parents=True)

    client.post(
        f"/api/workspaces/{ws_id}/repositories",
        json={
            "name": "readonly-docs",
            "local_path": str(repo_dir),
            "kind": "documentation",
            "writable": False,
        },
    )

    d = client.post(
        f"/api/workspaces/{ws_id}/decisions",
        json={"title": "Readonly Test Decision"},
    ).json()

    resp = client.post(f"/api/workspaces/{ws_id}/decisions/{d['id']}/approve")
    assert resp.status_code == 403
    assert "read-only" in resp.json()["detail"]


def test_ambiguous_adr_location(client: TestClient, tmp_path: Path) -> None:
    """When multiple distinct candidate ADR folders exist with no clear choice, report 409."""
    ws = client.post("/api/workspaces", json={"name": "Ambiguous Repo WS"}).json()
    ws_id = ws["id"]
    repo_dir = tmp_path / "ambiguous-repo"
    repo_dir.mkdir()

    # Create two conflicting candidate decision folders
    (repo_dir / "architecture" / "decisions").mkdir(parents=True)
    (repo_dir / "docs" / "decisions").mkdir(parents=True)

    client.post(
        f"/api/workspaces/{ws_id}/repositories",
        json={
            "name": "ambiguous-docs",
            "local_path": str(repo_dir),
            "kind": "documentation",
            "writable": True,
        },
    )

    d = client.post(
        f"/api/workspaces/{ws_id}/decisions",
        json={"title": "Ambiguous Decision"},
    ).json()

    resp = client.post(f"/api/workspaces/{ws_id}/decisions/{d['id']}/approve")
    assert resp.status_code == 409
    assert "Multiple candidate ADR directories exist" in resp.json()["detail"]


def test_missing_decision_or_workspace_returns_404(client: TestClient) -> None:
    # Missing workspace
    assert client.post("/api/workspaces/999999/decisions/1/approve").status_code == 404

    # Missing decision
    ws = client.post("/api/workspaces", json={"name": "Valid WS"}).json()
    assert client.post(f"/api/workspaces/{ws['id']}/decisions/999999/approve").status_code == 404
