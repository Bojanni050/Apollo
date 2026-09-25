"""Tests for the approval-gated proposal flow (Milestone 2).

The central property under test: planning a change never touches the disk, and
only an explicit accept writes. Source repositories stay read-only.
"""
from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient


def _docs_id(workspace: dict) -> int:
    return next(r["id"] for r in workspace["repositories"] if r["kind"] == "documentation")


def _src_id(workspace: dict) -> int:
    return next(r["id"] for r in workspace["repositories"] if r["kind"] == "source")


# --------------------------------------------------------------------------
# Planning must not mutate anything
# --------------------------------------------------------------------------


def test_plan_move_does_not_touch_disk(client: TestClient, workspace: dict, doc_repo: Path) -> None:
    response = client.post(
        f"/api/workspaces/{workspace['id']}/proposals/move",
        json={
            "repository_id": _docs_id(workspace),
            "source_path": "notes.md",
            "target_dir": "architecture/components",
            "reason": "Memory notes belong with the component.",
        },
    )
    assert response.status_code == 201, response.json()
    body = response.json()
    assert body["kind"] == "move"
    assert body["status"] == "pending"
    assert body["changes"][0]["target_path"] == "architecture/components/notes.md"
    assert "notes.md" in body["diff"]

    # The file has NOT moved.
    assert (doc_repo / "notes.md").exists()
    assert not (doc_repo / "architecture" / "components" / "notes.md").exists()
    assert list(doc_repo.rglob("*.tmp-gaia")) == []


def test_plan_rename_is_detected(client: TestClient, workspace: dict, doc_repo: Path) -> None:
    body = client.post(
        f"/api/workspaces/{workspace['id']}/proposals/move",
        json={
            "repository_id": _docs_id(workspace),
            "source_path": "notes.md",
            "target_dir": ".",
            "new_name": "scratch.md",
        },
    ).json()
    assert body["kind"] == "rename"
    assert body["changes"][0]["target_path"] == "scratch.md"
    assert (doc_repo / "notes.md").exists()


def test_plan_edit_shows_unified_diff(client: TestClient, workspace: dict) -> None:
    body = client.post(
        f"/api/workspaces/{workspace['id']}/proposals/edit",
        json={
            "repository_id": _docs_id(workspace),
            "path": "notes.md",
            "new_content": "# Scratch\n\nRewritten by the architect.\n",
        },
    ).json()
    assert body["kind"] == "edit"
    assert "-Random thoughts" in body["diff"]
    assert "+Rewritten by the architect." in body["diff"]


def test_plan_create_of_existing_file_is_rejected(client: TestClient, workspace: dict) -> None:
    response = client.post(
        f"/api/workspaces/{workspace['id']}/proposals/create",
        json={
            "repository_id": _docs_id(workspace),
            "target_path": "notes.md",
            "content": "clobber",
        },
    )
    assert response.status_code == 400
    assert "already exists" in response.json()["detail"]


def test_identical_edit_is_rejected(client: TestClient, workspace: dict) -> None:
    response = client.post(
        f"/api/workspaces/{workspace['id']}/proposals/edit",
        json={
            "repository_id": _docs_id(workspace),
            "path": "notes.md",
            "new_content": "# Scratch\n\nRandom thoughts about the memory architecture, unfiled.\n",
        },
    )
    assert response.status_code == 400


# --------------------------------------------------------------------------
# Safety
# --------------------------------------------------------------------------


def test_move_traversal_is_blocked(client: TestClient, workspace: dict) -> None:
    response = client.post(
        f"/api/workspaces/{workspace['id']}/proposals/move",
        json={
            "repository_id": _docs_id(workspace),
            "source_path": "notes.md",
            "target_dir": "../../../Windows",
        },
    )
    assert response.status_code == 400
    assert "traversal" in response.json()["detail"].lower()


def test_move_with_separators_in_name_is_blocked(client: TestClient, workspace: dict) -> None:
    response = client.post(
        f"/api/workspaces/{workspace['id']}/proposals/move",
        json={
            "repository_id": _docs_id(workspace),
            "source_path": "notes.md",
            "target_dir": ".",
            "new_name": "../escaped.md",
        },
    )
    assert response.status_code == 400


def test_source_repository_is_read_only(client: TestClient, workspace: dict) -> None:
    response = client.post(
        f"/api/workspaces/{workspace['id']}/proposals/move",
        json={
            "repository_id": _src_id(workspace),
            "source_path": "README.md",
            "target_dir": "docs",
        },
    )
    assert response.status_code == 403
    assert "read-only" in response.json()["detail"]


def test_non_markdown_move_is_rejected(
    client: TestClient, workspace: dict, doc_repo: Path
) -> None:
    (doc_repo / "data.json").write_text("{}", encoding="utf-8")
    response = client.post(
        f"/api/workspaces/{workspace['id']}/proposals/move",
        json={"repository_id": _docs_id(workspace), "source_path": "data.json"},
    )
    assert response.status_code == 400


# --------------------------------------------------------------------------
# Accept / reject
# --------------------------------------------------------------------------


