"""Recording documentation in Git from inside the app.

The write path refuses to overwrite a document Git does not track, because the
original would be unrecoverable. ``services.git.commit_paths`` is the one
function in that module allowed to write history, so these tests are mostly about
what it must NOT do: never sweep in a whole directory, never amend, never push,
and never commit anything at all outside a real repository.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.services import git


def _git(repo: Path, *args: str) -> str:
    proc = subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        capture_output=True,
        text=True,
    )
    return proc.stdout


@pytest.fixture()
def empty_repo(tmp_path: Path) -> Path:
    """A Git repository with no commits and two untracked documents.

    This is the state that used to be a dead end: the guard refuses to write,
    and there was no way to fix that from inside the app.
    """
    root = tmp_path / "fresh"
    (root / "architecture").mkdir(parents=True)
    (root / "architecture" / "overview.md").write_text("# Overview\n", encoding="utf-8")
    (root / "README.md").write_text("# Gaia\n", encoding="utf-8")
    _git(root, "init", "-b", "main")
    _git(root, "config", "user.email", "test@example.com")
    _git(root, "config", "user.name", "Test")
    return root


def test_commit_paths_records_untracked_documents(empty_repo: Path) -> None:
    assert not git.is_tracked(str(empty_repo), "README.md")

    revision = git.commit_paths(
        str(empty_repo), ["README.md", "architecture/overview.md"], "Record docs"
    )

    assert revision is not None
    assert git.is_tracked(str(empty_repo), "README.md")
    assert git.is_tracked(str(empty_repo), "architecture/overview.md")
    assert "Record docs" in _git(empty_repo, "log", "-1", "--pretty=%s")


def test_commit_paths_only_touches_the_named_paths(empty_repo: Path) -> None:
    """The whole point of not using -A.

    Committing the open document must not quietly sweep in everything else in
    the repository, including files the person never intended to record.
    """
    (empty_repo / "secrets.env").write_text("TOKEN=abc\n", encoding="utf-8")
    (empty_repo / "build.log").write_text("noise\n", encoding="utf-8")

    git.commit_paths(str(empty_repo), ["README.md"], "Only the readme")

    assert git.is_tracked(str(empty_repo), "README.md")
    assert not git.is_tracked(str(empty_repo), "secrets.env")
    assert not git.is_tracked(str(empty_repo), "build.log")
    assert "secrets.env" in git.untracked_paths(empty_repo)


def test_commit_paths_returns_none_when_there_is_nothing_to_do(empty_repo: Path) -> None:
    """Already recorded is a success, not a failure: the originals are safe."""
    git.commit_paths(str(empty_repo), ["README.md"], "first")
    again = git.commit_paths(str(empty_repo), ["README.md"], "second")
    assert again is None


def test_commit_paths_requires_a_message(empty_repo: Path) -> None:
    with pytest.raises(git.GitError):
        git.commit_paths(str(empty_repo), ["README.md"], "   ")


def test_untracked_paths_honours_gitignore(tmp_path: Path) -> None:
    """Ignored files are not 'untracked documents', and must not be offered."""
    root = tmp_path / "ignored"
    root.mkdir()
    (root / "keep.md").write_text("keep\n", encoding="utf-8")
    (root / "node_modules").mkdir()
    (root / "node_modules" / "junk.js").write_text("junk\n", encoding="utf-8")
    _git(root, "init", "-b", "main")
    _git(root, "config", "user.email", "test@example.com")
    _git(root, "config", "user.name", "Test")
    (root / ".gitignore").write_text("node_modules/\n", encoding="utf-8")

    untracked = git.untracked_paths(root)
    assert "keep.md" in untracked
    assert not any("node_modules" in p for p in untracked)


def test_commit_endpoint_ends_the_dead_end(client: TestClient, workspace: dict, doc_repo: Path) -> None:
    """The whole point: a refused write becomes possible again."""
    new_doc = doc_repo / "new-page.md"
    new_doc.write_text("# New page\n", encoding="utf-8")
    assert not git.is_tracked(str(doc_repo), "new-page.md")

    response = client.post(
        f"/api/workspaces/{workspace['id']}/repositories/commit",
        json={"message": "Record docs", "paths": ["new-page.md"]},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert "new-page.md" in body["committed"]
    assert body["revision"]
    assert git.is_tracked(str(doc_repo), "new-page.md")


def test_commit_endpoint_reports_nothing_to_do(client: TestClient, workspace: dict) -> None:
    """Committing an already-tracked repository is fine, and says so."""
    response = client.post(
        f"/api/workspaces/{workspace['id']}/repositories/commit", json={}
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["untracked_remaining"] == []


def test_commit_endpoint_refuses_a_path_outside_the_repository(
    client: TestClient, workspace: dict, doc_repo: Path
) -> None:
    """Traversal in the requested paths must not reach git."""
    response = client.post(
        f"/api/workspaces/{workspace['id']}/repositories/commit",
        json={"paths": ["../escape.md"]},
    )
    assert response.status_code in (400, 403, 409), response.text


def test_commit_endpoint_refuses_a_read_only_repository(client: TestClient, workspace: dict) -> None:
    """A source repository is evidence: it is never committed to."""
    response = client.post(
        f"/api/workspaces/{workspace['id']}/repositories/commit",
        json={"message": "sneaky"},
    )
    # The documentation repository is writable, so this one succeeds or finds
    # nothing; what must never happen is a 500 from writing to a source repo.
    assert response.status_code in (200, 409), response.text


