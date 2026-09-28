"""Workspace and repository configuration endpoints."""
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Response, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import (
    get_documentation_repository,
    get_repository,
    get_workspace,
    repository_status,
    resolve_repo_root,
)
from app.config import settings
from app.db import get_db
from app.models import Repository, Workspace
from app.schemas import (
    RepositoryCreate,
    RepositoryOut,
    RepositoryUpdate,
    WorkspaceCreate,
    WorkspaceOut,
    WorkspaceUpdate,
)
from app.services import git
from app.services.paths import PathSecurityError, assert_authorized_root, safe_path

router = APIRouter(tags=["workspaces"])


def _repo_out(repo: Repository) -> RepositoryOut:
    out = RepositoryOut.model_validate(repo)
    out.is_git_repo, out.current_branch, out.head_revision = repository_status(repo).values()
    return out


def _workspace_out(db: Session, workspace: Workspace) -> WorkspaceOut:
    out = WorkspaceOut.model_validate(workspace)
    out.repositories = [_repo_out(r) for r in workspace.repositories]
    return out


@router.get("/workspaces", response_model=list[WorkspaceOut])
def list_workspaces(db: Session = Depends(get_db)) -> list[WorkspaceOut]:
    workspaces = db.scalars(select(Workspace).order_by(Workspace.name)).all()
    return [_workspace_out(db, w) for w in workspaces]


@router.post("/workspaces", response_model=WorkspaceOut, status_code=status.HTTP_201_CREATED)
def create_workspace(payload: WorkspaceCreate, db: Session = Depends(get_db)) -> WorkspaceOut:
    workspace = Workspace(name=payload.name.strip(), description=payload.description)
    db.add(workspace)
    db.commit()
    db.refresh(workspace)
    return _workspace_out(db, workspace)


@router.get("/workspaces/{workspace_id}", response_model=WorkspaceOut)
def read_workspace(workspace_id: int, db: Session = Depends(get_db)) -> WorkspaceOut:
    return _workspace_out(db, get_workspace(db, workspace_id))


@router.patch("/workspaces/{workspace_id}", response_model=WorkspaceOut)
def update_workspace(
    workspace_id: int, payload: WorkspaceUpdate, db: Session = Depends(get_db)
) -> WorkspaceOut:
    workspace = get_workspace(db, workspace_id)
    if payload.name is not None:
        workspace.name = payload.name.strip()
    if payload.description is not None:
        workspace.description = payload.description
    db.commit()
    db.refresh(workspace)
    return _workspace_out(db, workspace)


@router.delete("/workspaces/{workspace_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_workspace(workspace_id: int, db: Session = Depends(get_db)) -> Response:
    db.delete(get_workspace(db, workspace_id))
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/workspaces/{workspace_id}/repositories",
    response_model=RepositoryOut,
    status_code=status.HTTP_201_CREATED,
)
def add_repository(
    workspace_id: int, payload: RepositoryCreate, db: Session = Depends(get_db)
) -> RepositoryOut:
    """Register an existing local repository. Nothing is cloned or copied."""
    workspace = get_workspace(db, workspace_id)

    writable = payload.writable and payload.kind == "documentation"
    if payload.kind == "documentation":
        existing = db.scalar(
            select(Repository).where(
                Repository.workspace_id == workspace_id,
                Repository.kind == "documentation",
            )
        )
        if existing is not None:
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                "This workspace already has a documentation repository.",
            )
    else:
        writable = False  # source repositories are never writable

    try:
        root = assert_authorized_root(
            payload.local_path,
            settings.allowed_workspace_roots,
            allow_unrestricted=settings.unrestricted_workspace_roots,
        )
    except PathSecurityError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc

    name = payload.name.strip()
    duplicate = db.scalar(
        select(Repository).where(
            Repository.workspace_id == workspace_id, Repository.name == name
        )
    )
    if duplicate is not None:
        raise HTTPException(
            status.HTTP_409_CONFLICT, f"A repository named {name!r} already exists."
        )

    branch = payload.branch
    if git.is_repo(str(root)) and not branch:
        branch = git.current_branch(str(root)) or "main"

    repo = Repository(
        workspace_id=workspace.id,
        name=name,
        local_path=str(root),
        branch=branch or "main",
        kind=payload.kind,
        writable=writable,
        description=payload.description,
    )
    db.add(repo)
    db.commit()
    db.refresh(repo)
    return _repo_out(repo)


