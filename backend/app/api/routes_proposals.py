"""Change proposal endpoints: plan, inspect, accept, reject.

There is intentionally no endpoint that mutates documentation directly. Every
modification is a proposal that a human must accept, and acceptance never
commits to Git.
"""
from __future__ import annotations

import datetime as dt

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_repository, get_workspace, resolve_repo_root
from app.db import get_db
from app.models import ChangeProposal, Repository
from app.schemas import (
    AcceptProposalOut,
    CreateProposalCreate,
    EditProposalCreate,
    GitStatusEntryOut,
    MoveProposalCreate,
    ProposalOut,
)
from app.services import git
from app.services.filing import repoint_after_move
from app.services.proposals import (
    PlannedChange,
    ProposalError,
    apply_change,
    plan_create,
    plan_edit,
    plan_move,
)

router = APIRouter(prefix="/workspaces/{workspace_id}", tags=["proposals"])


def _writable_repository(
    db: Session, workspace_id: int, repository_id: int
) -> tuple[Repository, str]:
    get_workspace(db, workspace_id)
    repo = get_repository(db, workspace_id, repository_id)
    if not repo.writable:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            f"Repository {repo.name!r} is read-only. Only the documentation "
            "repository can be modified.",
        )
    return repo, resolve_repo_root(repo)


def _proposal_out(proposal: ChangeProposal, repository_id: int) -> ProposalOut:
    out = ProposalOut.model_validate(proposal)
    out.repository_id = repository_id
    return out


def _store(
    db: Session,
    workspace_id: int,
    repository_id: int,
    kind: str,
    title: str,
    reason: str,
    change: PlannedChange,
    consequences: str,
) -> ChangeProposal:
    proposal = ChangeProposal(
        workspace_id=workspace_id,
        kind=kind,
        title=title or f"{kind.capitalize()}: {change.target_path}",
        reason=reason,
        changes=[
            {
                "action": change.action,
                "source_path": change.source_path,
                "target_path": change.target_path,
                "content": change.content_preview,
                "repository_id": repository_id,
            }
        ],
        expected_consequences=consequences,
        diff=change.diff,
        status="pending",
    )
    db.add(proposal)
    db.commit()
    db.refresh(proposal)
    return proposal


def _get_proposal(db: Session, workspace_id: int, proposal_id: int) -> ChangeProposal:
    proposal = db.scalar(
        select(ChangeProposal).where(
            ChangeProposal.id == proposal_id,
            ChangeProposal.workspace_id == workspace_id,
        )
    )
    if proposal is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Proposal not found.")
    return proposal


@router.get("/proposals", response_model=list[ProposalOut])
def list_proposals(
    workspace_id: int,
    status_filter: str | None = None,
    db: Session = Depends(get_db),
) -> list[ProposalOut]:
    get_workspace(db, workspace_id)
    query = select(ChangeProposal).where(ChangeProposal.workspace_id == workspace_id)
    if status_filter:
        query = query.where(ChangeProposal.status == status_filter)
    proposals = db.scalars(query.order_by(ChangeProposal.id.desc())).all()
    return [_proposal_out(p, p.changes[0].get("repository_id") if p.changes else None) for p in proposals]


@router.get("/proposals/{proposal_id}", response_model=ProposalOut)
def read_proposal(
    workspace_id: int, proposal_id: int, db: Session = Depends(get_db)
) -> ProposalOut:
    proposal = _get_proposal(db, workspace_id, proposal_id)
    repository_id = proposal.changes[0].get("repository_id") if proposal.changes else None
    return _proposal_out(proposal, repository_id)



# --------------------------------------------------------------------------
# Planning endpoints -- these only READ the filesystem.
# --------------------------------------------------------------------------


@router.post("/proposals/move", response_model=ProposalOut, status_code=status.HTTP_201_CREATED)
def create_move_proposal(
    workspace_id: int, payload: MoveProposalCreate, db: Session = Depends(get_db)
) -> ProposalOut:
    """Plan a move or rename and store it for review. Changes nothing on disk."""
    repo, root = _writable_repository(db, workspace_id, payload.repository_id)
    try:
        change = plan_move(root, payload.source_path, payload.target_dir, payload.new_name)
    except ProposalError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc

    proposal = _store(
        db,
        workspace_id,
        repo.id,
        change.action,
        payload.title,
        payload.reason,
        change,
        f"'{change.source_path}' will no longer exist at its current path. "
        "Links pointing to it will need updating.",
    )
    return _proposal_out(proposal, repo.id)


