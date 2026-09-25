"""Architecture chat endpoints.

Conversations persist across restarts and stay bound to their workspace. Mode
switching updates only the mode -- it never discards messages, so a discussion
can move from Explore to Investigate to Apply without losing its context.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.api.deps import get_workspace
from app.config import settings
from app.db import get_db
from app.llm import available_providers, get_provider, is_configured
from app.llm.base import LLMError, LLMNotConfigured
from app.models import Conversation, Message, Repository
from app.prompts import VALID_MODES
from app.schemas import (
    ChatStatusOut,
    ConversationCreate,
    ConversationDetailOut,
    ConversationOut,
    ConversationUpdate,
    SendMessageIn,
    SendMessageOut,
)
from app.services.agent import Agent

router = APIRouter(prefix="/workspaces/{workspace_id}", tags=["chat"])


def _conversation_out(conversation: Conversation) -> ConversationOut:
    out = ConversationOut.model_validate(conversation)
    out.message_count = len(conversation.messages)
    return out


def _get_conversation(db: Session, workspace_id: int, conversation_id: int) -> Conversation:
    conversation = db.scalar(
        select(Conversation)
        .options(selectinload(Conversation.messages))
        .where(Conversation.id == conversation_id, Conversation.workspace_id == workspace_id)
    )
    if conversation is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Conversation not found.")
    return conversation


def _validate_mode(mode: str) -> None:
    if mode not in VALID_MODES:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            f"Unknown mode {mode!r}. Valid modes: {', '.join(VALID_MODES)}.",
        )


@router.get("/chat/status", response_model=ChatStatusOut)
def chat_status() -> ChatStatusOut:
    """Report whether the AI is usable, so the UI can explain itself."""
    return ChatStatusOut(
        llm_configured=is_configured(),
        providers=available_providers(),
        model=settings.llm_model,
        base_url=settings.llm_base_url,
    )


@router.get("/conversations", response_model=list[ConversationOut])
def list_conversations(
    workspace_id: int,
    include_archived: bool = False,
    db: Session = Depends(get_db),
) -> list[ConversationOut]:
    get_workspace(db, workspace_id)
    query = select(Conversation).where(Conversation.workspace_id == workspace_id)
    if not include_archived:
        query = query.where(Conversation.archived.is_(False))
    conversations = db.scalars(
        query.options(selectinload(Conversation.messages)).order_by(Conversation.updated_at.desc())
    ).all()
    return [_conversation_out(c) for c in conversations]


@router.post(
    "/conversations", response_model=ConversationOut, status_code=status.HTTP_201_CREATED
)
def create_conversation(
    workspace_id: int, payload: ConversationCreate, db: Session = Depends(get_db)
) -> ConversationOut:
    workspace = get_workspace(db, workspace_id)
    _validate_mode(payload.mode)
    conversation = Conversation(
        workspace_id=workspace.id,
        title=payload.title,
        mode=payload.mode,
        question_id=payload.question_id,
    )
    db.add(conversation)
    db.commit()
    db.refresh(conversation)
    return _conversation_out(conversation)



@router.get("/conversations/{conversation_id}", response_model=ConversationDetailOut)
def read_conversation(
    workspace_id: int, conversation_id: int, db: Session = Depends(get_db)
) -> ConversationDetailOut:
    conversation = _get_conversation(db, workspace_id, conversation_id)
    out = ConversationDetailOut.model_validate(conversation)
    out.messages = list(conversation.messages)
    out.message_count = len(out.messages)
    return out


@router.patch("/conversations/{conversation_id}", response_model=ConversationOut)
def update_conversation(
    workspace_id: int,
    conversation_id: int,
    payload: ConversationUpdate,
    db: Session = Depends(get_db),
) -> ConversationOut:
    """Update title, mode or archived state.

    Changing the mode preserves the entire transcript by design: the mode only
    selects the system prompt.
    """
    conversation = _get_conversation(db, workspace_id, conversation_id)
    if payload.mode is not None:
        _validate_mode(payload.mode)
        conversation.mode = payload.mode
    if payload.title is not None:
        conversation.title = payload.title
    if payload.archived is not None:
        conversation.archived = payload.archived
    db.commit()
    db.refresh(conversation)
    return _conversation_out(conversation)


@router.delete(
    "/conversations/{conversation_id}", status_code=status.HTTP_204_NO_CONTENT
)
def delete_conversation(
    workspace_id: int, conversation_id: int, db: Session = Depends(get_db)
) -> None:
    db.delete(_get_conversation(db, workspace_id, conversation_id))
    db.commit()


@router.post(
    "/conversations/{conversation_id}/messages",
    response_model=SendMessageOut,
    status_code=status.HTTP_201_CREATED,
)
def send_message(
    workspace_id: int,
    conversation_id: int,
    payload: SendMessageIn,
    db: Session = Depends(get_db),
) -> SendMessageOut:
    """Send a message and run the agent to completion.

    The agent holds read-only tools only; it cannot modify any file.
    """
    conversation = _get_conversation(db, workspace_id, conversation_id)
    if conversation.archived:
        raise HTTPException(status.HTTP_409_CONFLICT, "This conversation is archived.")

    repositories = db.scalars(
        select(Repository).where(Repository.workspace_id == workspace_id).order_by(Repository.id)
    ).all()
    if not repositories:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "This workspace has no repositories registered, so there is nothing to discuss.",
        )

    # Only now check the provider: a workspace with nothing to discuss is a
    # workspace problem (409), not a missing-LLM problem (503).
    try:
        provider = get_provider()
    except LLMNotConfigured as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(exc)) from exc
    except LLMError as exc:
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, str(exc)) from exc

    before = set(db.scalars(select(Message.id).where(Message.conversation_id == conversation.id)))
    try:
        Agent(provider).run(db, conversation, payload.content, list(repositories))
    except LLMError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"LLM error: {exc}") from exc

    after = (
        db.scalars(
            select(Message)
            .where(
                Message.conversation_id == conversation.id,
                Message.id.notin_(before) if before else Message.id > 0,
            )
            .order_by(Message.id)
        )
        .all()
    )
    user_message = next((m for m in after if m.role == "user"), None)
    assistant_message = next((m for m in after if m.role == "assistant"), None)
    if assistant_message is None:
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY, "The model did not return a response."
        )

    # Title the conversation from its first question, for a usable sidebar.
    if conversation.title == "New conversation" and user_message is not None:
        first_line = user_message.content.strip().splitlines()
        conversation.title = first_line[0][:80] if first_line else "New conversation"
        db.commit()

    return SendMessageOut(user_message=user_message, assistant_message=assistant_message)
