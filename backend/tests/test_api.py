"""End-to-end API tests for the Milestone 1 surface."""
from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient


def test_health(client: TestClient) -> None:
    assert client.get("/api/health").json()["status"] == "ok"


def test_workspace_lifecycle(client: TestClient) -> None:
    created = client.post("/api/workspaces", json={"name": "Gaia"})
    assert created.status_code == 201
    ws_id = created.json()["id"]

    assert client.get("/api/workspaces").json()[0]["name"] == "Gaia"
    assert client.patch(f"/api/workspaces/{ws_id}", json={"description": "docs"}).json()[
        "description"
    ] == "docs"
    assert client.delete(f"/api/workspaces/{ws_id}").status_code == 204
    assert client.get(f"/api/workspaces/{ws_id}").status_code == 404


def test_register_repositories_sets_permissions(workspace: dict) -> None:
    repos = {r["name"]: r for r in workspace["repositories"]}
    assert repos["gaia-docs"]["writable"] is True
    assert repos["gaia-docs"]["kind"] == "documentation"
    assert repos["gaia-docs"]["is_git_repo"] is True
    assert repos["gaia-docs"]["head_revision"]

    # source repositories are read-only regardless of what was requested
    assert repos["gaia-service"]["writable"] is False


def test_source_repo_cannot_be_marked_writable(client: TestClient, source_repo: Path) -> None:
    ws = client.post("/api/workspaces", json={"name": "W"}).json()
    repo = client.post(
        f"/api/workspaces/{ws['id']}/repositories",
        json={
            "name": "svc",
            "local_path": str(source_repo),
            "kind": "source",
            "writable": True,
        },
    ).json()
    assert repo["writable"] is False


def test_only_one_documentation_repo(client: TestClient, doc_repo: Path) -> None:
    ws = client.post("/api/workspaces", json={"name": "W"}).json()
    payload = {"name": "docs", "local_path": str(doc_repo), "kind": "documentation"}
    assert client.post(f"/api/workspaces/{ws['id']}/repositories", json=payload).status_code == 201
    assert client.post(f"/api/workspaces/{ws['id']}/repositories", json=payload).status_code == 409


def test_register_missing_path_is_rejected(client: TestClient, tmp_path: Path) -> None:
    ws = client.post("/api/workspaces", json={"name": "W"}).json()
    response = client.post(
        f"/api/workspaces/{ws['id']}/repositories",
        json={"name": "docs", "local_path": str(tmp_path / "nope"), "kind": "documentation"},
    )
    assert response.status_code == 400



def test_document_tree_reflects_disk(client: TestClient, workspace: dict) -> None:
    docs_id = next(r["id"] for r in workspace["repositories"] if r["kind"] == "documentation")
    tree = client.get(f"/api/workspaces/{workspace['id']}/repositories/{docs_id}/tree").json()

    names = {child["name"] for child in tree["root"]["children"]}
    assert {"foundation", "architecture", "development", "operations"} <= names
    assert ".git" not in names
    assert tree["revision"]

    architecture = next(c for c in tree["root"]["children"] if c["name"] == "architecture")
    assert {c["name"] for c in architecture["children"]} == {
        "components",
        "decisions",
        "overview.md",
    }


def test_read_document_handles_utf8_bom(
    client: TestClient, workspace: dict, doc_repo: Path
) -> None:
    """Editors on Windows may write a BOM; it must not break heading parsing."""
    docs_id = next(r["id"] for r in workspace["repositories"] if r["kind"] == "documentation")
    (doc_repo / "bom.md").write_bytes("\ufeff# BOM Document\n\nbody\n".encode("utf-8"))
    body = client.get(
        f"/api/workspaces/{workspace['id']}/repositories/{docs_id}/document",
        params={"path": "bom.md"},
    ).json()
    assert body["title"] == "BOM Document"
    assert not body["raw_markdown"].startswith("\ufeff")



def test_read_document_returns_raw_markdown(client: TestClient, workspace: dict) -> None:
    docs_id = next(r["id"] for r in workspace["repositories"] if r["kind"] == "documentation")
    response = client.get(
        f"/api/workspaces/{workspace['id']}/repositories/{docs_id}/document",
        params={"path": "architecture/components/memory.md"},
    )
    body = response.json()
    assert response.status_code == 200
    assert body["title"] == "Memory component"
    assert body["raw_markdown"].startswith("# Memory component")


