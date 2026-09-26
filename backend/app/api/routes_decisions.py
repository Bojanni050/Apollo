"""Decision management endpoints."""
from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import (
    get_decision,
    get_documentation_repository,
    get_workspace,
    resolve_repo_root,
)
from app.db import get_db
from app.models import Decision, OpenQuestion, utcnow
from app.schemas import (
    DecisionApproveOut,
    DecisionCreate,
    DecisionOut,
    DecisionUpdate,
    GitStatusEntryOut,
)
from app.services.adr import (
    ADRConflictError,
    ADRError,
    AmbiguousADRLocationError,
    sync_decision_adr,
)

router = APIRouter(prefix="/workspaces/{workspace_id}", tags=["decisions"])


def _validate_related_questions(
    db: Session, workspace_id: int, related_questions: list[Any]
) -> None:
    """Ensure every referenced question exists in the same workspace."""
    for q_ref in related_questions:
        q = None
        if isinstance(q_ref, int) or (isinstance(q_ref, str) and q_ref.isdigit()):
            q = db.scalar(
                select(OpenQuestion).where(
                    OpenQuestion.id == int(q_ref),
                    OpenQuestion.workspace_id == workspace_id,
                )
            )
        elif isinstance(q_ref, str):
            try:
                parsed_uid = uuid.UUID(q_ref)
                q = db.scalar(
                    select(OpenQuestion).where(
                        OpenQuestion.uid == parsed_uid,
                        OpenQuestion.workspace_id == workspace_id,
                    )
                )
            except ValueError:
                pass
        elif isinstance(q_ref, uuid.UUID):
            q = db.scalar(
                select(OpenQuestion).where(
                    OpenQuestion.uid == q_ref,
                    OpenQuestion.workspace_id == workspace_id,
                )
            )

        if q is None:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                f"Referenced question {q_ref!r} not found in workspace {workspace_id}.",
            )


def _validate_related_documents(related_documents: list[Any]) -> None:
    """Ensure document references are clean relative paths without traversal."""
    for doc in related_documents:
        if not isinstance(doc, str) or not doc.strip():
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                "Related document references must be non-empty path strings.",
            )
        clean = doc.strip().replace("\\", "/")
        if ".." in clean or clean.startswith("/"):
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                f"Invalid document path {doc!r}: directory traversal or absolute paths are not permitted.",
            )


@router.get("/decisions", response_model=list[DecisionOut])
def list_decisions(
    workspace_id: int,
    status: str | None = Query(
        None, description="Filter by status (proposed, approved, rejected, superseded)"
    ),
    db: Session = Depends(get_db),
) -> list[DecisionOut]:
    """List architectural decisions in a workspace."""
    get_workspace(db, workspace_id)
    query = select(Decision).where(Decision.workspace_id == workspace_id)
    if status is not None:
        query = query.where(Decision.status == status)
    decisions = db.scalars(query.order_by(Decision.id.asc())).all()
    return [DecisionOut.model_validate(d) for d in decisions]


@router.post(
    "/decisions",
    response_model=DecisionOut,
    status_code=status.HTTP_201_CREATED,
)
def create_decision(
    workspace_id: int,
    payload: DecisionCreate,
    db: Session = Depends(get_db),
) -> DecisionOut:
    """Record an architectural decision."""
    workspace = get_workspace(db, workspace_id)

    if payload.related_questions:
        _validate_related_questions(db, workspace_id, payload.related_questions)
    if payload.related_documents:
        _validate_related_documents(payload.related_documents)

    decided_on = payload.decided_on
    approved_at = payload.approved_at
    if payload.status == "approved":
        if approved_at is None:
            approved_at = utcnow()
        if decided_on is None:
            decided_on = utcnow()

    decision = Decision(
        workspace_id=workspace.id,
        title=payload.title.strip(),
        context=payload.context or "",
        decision=payload.decision or "",
        rationale=payload.rationale or "",
        consequences=payload.consequences or "",
        status=payload.status,
        decided_on=decided_on,
        approved_at=approved_at,
        markdown_path=payload.markdown_path,
        related_documents=payload.related_documents or [],
        related_questions=[
            str(q) if isinstance(q, uuid.UUID) else q
            for q in (payload.related_questions or [])
        ],
    )
    db.add(decision)
    db.commit()
    db.refresh(decision)
    return DecisionOut.model_validate(decision)


