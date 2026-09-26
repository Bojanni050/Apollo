"""OpenQuestion management endpoints."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_question, get_workspace
from app.db import get_db
from app.models import Conversation, OpenQuestion, utcnow
from app.schemas import OpenQuestionCreate, OpenQuestionOut, OpenQuestionUpdate

router = APIRouter(prefix="/workspaces/{workspace_id}", tags=["questions"])


@router.get("/questions", response_model=list[OpenQuestionOut])
def list_questions(
    workspace_id: int,
    status: str | None = Query(None, description="Filter by status (open, answered, resolved)"),
    conversation_id: int | None = Query(None, description="Filter by conversation ID"),
    db: Session = Depends(get_db),
) -> list[OpenQuestionOut]:
    """List open questions registered in a workspace."""
    get_workspace(db, workspace_id)
    query = select(OpenQuestion).where(OpenQuestion.workspace_id == workspace_id)
    if status is not None:
        query = query.where(OpenQuestion.status == status)
    if conversation_id is not None:
        query = query.where(OpenQuestion.conversation_id == conversation_id)
    questions = db.scalars(query.order_by(OpenQuestion.id.asc())).all()
    return [OpenQuestionOut.model_validate(q) for q in questions]


@router.post(
    "/questions",
    response_model=OpenQuestionOut,
    status_code=status.HTTP_201_CREATED,
)
def create_question(
    workspace_id: int,
    payload: OpenQuestionCreate,
    db: Session = Depends(get_db),
) -> OpenQuestionOut:
    """Create a new open question in the workspace."""
    workspace = get_workspace(db, workspace_id)

    if payload.conversation_id is not None:
        conv = db.scalar(
            select(Conversation).where(
                Conversation.id == payload.conversation_id,
                Conversation.workspace_id == workspace_id,
            )
        )
        if conv is None:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                f"Conversation {payload.conversation_id} not found in workspace {workspace_id}.",
            )

    resolved_at = payload.resolved_at
    if payload.status == "resolved" and resolved_at is None:
        resolved_at = utcnow()

    kwargs = {}
    if payload.uid is not None:
        kwargs["uid"] = payload.uid

    question = OpenQuestion(
        workspace_id=workspace.id,
        title=payload.title.strip(),
        description=payload.description or "",
        evidence=payload.evidence or [],
        affected=payload.affected or [],
        status=payload.status,
        source=payload.source or "manual",
        conversation_id=payload.conversation_id,
        resolution=payload.resolution,
        resolved_at=resolved_at,
        **kwargs,
    )
    db.add(question)
    db.commit()
    db.refresh(question)
    return OpenQuestionOut.model_validate(question)


@router.get("/questions/{question_id}", response_model=OpenQuestionOut)
def read_question(
    workspace_id: int,
    question_id: int,
    db: Session = Depends(get_db),
) -> OpenQuestionOut:
    """Retrieve an open question by ID."""
    get_workspace(db, workspace_id)
    return OpenQuestionOut.model_validate(get_question(db, workspace_id, question_id))


@router.patch("/questions/{question_id}", response_model=OpenQuestionOut)
def update_question(
    workspace_id: int,
    question_id: int,
    payload: OpenQuestionUpdate,
    db: Session = Depends(get_db),
) -> OpenQuestionOut:
    """Update title, description, status, resolution, or relationships of a question."""
    get_workspace(db, workspace_id)
    question = get_question(db, workspace_id, question_id)

    if payload.conversation_id is not None:
        conv = db.scalar(
            select(Conversation).where(
                Conversation.id == payload.conversation_id,
                Conversation.workspace_id == workspace_id,
            )
        )
        if conv is None:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                f"Conversation {payload.conversation_id} not found in workspace {workspace_id}.",
            )
        question.conversation_id = payload.conversation_id

    if payload.title is not None:
        question.title = payload.title.strip()
    if payload.description is not None:
        question.description = payload.description
    if payload.evidence is not None:
        question.evidence = payload.evidence
    if payload.affected is not None:
        question.affected = payload.affected
    if payload.source is not None:
        question.source = payload.source
    if payload.resolution is not None:
        question.resolution = payload.resolution

    if payload.status is not None:
        question.status = payload.status
        if payload.status == "resolved":
            question.resolved_at = payload.resolved_at or question.resolved_at or utcnow()
        elif payload.resolved_at is None:
            question.resolved_at = None

    if payload.resolved_at is not None:
        question.resolved_at = payload.resolved_at

    db.commit()
    db.refresh(question)
    return OpenQuestionOut.model_validate(question)


@router.delete("/questions/{question_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_question(
    workspace_id: int,
    question_id: int,
    db: Session = Depends(get_db),
) -> None:
    """Delete an open question."""
    get_workspace(db, workspace_id)
    question = get_question(db, workspace_id, question_id)
    db.delete(question)
    db.commit()
