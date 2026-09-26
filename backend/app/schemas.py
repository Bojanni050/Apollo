"""Pydantic API schemas for the Milestone 1 surface."""
from __future__ import annotations

import datetime as dt
from typing import Any
import uuid

from pydantic import BaseModel, ConfigDict, Field, field_validator


class RepositoryCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    local_path: str = Field(min_length=1)
    branch: str = "main"
    kind: str = Field(default="source", pattern="^(documentation|source)$")
    writable: bool = False
    description: str | None = None


class RepositoryUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    branch: str | None = None
    description: str | None = None


class RepositoryOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    local_path: str
    branch: str
    kind: str
    writable: bool
    description: str | None
    is_git_repo: bool = False
    current_branch: str | None = None
    head_revision: str | None = None


class WorkspaceCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    description: str | None = None


# --------------------------------------------------------------------------
# Authentication
# --------------------------------------------------------------------------


class LoginIn(BaseModel):
    username: str = Field(min_length=1, max_length=200)
    password: str = Field(min_length=1, max_length=1000)


class LoginOut(BaseModel):
    authenticated: bool
    username: str | None = None
    # Lets the UI distinguish "logged in" from "auth is switched off locally".
    auth_required: bool = True


class AuthStatusOut(BaseModel):
    auth_required: bool
    authenticated: bool
    username: str | None = None


class WorkspaceUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = None


class WorkspaceOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    description: str | None
    created_at: dt.datetime
    repositories: list[RepositoryOut] = Field(default_factory=list)


class DocumentNodeOut(BaseModel):
    # build_tree returns a DocNode dataclass, so attribute-based coercion is
    # required here.
    model_config = ConfigDict(from_attributes=True)

    name: str
    path: str
    is_dir: bool
    size: int | None = None
    children: list["DocumentNodeOut"] = Field(default_factory=list)


class DocumentTreeOut(BaseModel):
    repository_id: int
    repository: str
    revision: str | None = None
    root: DocumentNodeOut


class DocumentOut(BaseModel):
    repository_id: int
    repository: str
    path: str
    title: str
    raw_markdown: str
    revision: str | None = None
    size: int


class SearchHitOut(BaseModel):
    path: str
    score: float
    title: str
    snippet: str
    line: int | None = None


class SearchOut(BaseModel):
    repository_id: int
    query: str
    hits: list[SearchHitOut]


class GitStatusEntryOut(BaseModel):
    path: str
    status: str


class GitChangesOut(BaseModel):
    repository_id: int
    repository: str
    branch: str | None = None
    revision: str | None = None
    entries: list[GitStatusEntryOut]
    diff: str


class MoveProposalCreate(BaseModel):
    """Request to plan a move or rename. Planning never touches the disk."""

    repository_id: int
    source_path: str = Field(..., min_length=1)
    target_dir: str = "."
    new_name: str | None = None
    reason: str = ""
    title: str = ""


class EditProposalCreate(BaseModel):
    repository_id: int
    path: str = Field(..., min_length=1)
    new_content: str
    reason: str = ""
    title: str = ""


class CreateProposalCreate(BaseModel):
    repository_id: int
    target_path: str = Field(..., min_length=1)
    content: str
    reason: str = ""
    title: str = ""


class ProposalOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    workspace_id: int
    kind: str
    title: str
    reason: str
    evidence: list | None = None
    changes: list | None = None
    expected_consequences: str
    diff: str | None = None
    status: str
    decided_at: dt.datetime | None = None
    created_at: dt.datetime
    repository_id: int | None = None


class AcceptProposalOut(BaseModel):
    proposal: ProposalOut
    applied_paths: list[str]
    # Reminder surfaced to the UI: we never commit on the user's behalf.
    requires_manual_commit: bool = True
    git_status: list[GitStatusEntryOut] = Field(default_factory=list)


class ChatStatusOut(BaseModel):
    llm_configured: bool
    providers: list[str]
    model: str | None = None
    base_url: str | None = None