@router.get("/decisions/{decision_id}", response_model=DecisionOut)
def read_decision(
    workspace_id: int,
    decision_id: int,
    db: Session = Depends(get_db),
) -> DecisionOut:
    """Retrieve an architectural decision by ID."""
    get_workspace(db, workspace_id)
    return DecisionOut.model_validate(get_decision(db, workspace_id, decision_id))


@router.patch("/decisions/{decision_id}", response_model=DecisionOut)
def update_decision(
    workspace_id: int,
    decision_id: int,
    payload: DecisionUpdate,
    db: Session = Depends(get_db),
) -> DecisionOut:
    """Update title, status, context, decision, rationale, or relationships."""
    get_workspace(db, workspace_id)
    decision = get_decision(db, workspace_id, decision_id)

    if payload.related_questions is not None:
        _validate_related_questions(db, workspace_id, payload.related_questions)
        decision.related_questions = [
            str(q) if isinstance(q, uuid.UUID) else q
            for q in payload.related_questions
        ]

    if payload.related_documents is not None:
        _validate_related_documents(payload.related_documents)
        decision.related_documents = payload.related_documents

    if payload.title is not None:
        decision.title = payload.title.strip()
    if payload.context is not None:
        decision.context = payload.context
    if payload.decision is not None:
        decision.decision = payload.decision
    if payload.rationale is not None:
        decision.rationale = payload.rationale
    if payload.consequences is not None:
        decision.consequences = payload.consequences
    if payload.markdown_path is not None:
        decision.markdown_path = payload.markdown_path

    if payload.status is not None:
        decision.status = payload.status
        if payload.status == "approved":
            decision.approved_at = payload.approved_at or decision.approved_at or utcnow()
            decision.decided_on = payload.decided_on or decision.decided_on or utcnow()
        elif payload.status != "approved" and payload.approved_at is None:
            decision.approved_at = None

    if payload.decided_on is not None:
        decision.decided_on = payload.decided_on
    if payload.approved_at is not None:
        decision.approved_at = payload.approved_at

    db.commit()
    db.refresh(decision)
    return DecisionOut.model_validate(decision)


@router.delete("/decisions/{decision_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_decision(
    workspace_id: int,
    decision_id: int,
    db: Session = Depends(get_db),
) -> None:
    """Delete an architectural decision."""
    get_workspace(db, workspace_id)
    decision = get_decision(db, workspace_id, decision_id)
    db.delete(decision)
    db.commit()


@router.post(
    "/decisions/{decision_id}/approve",
    response_model=DecisionApproveOut,
    status_code=status.HTTP_200_OK,
)
def approve_decision(
    workspace_id: int,
    decision_id: int,
    db: Session = Depends(get_db),
) -> DecisionApproveOut:
    """Explicitly approve an architectural decision and synchronize its ADR document.

    Workflow:
    1. Retrieve Decision (404 if missing or not in workspace).
    2. Retrieve workspace documentation repository (409 if missing, 403 if read-only).
    3. Render / update the durable ADR Markdown document in the documentation repository.
    4. Transition Decision state to 'approved' and record timestamps.
    5. Return synchronization status, diff, and working tree Git status.
    """
    get_workspace(db, workspace_id)
    decision = get_decision(db, workspace_id, decision_id)

    repo = get_documentation_repository(db, workspace_id)
    if not repo.writable:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            f"Documentation repository '{repo.name}' is read-only. Only writable "
            "repositories can receive ADR documents.",
        )
    root = resolve_repo_root(repo)

    # 1. Update Decision status to approved
    decision.status = "approved"
    if decision.approved_at is None:
        decision.approved_at = utcnow()
    if decision.decided_on is None:
        decision.decided_on = utcnow()

    # 2. Synchronize ADR markdown
    try:
        sync_result = sync_decision_adr(root, decision)
    except AmbiguousADRLocationError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    except ADRConflictError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    except ADRError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc

    decision.markdown_path = sync_result.markdown_path
    db.commit()
    db.refresh(decision)

    return DecisionApproveOut(
        decision=DecisionOut.model_validate(decision),
        approved=True,
        sync_status=sync_result.sync_status,
        markdown_path=sync_result.markdown_path,
        diff=sync_result.diff,
        git_status=[GitStatusEntryOut(path=e.path, status=e.status) for e in sync_result.git_status],
        message=sync_result.message,
    )