@router.post("/proposals/edit", response_model=ProposalOut, status_code=status.HTTP_201_CREATED)
def create_edit_proposal(
    workspace_id: int, payload: EditProposalCreate, db: Session = Depends(get_db)
) -> ProposalOut:
    repo, root = _writable_repository(db, workspace_id, payload.repository_id)
    try:
        change = plan_edit(root, payload.path, payload.new_content)
    except ProposalError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc

    proposal = _store(
        db,
        workspace_id,
        repo.id,
        "edit",
        payload.title,
        payload.reason,
        change,
        "The document's content will change. The previous version remains "
        "recoverable through Git once you commit.",
    )
    return _proposal_out(proposal, repo.id)


@router.post(
    "/proposals/create", response_model=ProposalOut, status_code=status.HTTP_201_CREATED
)
def create_new_document_proposal(
    workspace_id: int, payload: CreateProposalCreate, db: Session = Depends(get_db)
) -> ProposalOut:
    repo, root = _writable_repository(db, workspace_id, payload.repository_id)
    try:
        change = plan_create(root, payload.target_path, payload.content)
    except ProposalError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc

    proposal = _store(
        db,
        workspace_id,
        repo.id,
        "create",
        payload.title,
        payload.reason,
        change,
        f"A new document will appear at '{change.target_path}' as an untracked "
        "file until you commit it.",
    )
    return _proposal_out(proposal, repo.id)



# --------------------------------------------------------------------------
# Decision endpoints -- the ONLY code path that writes to the filesystem.
# --------------------------------------------------------------------------


@router.post("/proposals/{proposal_id}/accept", response_model=AcceptProposalOut)
def accept_proposal(
    workspace_id: int, proposal_id: int, db: Session = Depends(get_db)
) -> AcceptProposalOut:
    """Apply an approved proposal. This is the human's explicit go-ahead.

    Nothing is committed to Git: the working tree is left dirty so the change
    can be reviewed and committed deliberately.
    """
    proposal = _get_proposal(db, workspace_id, proposal_id)
    if proposal.status != "pending":
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"Proposal is already {proposal.status} and cannot be applied again.",
        )
    if not proposal.changes:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Proposal has no changes.")

    first = proposal.changes[0]
    repository_id = first.get("repository_id")
    if repository_id is None:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY, "Proposal is missing its repository."
        )

    repo, root = _writable_repository(db, workspace_id, repository_id)

    # Re-validate against the current filesystem: the proposal may have been
    # created before intervening changes.
    change = PlannedChange(
        action=first["action"],
        source_path=first.get("source_path"),
        target_path=first["target_path"],
        content_preview=first.get("content"),
    )
    try:
        applied = apply_change(root, change)
    except ProposalError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc

    # A move changes the document's identity, so everything that recorded the
    # old path has to follow it. Done here rather than in the proposal service
    # because the service knows nothing about groups, and a caller that forgot
    # to do this would leave cards pointing at a file that is no longer there.
    if change.action in ("move", "rename") and first.get("source_path"):
        repoint_after_move(
            db,
            workspace_id,
            repository_id,
            first["source_path"],
            applied,
        )

    proposal.status = "accepted"
    proposal.decided_at = dt.datetime.now(dt.timezone.utc)
    db.commit()
    db.refresh(proposal)

    entries = git.status(root) if git.is_repo(root) else []
    return AcceptProposalOut(
        proposal=_proposal_out(proposal, repo.id),
        applied_paths=[applied],
        git_status=[GitStatusEntryOut(path=e.path, status=e.status) for e in entries],
    )


@router.post("/proposals/{proposal_id}/reject", response_model=ProposalOut)
def reject_proposal(
    workspace_id: int, proposal_id: int, db: Session = Depends(get_db)
) -> ProposalOut:
    """Decline a proposal. The filesystem is untouched."""
    proposal = _get_proposal(db, workspace_id, proposal_id)
    if proposal.status != "pending":
        raise HTTPException(
            status.HTTP_409_CONFLICT, f"Proposal is already {proposal.status}."
        )
    proposal.status = "rejected"
    proposal.decided_at = dt.datetime.now(dt.timezone.utc)
    db.commit()
    db.refresh(proposal)
    repository_id = proposal.changes[0].get("repository_id") if proposal.changes else None
    return _proposal_out(proposal, repository_id)
