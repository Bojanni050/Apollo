"""Document tree/read/search/git over HTTP.

Split out of the old ``test_api.py`` monolith (stap 2 opruimplan). Format
details (.pdf/.docx/.txt) stay in ``test_document_formats_and_folders.py``;
link-graph behaviour stays in ``test_links.py``.
"""
from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient


def _docs_id(workspace: dict) -> int:
    return next(r["id"] for r in workspace["repositories"] if r["kind"] == "documentation")


def test_document_tree_reflects_disk(client: TestClient, workspace: dict) -> None:
    tree = client.get(f"/api/workspaces/{workspace['id']}/repositories/{_docs_id(workspace)}/tree").json()

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
    (doc_repo / "bom.md").write_bytes("\ufeff# BOM Document\n\nbody\n".encode("utf-8"))
    body = client.get(
        f"/api/workspaces/{workspace['id']}/repositories/{_docs_id(workspace)}/document",
        params={"path": "bom.md"},
    ).json()
    assert body["title"] == "BOM Document"
    assert not body["raw_markdown"].startswith("\ufeff")


def test_read_document_returns_raw_markdown(client: TestClient, workspace: dict) -> None:
    response = client.get(
        f"/api/workspaces/{workspace['id']}/repositories/{_docs_id(workspace)}/document",
        params={"path": "architecture/components/memory.md"},
    )
    body = response.json()
    assert response.status_code == 200
    assert body["title"] == "Memory component"
    assert body["raw_markdown"].startswith("# Memory component")


def test_read_document_rejects_traversal(client: TestClient, workspace: dict) -> None:
    response = client.get(
        f"/api/workspaces/{workspace['id']}/repositories/{_docs_id(workspace)}/document",
        params={"path": "../../../Windows/System32/config/SAM"},
    )
    assert response.status_code == 400


def test_read_document_rejects_absolute_path(client: TestClient, workspace: dict) -> None:
    response = client.get(
        f"/api/workspaces/{workspace['id']}/repositories/{_docs_id(workspace)}/document",
        params={"path": "C:/Windows/win.ini"},
    )
    assert response.status_code == 400


def test_read_missing_document_is_404(client: TestClient, workspace: dict) -> None:
    response = client.get(
        f"/api/workspaces/{workspace['id']}/repositories/{_docs_id(workspace)}/document",
        params={"path": "nope.md"},
    )
    assert response.status_code == 404


def test_search_ranks_relevant_documents(client: TestClient, workspace: dict) -> None:
    hits = client.get(
        f"/api/workspaces/{workspace['id']}/repositories/{_docs_id(workspace)}/search",
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
    body = client.get(f"/api/workspaces/{workspace['id']}/repositories/{_docs_id(workspace)}/git").json()
    assert body["branch"] == "main"
    assert body["entries"] == []
    assert body["diff"] == ""


def test_git_changes_detects_uncommitted_edit(
    client: TestClient, workspace: dict, doc_repo: Path
) -> None:
    (doc_repo / "notes.md").write_text("# Scratch\n\nedited\n", encoding="utf-8")
    body = client.get(f"/api/workspaces/{workspace['id']}/repositories/{_docs_id(workspace)}/git").json()
    assert [e["path"] for e in body["entries"]] == ["notes.md"]
    assert "edited" in body["diff"]