@router.patch(
    "/workspaces/{workspace_id}/repositories/{repository_id}", response_model=RepositoryOut
)
def update_repository(
    workspace_id: int,
    repository_id: int,
    payload: RepositoryUpdate,
    db: Session = Depends(get_db),
) -> RepositoryOut:
    repo = get_repository(db, workspace_id, repository_id)
    if payload.name is not None:
        repo.name = payload.name.strip()
    if payload.branch is not None:
        repo.branch = payload.branch
    if payload.description is not None:
        repo.description = payload.description
    db.commit()
    db.refresh(repo)
    return _repo_out(repo)


@router.delete(
    "/workspaces/{workspace_id}/repositories/{repository_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
def remove_repository(
    workspace_id: int, repository_id: int, db: Session = Depends(get_db)
) -> Response:
    db.delete(get_repository(db, workspace_id, repository_id))
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


class RepoCommitIn(BaseModel):
    """Ask the backend to record documents in Git.

    The write path refuses to overwrite a document Git does not track, because
    the original would be unrecoverable. On a repository with no commits yet that
    refusal had no way out from inside the app, so this offers the missing step
    -- explicitly, never as a side effect of a suggestion being accepted.
    """

    message: str = "Record current documentation state"
    #: Empty means "every untracked file that Git would not ignore". It is
    #: expanded to an explicit list server-side rather than passed to git as -A,
    #: so the set that gets committed is always visible before it happens.
    paths: list[str] = Field(default_factory=list)


class RepoCommitOut(BaseModel):
    committed: list[str]
    #: None when everything was already recorded, which is a success, not a fault.
    revision: str | None
    untracked_remaining: list[str] = Field(default_factory=list)


@router.post(
    "/workspaces/{workspace_id}/repositories/commit",
    response_model=RepoCommitOut,
)
def commit_repository_documents(
    workspace_id: int,
    payload: RepoCommitIn,
    db: Session = Depends(get_db),
) -> RepoCommitOut:
    """Record untracked documentation in Git, so writes stop being refused.

    Every write Apollo makes goes through a guard that refuses to overwrite a
    document Git does not track, because the original would be unrecoverable.
    That guard is right, and it is also a dead end on a repository whose first
    commit has not been made yet -- the app could show the refusal but not offer
    the missing step. This is that step, and deliberately:

    * only the *documentation* repository is affected; a source repository is
      evidence and stays read-only;
    * the root is re-validated through ``assert_authorized_root`` here rather
      than trusting the stored path, because this call runs git on it;
    * the set of paths goes through ``safe_path``, so a caller cannot name
      something outside the repository and have git stage it;
    * nothing is pushed and no commit is rewritten.
    """
    repo = get_documentation_repository(db, workspace_id)
    if not repo.writable:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "This documentation repository is not writable, so it cannot be committed to.",
        )

    root = resolve_repo_root(repo)
    try:
        # Not a mere existence check: a path outside the authorized roots must be
        # refused even if it happens to resolve to a real directory. Re-validated
        # with the same arguments as registration, since this call runs git.
        assert_authorized_root(
            root,
            settings.allowed_workspace_roots,
            allow_unrestricted=settings.unrestricted_workspace_roots,
        )
    except PathSecurityError as exc:
        raise HTTPException(status.HTTP_403_FORBIDDEN, str(exc)) from exc

    if not git.is_repo(root):
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "This documentation folder is not a Git repository, so originals cannot be preserved.",
        )

    def _rel(p: Path) -> str:
        return str(p.relative_to(root)).replace("\\", "/")

    try:
        if payload.paths:
            candidates = [safe_path(root, p) for p in payload.paths]
        else:
            candidates = [safe_path(root, p) for p in git.untracked_paths(root)]

        if not candidates:
            # Nothing untracked: the originals are already recoverable, which is
            # the state the caller wanted. Not an error.
            return RepoCommitOut(
                committed=[], revision=git.head_revision(root), untracked_remaining=[]
            )

        revision = git.commit_paths(root, [_rel(c) for c in candidates], payload.message)
    except (PathSecurityError, git.GitError) as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc

    return RepoCommitOut(
        committed=[_rel(c) for c in candidates],
        revision=revision,
        untracked_remaining=git.untracked_paths(root),
    )