def test_accept_move_applies_and_leaves_git_dirty(
    client: TestClient, workspace: dict, doc_repo: Path
) -> None:
    proposal = client.post(
        f"/api/workspaces/{workspace['id']}/proposals/move",
        json={
            "repository_id": _docs_id(workspace),
            "source_path": "notes.md",
            "target_dir": "architecture/components",
        },
    ).json()

    result = client.post(
        f"/api/workspaces/{workspace['id']}/proposals/{proposal['id']}/accept"
    ).json()

    assert result["proposal"]["status"] == "accepted"
    assert result["requires_manual_commit"] is True
    assert not (doc_repo / "notes.md").exists()
    moved = doc_repo / "architecture" / "components" / "notes.md"
    assert moved.exists()
    assert "unfiled" in moved.read_text(encoding="utf-8")

    # The change is visible as an uncommitted Git change -- we never commit.
    paths = {e["path"] for e in result["git_status"]}
    assert paths == {"notes.md", "architecture/components/notes.md"}


def test_accept_edit_updates_content(client: TestClient, workspace: dict, doc_repo: Path) -> None:
    proposal = client.post(
        f"/api/workspaces/{workspace['id']}/proposals/edit",
        json={
            "repository_id": _docs_id(workspace),
            "path": "notes.md",
            "new_content": "# Scratch\n\nNow classified.\n",
        },
    ).json()
    client.post(f"/api/workspaces/{workspace['id']}/proposals/{proposal['id']}/accept")

    assert "Now classified." in (doc_repo / "notes.md").read_text(encoding="utf-8")


def test_accept_create_writes_new_file(client: TestClient, workspace: dict, doc_repo: Path) -> None:
    proposal = client.post(
        f"/api/workspaces/{workspace['id']}/proposals/create",
        json={
            "repository_id": _docs_id(workspace),
            "target_path": "architecture/decisions/adr-002.md",
            "content": "# ADR 002\n\nStatus: proposed\n",
        },
    ).json()
    client.post(f"/api/workspaces/{workspace['id']}/proposals/{proposal['id']}/accept")

    assert (doc_repo / "architecture" / "decisions" / "adr-002.md").exists()


def test_reject_leaves_filesystem_untouched(
    client: TestClient, workspace: dict, doc_repo: Path
) -> None:
    original = (doc_repo / "notes.md").read_text(encoding="utf-8")
    proposal = client.post(
        f"/api/workspaces/{workspace['id']}/proposals/move",
        json={"repository_id": _docs_id(workspace), "source_path": "notes.md",
              "target_dir": "architecture"},
    ).json()

    result = client.post(
        f"/api/workspaces/{workspace['id']}/proposals/{proposal['id']}/reject"
    ).json()
    assert result["status"] == "rejected"
    assert (doc_repo / "notes.md").read_text(encoding="utf-8") == original
    assert not (doc_repo / "architecture" / "notes.md").exists()


def test_cannot_accept_twice(client: TestClient, workspace: dict) -> None:
    proposal = client.post(
        f"/api/workspaces/{workspace['id']}/proposals/move",
        json={"repository_id": _docs_id(workspace), "source_path": "notes.md",
              "target_dir": "architecture"},
    ).json()
    url = f"/api/workspaces/{workspace['id']}/proposals/{proposal['id']}/accept"
    assert client.post(url).status_code == 200
    assert client.post(url).status_code == 409


def test_cannot_accept_rejected_proposal(client: TestClient, workspace: dict) -> None:
    proposal = client.post(
        f"/api/workspaces/{workspace['id']}/proposals/move",
        json={"repository_id": _docs_id(workspace), "source_path": "notes.md",
              "target_dir": "architecture"},
    ).json()
    base = f"/api/workspaces/{workspace['id']}/proposals/{proposal['id']}"
    client.post(f"{base}/reject")
    assert client.post(f"{base}/accept").status_code == 409


def test_accept_refuses_untracked_document(
    client: TestClient, workspace: dict, doc_repo: Path
) -> None:
    """An uncommitted file has no Git history, so overwriting it is refused."""
    (doc_repo / "untracked.md").write_text("# Draft\n", encoding="utf-8")

    move = client.post(
        f"/api/workspaces/{workspace['id']}/proposals/move",
        json={"repository_id": _docs_id(workspace), "source_path": "untracked.md",
              "target_dir": "architecture"},
    ).json()
    assert move["status"] == "pending"
    response = client.post(
        f"/api/workspaces/{workspace['id']}/proposals/{move['id']}/accept"
    )
    assert response.status_code == 409
    assert "untracked" in response.json()["detail"].lower()
    assert (doc_repo / "untracked.md").exists()


def test_proposals_are_listed_with_reason(client: TestClient, workspace: dict) -> None:
    client.post(
        f"/api/workspaces/{workspace['id']}/proposals/move",
        json={"repository_id": _docs_id(workspace), "source_path": "notes.md",
              "target_dir": "architecture", "reason": "classify it"},
    )
    pending = client.get(
        f"/api/workspaces/{workspace['id']}/proposals", params={"status_filter": "pending"}
    ).json()
    assert len(pending) == 1
    assert pending[0]["reason"] == "classify it"

    detail = client.get(
        f"/api/workspaces/{workspace['id']}/proposals/{pending[0]['id']}"
    ).json()
    assert detail["changes"][0]["source_path"] == "notes.md"


def test_identical_edit_is_rejected(client: TestClient, workspace: dict) -> None:
    response = client.post(
        f"/api/workspaces/{workspace['id']}/proposals/edit",
        json={
            "repository_id": _docs_id(workspace),
            "path": "notes.md",
            "new_content": "# Scratch\n\nRandom thoughts about the memory architecture, unfiled.\n",
        },
    )
    assert response.status_code == 400
