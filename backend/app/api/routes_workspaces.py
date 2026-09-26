"""Workspace and repository configuration endpoints."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_repository, get_workspace, repository_status, resolve_repo_root
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
from app.services.paths import PathSecurityError, assert_authorized_root

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