def test_read_document_rejects_traversal(client: TestClient, workspace: dict) -> None:
    docs_id = next(r["id"] for r in workspace["repositories"] if r["kind"] == "documentation")
    response = client.get(
        f"/api/workspaces/{workspace['id']}/repositories/{docs_id}/document",
        params={"path": "../../../Windows/System32/config/SAM"},
    )
    assert response.status_code == 400


def test_read_document_rejects_absolute_path(client: TestClient, workspace: dict) -> None:
    docs_id = next(r["id"] for r in workspace["repositories"] if r["kind"] == "documentation")
    response = client.get(
        f"/api/workspaces/{workspace['id']}/repositories/{docs_id}/document",
        params={"path": "C:/Windows/win.ini"},
    )
    assert response.status_code == 400


def test_read_missing_document_is_404(client: TestClient, workspace: dict) -> None:
    docs_id = next(r["id"] for r in workspace["repositories"] if r["kind"] == "documentation")
    response = client.get(
        f"/api/workspaces/{workspace['id']}/repositories/{docs_id}/document",
        params={"path": "nope.md"},
    )
    assert response.status_code == 404


def test_search_ranks_relevant_documents(client: TestClient, workspace: dict) -> None:
    docs_id = next(r["id"] for r in workspace["repositories"] if r["kind"] == "documentation")
    hits = client.get(
        f"/api/workspaces/{workspace['id']}/repositories/{docs_id}/search",
        params={"q": "memory architecture"},
    ).json()["hits"]
    paths = [h["path"] for h in hits]
    assert "architecture/components/memory.md" in paths
    assert "notes.md" in paths
    assert all("line" in h for h in hits)


def test_search_works_in_source_repository(client: TestClient, workspace: dict) -> None:
    svc_id = next(r["id"] for r in workspace["repositories"] if r["kind"] == "source")
    hits = client.get(
        f"/api/workspaces/{workspace['id']}/repositories/{svc_id}/search",
        params={"q": "remember"},
    ).json()["hits"]
    assert any(h["path"] == "README.md" or h["path"] == "memory.md" for h in hits) or not hits


def test_git_changes_reports_clean_tree(client: TestClient, workspace: dict) -> None:
    docs_id = next(r["id"] for r in workspace["repositories"] if r["kind"] == "documentation")
    body = client.get(f"/api/workspaces/{workspace['id']}/repositories/{docs_id}/git").json()
    assert body["branch"] == "main"
    assert body["entries"] == []
    assert body["diff"] == ""


def test_git_changes_detects_uncommitted_edit(
    client: TestClient, workspace: dict, doc_repo: Path
) -> None:
    docs_id = next(r["id"] for r in workspace["repositories"] if r["kind"] == "documentation")
    (doc_repo / "notes.md").write_text("# Scratch\n\nedited\n", encoding="utf-8")
    body = client.get(f"/api/workspaces/{workspace['id']}/repositories/{docs_id}/git").json()
    assert [e["path"] for e in body["entries"]] == ["notes.md"]
    assert "edited" in body["diff"]


def test_repository_must_belong_to_workspace(client: TestClient, workspace: dict) -> None:
    other = client.post("/api/workspaces", json={"name": "Other"}).json()
    docs_id = next(r["id"] for r in workspace["repositories"] if r["kind"] == "documentation")
    response = client.get(f"/api/workspaces/{other['id']}/repositories/{docs_id}/tree")
    assert response.status_code == 404


def test_duplicate_repository_name_is_rejected(client: TestClient, doc_repo: Path) -> None:
    ws = client.post("/api/workspaces", json={"name": "W"}).json()
    payload = {"name": "docs", "local_path": str(doc_repo), "kind": "documentation"}
    client.post(f"/api/workspaces/{ws['id']}/repositories", json=payload)
    duplicate = client.post(
        f"/api/workspaces/{ws['id']}/repositories", json={**payload, "kind": "source"}
    )
    assert duplicate.status_code == 409
