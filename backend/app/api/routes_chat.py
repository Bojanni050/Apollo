"""Architecture chat endpoints.

Conversations persist across restarts and stay bound to their workspace. Mode
switching updates only the mode -- it never discards messages, so a discussion
can move from Explore to Investigate to Apply without losing its context.

A message may name the document the reader was looking at. That is a pointer,
not content: it tells the model which file "this" means so it reads that file
rather than guessing between several, and it is applied to that one turn. The
document's text is not sent -- the agent has to read it through its own tools,
so an answer is always grounded in what is on disk at the time.

The pointer carries the repository as well as the path, because the path alone
is repository-relative and cannot say which of two registered repositories a
file belongs to.
"""
from __future__ import annotations

import logging
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import Response
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.api.deps import get_workspace, resolve_repo_root
from app.config import settings
from app.db import get_db
from app.llm import available_providers, get_provider, is_configured
from app.llm.base import LLMError, LLMNotConfigured
from app.llm.context import ContextBudgetError
from app.models import Conversation, Message, OpenQuestion, Repository
from app.prompts import VALID_MODES, DocumentFocus
from app.services.paths import PathSecurityError, safe_path, to_rel_path
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
from app.services.documents import DOC_SUFFIXES, TEXT_DOC_SUFFIXES

logger = logging.getLogger("apollo")

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


def _resolve_focus(
    db: Session,
    workspace_id: int,
    repositories: list[Repository],
    document_path: str | None,
) -> DocumentFocus | None:
    """Turn a client-supplied path into a validated focus pointer, or None.

    The path comes from the browser, so it is treated as untrusted input and
    resolved the same way any other repository path is: through
    :func:`safe_path`, against a repository this workspace has actually
    registered. A path that escapes the root, names a directory, is not a
    format the agent can read, does not exist, or lives in no registered
    repository is refused with a 400 rather than quietly dropped -- the reader
    asked a question about a specific file, and answering it about a different
    file is the failure this prevents.

    The resolved repository name travels with the path. The path alone is
    repository-relative, so two registered repositories that both hold
    ``architecture.md`` would otherwise resolve to whichever was tried first,
    and the model would read a different file than the one on screen.

    Documentation repositories are tried first, then the others in id order, so
    a workspace with both a docs repo and source repos resolves the common case
    without ambiguity.
    """
    if not document_path or not document_path.strip():
        return None

    candidate = document_path.strip().replace("\\", "/")
    ordered = sorted(repositories, key=lambda r: (r.kind != "documentation", r.id))

    for repo in ordered:
        try:
            root = resolve_repo_root(repo)
            target = safe_path(root, candidate)
        except (HTTPException, PathSecurityError):
            # An unauthorized root or an escaping path: this repository cannot
            # serve it. Try the next one, and refuse if none can.
            continue
        if not target.is_file():
            continue
        # The same set `read_document` accepts. Without this the focus could name
        # a file the agent is then unable to read -- a .py, a .png -- which
        # reintroduces exactly the "answering about a different file" failure
        # this function exists to prevent.
        if target.suffix.lower() not in DOC_SUFFIXES:
            continue
        return DocumentFocus(
            path=to_rel_path(root, target),
            title=_title_of_path(target),
            repository=repo.name,
        )

    raise HTTPException(
        status.HTTP_400_BAD_REQUEST,
        f"{candidate!r} is not a readable document in this workspace's repositories.",
    )


def _title_of_path(target: Path) -> str | None:
    """The document's own first heading, for the prompt.

    Read cheaply and best-effort: an unreadable file still has a valid path, and
    the path is what the model needs. A missing title is a worse prompt, not an
    error.

    Only text documents are read. A PDF or DOCX decoded as UTF-8 with
    replacement characters yields a title of mojibake, which is worse than no
    title at all -- it puts a confident-looking wrong string in the prompt.
    """
    if target.suffix.lower() not in TEXT_DOC_SUFFIXES:
        return None
    try:
        head = target.read_text(encoding="utf-8", errors="replace")[:2000]
    except OSError:
        return None
    for line in head.splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            title = stripped.lstrip("#").strip()
            if title:
                return title
    return None


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
    if payload.question_id is not None:
        q = db.scalar(
            select(OpenQuestion).where(
                OpenQuestion.id == payload.question_id,
                OpenQuestion.workspace_id == workspace_id,
            )
        )
        if q is None:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                f"Question {payload.question_id} not found in workspace {workspace_id}.",
            )
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
) -> Response:
    db.delete(_get_conversation(db, workspace_id, conversation_id))
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


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

    `document_path` names the file the reader had open. It is validated against
    the workspace's own repositories before use, and a path that does not
    resolve is refused rather than passed on: a focus pointer the agent cannot
    follow is worse than none, because the model would then answer "this
    document" from a guess.
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

    focus = _resolve_focus(db, workspace_id, repositories, payload.document_path)

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
        Agent(provider).run(db, conversation, payload.content, list(repositories), focus)
    except ContextBudgetError as exc:
        # The request could not be made to fit the configured context window.
        # That is the user's situation to fix (shorter message, or a model with
        # a larger window), not a provider fault -- so it must not be reported
        # as a bad gateway, and the internal reason is logged rather than shown.
        logger.warning("Context budget exceeded for conversation %s: %s", conversation.id, exc)
        raise HTTPException(
            status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            "This message is too large to fit the model's context window. "
            "Shorten it, or raise LLM_CONTEXT_TOKENS.",
        ) from exc
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