class CitationOut(BaseModel):
    repository: str
    path: str
    start_line: int | None = None
    end_line: int | None = None
    revision: str | None = None
    evidence_type: str = "ai_interpretation"
    note: str | None = None
    decision_id: int | None = None
    question_id: int | None = None


class MessageOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    role: str
    content: str
    mode: str | None = None
    citations: list[dict] | None = None
    tool_calls: list[dict] | None = None
    created_at: dt.datetime


class ConversationCreate(BaseModel):
    title: str = "New conversation"
    mode: str = "explore"
    question_id: int | None = None


class ConversationUpdate(BaseModel):
    title: str | None = None
    mode: str | None = None
    archived: bool | None = None


class ConversationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    workspace_id: int
    title: str
    mode: str
    question_id: int | None = None
    archived: bool
    created_at: dt.datetime
    updated_at: dt.datetime
    message_count: int = 0


class ConversationDetailOut(ConversationOut):
    messages: list[MessageOut] = Field(default_factory=list)


class SendMessageIn(BaseModel):
    content: str = Field(..., min_length=1)


class SendMessageOut(BaseModel):
    user_message: MessageOut
    assistant_message: MessageOut


class InventoryRunRequest(BaseModel):
    repository_id: int | None = None
    path: str = "."


class InventoryItemOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    source_path: str
    purpose: str
    suggested_path: str | None = None
    confidence: float | None = None
    overlaps: list | None = None
    ambiguous: bool
    note: str | None = None
    decision: str  # pending | applied | skipped
    # The target path including the original filename, if a move is proposed.
    target_path: str | None = None
    needs_move: bool = False
    # Other categories the model seriously weighed, so a judgement can be
    # reviewed rather than taken on trust.
    alternatives: list[str] = Field(default_factory=list)
    # The model's stated evidence for the classification.
    reason: str | None = None
    # True when the classification rests on a structural digest rather than the
    # whole document, and when the model reported low confidence. Both mean the
    # suggestion deserves a human look before it is applied.
    partial: bool = False
    low_confidence: bool = False


class InventoryRunOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    workspace_id: int
    status: str
    summary: str | None = None
    created_at: dt.datetime
    items: list[InventoryItemOut] = Field(default_factory=list)


class InventoryApplyRequest(BaseModel):
    item_ids: list[int] = Field(default_factory=list)
    # Guard against acting on a stale view of the plan.
    expected_source_paths: dict[str, str] | None = None


class InventoryApplyOut(BaseModel):
    run_id: int
    applied: list[str]
    skipped: list[dict[str, str]]
    requires_manual_commit: bool = True


class InventoryDecideOut(BaseModel):
    run_id: int
    item: InventoryItemOut
    applied_path: str | None = None
    requires_manual_commit: bool = True


# --------------------------------------------------------------------------
# Questions & Decisions
# --------------------------------------------------------------------------


class OpenQuestionCreate(BaseModel):
    title: str = Field(min_length=1, max_length=300)
    description: str = ""
    evidence: list[Any] | None = Field(default_factory=list)
    affected: list[Any] | None = Field(default_factory=list)
    status: str = Field(default="open", pattern="^(open|answered|resolved)$")
    source: str = Field(default="manual", max_length=20)
    conversation_id: int | None = None
    uid: uuid.UUID | None = None
    resolution: str | None = None
    resolved_at: dt.datetime | None = None

    @field_validator("title")
    @classmethod
    def validate_title(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("title cannot be empty or whitespace only")
        return v.strip()


class OpenQuestionUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=300)
    description: str | None = None
    evidence: list[Any] | None = None
    affected: list[Any] | None = None
    status: str | None = Field(default=None, pattern="^(open|answered|resolved)$")
    source: str | None = Field(default=None, max_length=20)
    conversation_id: int | None = None
    resolution: str | None = None
    resolved_at: dt.datetime | None = None

    @field_validator("title")
    @classmethod
    def validate_title(cls, v: str | None) -> str | None:
        if v is not None and not v.strip():
            raise ValueError("title cannot be empty or whitespace only")
        return v.strip() if v is not None else None


class OpenQuestionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    workspace_id: int
    uid: uuid.UUID
    title: str
    description: str
    evidence: list[Any] | None = None
    affected: list[Any] | None = None
    status: str
    source: str
    conversation_id: int | None = None
    resolution: str | None = None
    resolved_at: dt.datetime | None = None
    addressed_by: list[int] = Field(default_factory=list)
    created_at: dt.datetime
    updated_at: dt.datetime


class DecisionCreate(BaseModel):
    title: str = Field(min_length=1, max_length=300)
    context: str = ""
    decision: str = ""
    rationale: str = ""
    consequences: str = ""
    status: str = Field(default="proposed", pattern="^(proposed|approved|rejected|superseded)$")
    decided_on: dt.datetime | None = None
    approved_at: dt.datetime | None = None
    markdown_path: str | None = Field(default=None, max_length=1000)
    related_documents: list[Any] | None = Field(default_factory=list)
    related_questions: list[Any] | None = Field(default_factory=list)

    @field_validator("title")
    @classmethod
    def validate_title(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("title cannot be empty or whitespace only")
        return v.strip()


class DecisionUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=300)
    context: str | None = None
    decision: str | None = None
    rationale: str | None = None
    consequences: str | None = None
    status: str | None = Field(default=None, pattern="^(proposed|approved|rejected|superseded)$")
    decided_on: dt.datetime | None = None
    approved_at: dt.datetime | None = None
    markdown_path: str | None = Field(default=None, max_length=1000)
    related_documents: list[Any] | None = None
    related_questions: list[Any] | None = None

    @field_validator("title")
    @classmethod
    def validate_title(cls, v: str | None) -> str | None:
        if v is not None and not v.strip():
            raise ValueError("title cannot be empty or whitespace only")
        return v.strip() if v is not None else None


class DecisionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    workspace_id: int
    title: str
    context: str
    decision: str
    rationale: str
    consequences: str
    status: str
    decided_on: dt.datetime | None = None
    approved_at: dt.datetime | None = None
    markdown_path: str | None = None
    related_documents: list[Any] | None = None
    related_questions: list[Any] | None = None
    superseded_by_id: int | None = None
    supersedes_ids: list[int] = Field(default_factory=list)
    created_at: dt.datetime
    updated_at: dt.datetime


class DecisionApproveOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    decision: DecisionOut
    approved: bool = True
    sync_status: str
    markdown_path: str | None = None
    diff: str | None = None
    git_status: list[GitStatusEntryOut] = Field(default_factory=list)
    message: str | None = None


class DecisionSupersedeIn(BaseModel):
    superseded_by_id: int = Field(gt=0, description="ID of the newer decision that supersedes this one")


class DecisionSupersedeOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    decision: DecisionOut
    superseded_by: DecisionOut
    sync_status: str
    markdown_path: str | None = None
    diff: str | None = None
    git_status: list[GitStatusEntryOut] = Field(default_factory=list)
    message: str | None = None


class ConsistencyCheckIn(BaseModel):
    title: str = Field(min_length=1, max_length=300)
    decision: str = ""
    context: str = ""
    rationale: str = ""
    consequences: str = ""
    decision_id: int | None = None


class ConsistencyFinding(BaseModel):
    type: str  # conflict | overlap | compatible
    decision_id: int
    title: str
    reason: str
    proposed_claim: str = ""
    existing_claim: str = ""
    markdown_path: str | None = None


class ConsistencyCheckOut(BaseModel):
    status: str  # No apparent conflict | Potential conflict | Potential overlap | Insufficient evidence
    summary: str
    candidates_evaluated: list[dict[str, Any]] = Field(default_factory=list)
    findings: list[ConsistencyFinding] = Field(default_factory=list)
    evidence: list[dict[str, Any]] = Field(default_factory=list)


DocumentNodeOut.model_rebuild()

