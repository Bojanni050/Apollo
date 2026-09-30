"""Workspace and repository management over HTTP.

Split out of the old ``test_api.py`` monolith (stap 2 opruimplan): health,
workspace lifecycle, database reset and repository registration live here.
Document tree/read/search/git moved to ``test_documents.py``; LLM model
metadata moved to ``test_model_manager.py``.
"""
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


def test_database_reset_requires_confirmation(client: TestClient) -> None:
    assert (
        client.post("/api/system/database/reset", json={"confirm": "yes"}).status_code
        == 400
    )


def test_database_reset_wipes_all_workspaces(client: TestClient, workspace: dict) -> None:
    client.post("/api/workspaces", json={"name": "Second"})
    response = client.post("/api/system/database/reset", json={"confirm": "RESET"})
    assert response.status_code == 200
    assert response.json()["deleted_workspaces"] == 2
    assert client.get("/api/workspaces").json() == []
    assert client.get(f"/api/workspaces/{workspace['id']}").status_code == 404


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


def test_a_workspace_survives_a_repository_whose_folder_is_gone(
    client: TestClient, doc_repo: Path
) -> None:
    """A deleted folder must not take the workspace listing down with it.

    Folders go missing for ordinary reasons: an external drive that is not
    plugged in, a rename, a sync client that has not finished. The listing is
    read on every load to decide whether the reader has set up a workspace yet,
    so one repository pointing at nothing used to raise a 400 out of
    GET /workspaces, the application showed the first-run wizard again, and the
    wizard could not register a repository because the very listing it depends
    on was still failing. The way out was the database, by hand.
    """
    ws = client.post("/api/workspaces", json={"name": "W"}).json()
    doomed = doc_repo / "will-vanish"
    doomed.mkdir()
    client.post(
        f"/api/workspaces/{ws['id']}/repositories",
        json={"name": "gone", "local_path": str(doomed), "kind": "source"},
    )
    # A second repository that is perfectly fine, to show the failure was never
    # about the workspace as a whole.
    client.post(
        f"/api/workspaces/{ws['id']}/repositories",
        json={"name": "docs", "local_path": str(doc_repo), "kind": "documentation"},
    )

    import shutil

    shutil.rmtree(doomed)

    listed = client.get("/api/workspaces")
    assert listed.status_code == 200, listed.text
    mine = next(w for w in listed.json() if w["id"] == ws["id"])
    # As a set, not a list: the order is an implementation detail of the
    # relation, and this test is about which repositories survive, not how they
    # are sorted. Pinning the order would make it fail for the wrong reason.
    assert {r["name"] for r in mine["repositories"]} == {"docs", "gone"}
    # Reported as not a repository rather than as an error, so the interface has
    # something to show. It is not a Git question anybody asked.
    gone = next(r for r in mine["repositories"] if r["name"] == "gone")
    assert gone["is_git_repo"] is False
    assert gone["current_branch"] is None
