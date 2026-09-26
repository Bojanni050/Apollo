"""Shared helpers for resolving workspaces and repositories safely."""
from __future__ import annotations

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Repository, Workspace
from app.services import git
from app.services.paths import PathSecurityError, assert_authorized_root


def get_workspace(db: Session, workspace_id: int) -> Workspace:
    workspace = db.get(Workspace, workspace_id)
    if workspace is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Workspace not found.")
    return workspace


def get_repository(db: Session, workspace_id: int, repository_id: int) -> Repository:
    repo = db.scalar(
        select(Repository).where(
            Repository.id == repository_id, Repository.workspace_id == workspace_id
        )
    )
    if repo is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Repository not found in workspace.")
    return repo


def get_documentation_repository(db: Session, workspace_id: int) -> Repository:
    repo = db.scalar(
        select(Repository).where(
            Repository.workspace_id == workspace_id,
            Repository.kind == "documentation",
        )
    )
    if repo is None:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "This workspace has no documentation repository registered.",
        )
    return repo


def resolve_repo_root(repo: Repository) -> str:
    """Validate the repository's local path, mapping errors to HTTP 400.

    Re-checked on every use, not just at registration: configuration may have
    tightened since the repository was recorded, and a path that is no longer
    authorized must stop being served immediately.
    """
    from app.config import settings

    try:
        return str(
            assert_authorized_root(
                repo.local_path,
                settings.allowed_workspace_roots,
                allow_unrestricted=settings.unrestricted_workspace_roots,
            )
        )
    except PathSecurityError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc


def repository_status(repo: Repository) -> dict:
    """Git info for a repository, tolerant of non-Git directories."""
    root = resolve_repo_root(repo)
    if not git.is_repo(root):
        return {"is_git_repo": False, "current_branch": None, "head_revision": None}
    return {
        "is_git_repo": True,
        "current_branch": git.current_branch(root),
        "head_revision": git.head_revision(root),
    }
