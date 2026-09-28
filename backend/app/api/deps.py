"""Shared helpers for resolving workspaces and repositories safely."""
from __future__ import annotations

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Decision, OpenQuestion, Repository, Workspace
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
    """The workspace's *own* documentation repository.

    Apollo's inbox storage is a documentation repository too -- the documents in
    it are Markdown and text -- but it is not this one. Scanning the intake
    instead of the documentation would report "nothing found" about an empty
    inbox while the reader's real documentation sat unexamined, which is why the
    storage repository is excluded here rather than being another candidate.
    """
    repo = db.scalar(
        select(Repository).where(
            Repository.workspace_id == workspace_id,
            Repository.kind == "documentation",
            Repository.is_storage.is_(False),
        )
    )
    if repo is None:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "This workspace has no documentation repository registered.",
        )
    return repo


def get_storage_repository(db: Session, workspace_id: int) -> Repository | None:
    """The workspace's inbox storage, or None when nothing has been dropped in."""
    return db.scalar(
        select(Repository).where(
            Repository.workspace_id == workspace_id,
            Repository.is_storage.is_(True),
        )
    )


def get_analysis_repository(db: Session, workspace_id: int) -> Repository:
    """The collection Delphi analyses: the documentation, or else the inbox.

    A pulse run asks a question about *documentation*, so it needs a documentation
    repository and reports that it has none when there is not one. The analysis
    asks a different question -- what stands out among the documents this
    workspace actually has -- so it falls back to the inbox.

    Without that fallback the most common workspace in the basis workflow (ten
    files dropped in, nothing registered) would refuse the one button that gives
    a collection any shape at all, which reads as "Analyseren does not work"
    rather than as "you have not registered a folder".
    """
    repo = get_documentation_repository_or_none(db, workspace_id)
    if repo is not None:
        return repo
    storage = get_storage_repository(db, workspace_id)
    if storage is not None:
        return storage
    raise HTTPException(
        status.HTTP_409_CONFLICT,
        "This workspace has no documents yet. Add some before asking what stands "
        "out among them.",
    )


def get_documentation_repository_or_none(
    db: Session, workspace_id: int
) -> Repository | None:
    """The workspace's own documentation repository, or None."""
    return db.scalar(
        select(Repository).where(
            Repository.workspace_id == workspace_id,
            Repository.kind == "documentation",
            Repository.is_storage.is_(False),
        )
    )


def get_or_create_storage_repo(db: Session, workspace_id: int) -> Repository:
    """The repository the workspace's dropped-in documents live in.

    Apollo keeps what the reader drops in, rather than writing it into the folder
    they registered. That folder is read from and, at most, changed through the
    approval-gated proposal flow; a drop zone is neither of those, so the intake
    gets a home of its own. The consequence worth stating: the inbox exists
    whether or not the workspace has a documentation repository at all, which is
    the normal case the first time somebody opens the app.

    Created on first use, never on a read. A workspace somebody merely looked at
    must not gain a directory on disk, so only an upload reaches this.

    The directory is created before the row, because a repository pointing at a
    path that does not exist would be refused by the very check that authorizes
    it (see ``services.paths.assert_authorized_root``).
    """
    existing = get_storage_repository(db, workspace_id)
    if existing is not None:
        return existing

    from app.services.storage import ensure_inbox, workspace_storage

    ensure_inbox(workspace_id)
    repo = Repository(
        workspace_id=workspace_id,
        name=_unique_repository_name(db, workspace_id, "Inbox"),
        local_path=str(workspace_storage(workspace_id)),
        branch="main",
        kind="documentation",
        # Writable because it is Apollo's own folder. Nothing here bypasses the
        # proposal flow: that flow guards documents the *operator* registered.
        writable=True,
        is_storage=True,
        description=(
            "Documents you added, kept by Apollo. This folder is never emptied "
            "and nothing in it is ever deleted."
        ),
    )
    db.add(repo)
    db.commit()
    db.refresh(repo)
    return repo


def _unique_repository_name(db: Session, workspace_id: int, desired: str) -> str:
    """A repository name that is free in this workspace.

    Names are unique per workspace, and a workspace that already registered a
    folder called "Inbox" must not make an upload fail over a label. The name is
    not how the storage repository is identified -- ``is_storage`` is.
    """
    taken = set(
        db.scalars(
            select(Repository.name).where(Repository.workspace_id == workspace_id)
        ).all()
    )
    if desired not in taken:
        return desired
    index = 2
    while f"{desired} {index}" in taken:
        index += 1
    return f"{desired} {index}"


def resolve_repo_root(repo: Repository) -> str:
    """Validate the repository's local path, mapping errors to HTTP 400.

    Re-checked on every use, not just at registration: configuration may have
    tightened since the repository was recorded, and a path that is no longer
    authorized must stop being served immediately.

    The check uses the *effective* roots -- the operator's list plus Apollo's own
    storage -- because the inbox repository is created by the application and
    would otherwise be refused by the rule that exists to keep unregistered
    directories out.
    """
    from app.config import settings

    try:
        return str(
            assert_authorized_root(
                repo.local_path,
                settings.effective_allowed_workspace_roots,
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


def get_question(db: Session, workspace_id: int, question_id: int) -> OpenQuestion:
    question = db.scalar(
        select(OpenQuestion).where(
            OpenQuestion.id == question_id, OpenQuestion.workspace_id == workspace_id
        )
    )
    if question is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Question not found.")
    return question


def get_decision(db: Session, workspace_id: int, decision_id: int) -> Decision:
    decision = db.scalar(
        select(Decision).where(
            Decision.id == decision_id, Decision.workspace_id == workspace_id
        )
    )
    if decision is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Decision not found.")
    return decision

