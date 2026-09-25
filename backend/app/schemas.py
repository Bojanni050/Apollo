"""Pydantic API schemas for the Milestone 1 surface."""
from __future__ import annotations

import datetime as dt

from pydantic import BaseModel, ConfigDict, Field


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


class MessageOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    role: str
    content: str
    mode: str | None = None
    citations: list[dict] | None = None
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


DocumentNodeOut.model_rebuild()
