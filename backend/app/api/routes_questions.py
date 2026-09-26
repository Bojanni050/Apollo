"""OpenQuestion management endpoints."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_decision, get_question, get_workspace
from app.db import get_db
from app.models import Conversation, Decision, OpenQuestion, utcnow
from app.schemas import DecisionOut, OpenQuestionCreate, OpenQuestionOut, OpenQuestionUpdate
from sqlalchemy.orm.attributes import flag_modified

router = APIRouter(prefix="/workspaces/{workspace_id}", tags=["questions"])


def _populate_addressed_by(db: Session, workspace_id: int, questions: list[OpenQuestion]) -> None:
    """Populate transient addressed_by attribute on questions from Decision.related_questions."""
    if not questions:
        return
    decisions = db.scalars(
        select(Decision).where(Decision.workspace_id == workspace_id)
    ).all()
    for q in questions:
        q_refs = {q.id, str(q.id), str(q.uid)}
        q.addressed_by = [
            d.id
            for d in decisions
            if any(ref in q_refs or str(ref) in q_refs for ref in (d.related_questions or []))
        ]


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
    _populate_addressed_by(db, workspace_id, questions)
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
    _populate_addressed_by(db, workspace_id, [question])
    return OpenQuestionOut.model_validate(question)


@router.get("/questions/{question_id}", response_model=OpenQuestionOut)
def read_question(
    workspace_id: int,
    question_id: int,
    db: Session = Depends(get_db),
) -> OpenQuestionOut:
    """Retrieve an open question by ID."""
    get_workspace(db, workspace_id)
    question = get_question(db, workspace_id, question_id)
    _populate_addressed_by(db, workspace_id, [question])
    return OpenQuestionOut.model_validate(question)


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
    _populate_addressed_by(db, workspace_id, [question])
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


@router.get("/questions/{question_id}/decisions", response_model=list[DecisionOut])
def list_question_decisions(
    workspace_id: int,
    question_id: int,
    db: Session = Depends(get_db),
) -> list[DecisionOut]:
    """List decisions that address this open question."""
    get_workspace(db, workspace_id)
    question = get_question(db, workspace_id, question_id)
    q_refs = {question.id, str(question.id), str(question.uid)}
    decisions = db.scalars(
        select(Decision).where(Decision.workspace_id == workspace_id).order_by(Decision.id.asc())
    ).all()
    matching = [
        d for d in decisions
        if any(ref in q_refs or str(ref) in q_refs for ref in (d.related_questions or []))
    ]
    return [DecisionOut.model_validate(d) for d in matching]


@router.post("/questions/{question_id}/decisions/{decision_id}", response_model=DecisionOut)
def link_decision_to_question(
    workspace_id: int,
    question_id: int,
    decision_id: int,
    db: Session = Depends(get_db),
) -> DecisionOut:
    """Link a decision to an open question (indicates the decision addresses the question)."""
    get_workspace(db, workspace_id)
    question = get_question(db, workspace_id, question_id)
    decision = get_decision(db, workspace_id, decision_id)

    related = list(decision.related_questions or [])
    q_refs = {question.id, str(question.id), str(question.uid)}
    if not any(ref in q_refs or str(ref) in q_refs for ref in related):
        related.append(question.id)
        decision.related_questions = related
        flag_modified(decision, "related_questions")
        db.commit()
        db.refresh(decision)
    return DecisionOut.model_validate(decision)


@router.delete("/questions/{question_id}/decisions/{decision_id}", response_model=DecisionOut)
def unlink_decision_from_question(
    workspace_id: int,
    question_id: int,
    decision_id: int,
    db: Session = Depends(get_db),
) -> DecisionOut:
    """Remove the association between a decision and an open question."""
    get_workspace(db, workspace_id)
    question = get_question(db, workspace_id, question_id)
    decision = get_decision(db, workspace_id, decision_id)

    q_refs = {question.id, str(question.id), str(question.uid)}
    related = [
        ref for ref in (decision.related_questions or [])
        if ref not in q_refs and str(ref) not in q_refs
    ]
    decision.related_questions = related
    flag_modified(decision, "related_questions")
    db.commit()
    db.refresh(decision)
    return DecisionOut.model_validate(decision)

