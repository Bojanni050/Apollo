"""Pydantic API schemas for the Milestone 1 surface."""
from __future__ import annotations

import datetime as dt
from typing import Any, Literal
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
    #: True for the repository Apollo keeps itself: the inbox storage. Present so
    #: a caller can tell the intake from the folder the operator registered
    #: without having to compare paths.
    is_storage: bool = False
    is_git_repo: bool = False
    current_branch: str | None = None
    head_revision: str | None = None
    # ---- Source-repository metadata (None for documentation repos) --------
    source_type: str | None = None
    source_url: str | None = None
    status: str | None = None
    status_message: str | None = None
    last_synced_at: dt.datetime | None = None


# --------------------------------------------------------------------------
# Repository sources (architecture evidence)
# --------------------------------------------------------------------------


class SourceCreate(BaseModel):
    """Register a repository source: a local path or a Git repository URL.

    The source type is inferred from the location when not given explicitly.
    GitHub sources are registered pending synchronization; nothing is cloned
    until the operator explicitly refreshes.
    """

    name: str = Field(min_length=1, max_length=200)
    location: str = Field(min_length=1, max_length=1000)
    source_type: str | None = Field(default=None, pattern="^(local|github)$")
    branch: str | None = Field(default=None, max_length=200)
    description: str | None = None


class SourceSyncOut(BaseModel):
    repository: RepositoryOut
    action: str
    status: str
    message: str | None = None
    branch: str | None = None
    revision: str | None = None


class ManifestEntryOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    index: int
    repo: str
    path: str
    branch: str | None = None
    source_type: str
    valid: bool
    error: str | None = None
    action: str
    existing_name: str | None = None


class ManifestPreviewOut(BaseModel):
    """What the operator sees before confirming an import. Nothing is applied."""

    total: int
    valid_count: int
    invalid_count: int
    new_count: int
    duplicate_count: int
    entries: list[ManifestEntryOut]


class ManifestImportOut(BaseModel):
    imported: list[RepositoryOut] = Field(default_factory=list)
    duplicates: list[str] = Field(default_factory=list)
    invalid: list[dict[str, str]] = Field(default_factory=list)
    manifest_path: str | None = None


class ManifestValidateIn(BaseModel):
    """The raw .sources.yaml content to validate or import."""

    content: str = Field(min_length=1, max_length=512 * 1024)


class ManifestImportIn(ManifestValidateIn):
    # Confirmed by the operator after reviewing the preview.
    confirm: bool = False


class SourceFileOut(BaseModel):
    path: str
    size: int


class SourceFileListOut(BaseModel):
    repository_id: int
    repository: str
    path: str
    files: list[SourceFileOut]


class SourceReadOut(BaseModel):
    repository_id: int
    repository: str
    path: str
    content: str
    start_line: int
    end_line: int
    total_lines: int


class SourceSearchHitOut(BaseModel):
    path: str
    line: int
    snippet: str
    score: float = 0.0


class SourceSearchOut(BaseModel):
    repository_id: int
    repository: str
    query: str
    hits: list[SourceSearchHitOut]


class SourceStructureOut(BaseModel):
    repository_id: int
    repository: str
    structure: str


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


class DocumentLinkOut(BaseModel):
    """One real Markdown link, as the author wrote it."""

    path: str
    text: str


class ExternalReferenceOut(BaseModel):
    """A link out of the repository. Reported, never fetched."""

    target: str
    text: str


class DocumentLinksOut(BaseModel):
    """The links into and out of one document.

    Separate from `DocumentOut` because these are derived from the whole
    repository, not from one file, and are worth requesting separately: the
    reader usually wants the file, and the link graph only when the panel is
    opened.
    """

    repository_id: int
    path: str
    outbound: list[DocumentLinkOut]
    inbound: list[DocumentLinkOut]
    external: list[ExternalReferenceOut]


# ---------------------------------------------------------------------------
# Visual groups
# ---------------------------------------------------------------------------


class GroupOut(BaseModel):
    """A group as the UI sees it, with its size resolved.

    ``source`` is on the wire rather than inferred from the UI: a group Delphi
    proposed and a group the reader made look identical otherwise, and the whole
    point of labelling relationships by origin applies to the arrangement too.
    """

    id: int
    name: str
    description: str | None = None
    source: str
    is_archive: bool
    position: int
    layout: str
    document_count: int = 0
    #: The folder this group's documents belong in, or None for a view-only
    #: group. On the wire because the board has to be able to say which of its
    #: groups rearrange files and which only rearrange the view -- a reader
    #: cannot make that distinction from the name alone.
    folder: str | None = None
    #: False only for a Delphi proposal the reader has not accepted or rejected
    #: yet. Always true for a group the reader made themselves.
    reviewed: bool = True


class GroupCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=2000)
    # Defaults to "user" because a group made through this endpoint is by
    # definition made by the reader. Delphi's groups arrive through the pulse
    # flow, which sets "ai" itself.
    source: str = "user"
    is_archive: bool = False
    layout: str = "grid"
    # No folder by default: a new group is a view, and giving it a folder would
    # make the very first document dropped into it propose a move.
    folder: str | None = Field(default=None, max_length=200)


class GroupUpdate(BaseModel):
    """Every field optional, so a rename and a description edit are the same call.

    ``folder`` is in here but is *not* cleared by omission: a request that does
    not mention it leaves it alone. Clearing it is an explicit ``null``, because
    "you did not say" and "remove the folder" are different intentions, and
    guessing between them would either lose a folder the reader set up or keep
    one they just removed. That needs ``model_fields_set``, which is why the
    route below checks it rather than reading the attribute.
    """

    name: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=2000)
    layout: str | None = None
    #: Absent leaves the folder alone; an explicit null clears it.
    folder: str | None = Field(default=None, max_length=200)


class GroupMoveOut(BaseModel):
    """The result of dragging a document onto a group.

    ``proposal_id`` is the only thing here, and it is null more often than not:
    most groups are views, and a drop into a view rearranges the board and
    nothing else. Reporting that honestly -- rather than always returning a
    proposal -- is what keeps the distinction between the two kinds of group
    visible to the reader.
    """

    proposal_id: int | None = None


class GroupDocumentOut(BaseModel):
    """A document as a group member.

    ``repository_id`` travels with the path because that pair is the document's
    identity: two repositories can both hold ``architecture.md``.
    """

    repository_id: int
    path: str
    position: int
    placed_by: str | None = None
    #: The move this placement proposed, when the group has a folder and the
    #: document was not already filed in it.
    #:
    #: On the response rather than only in the proposal list, because the reader
    #: has just dropped a document and the next thing they need to know is "there
    #: is a card waiting for you" -- not "the drop worked, go and find the other
    #: screen". None means the placement changed only the board, which is a
    #: perfectly good outcome and must not be dressed up as a pending action.
    proposal_id: int | None = None


class GroupPlacementRequest(BaseModel):
    """A document being put into a group by whoever is reading the board.

    There is deliberately no ``placed_by`` here. This endpoint is the reader
    acting, so the server records the placement as theirs: a client that could
    declare its own placement provisional would be able to talk a later analysis
    into moving the document back, and a reader's arrangement is exactly what
    must survive that. Delphi's placements are written by the analysis itself,
    which is the only other writer.
    """

    repository_id: int
    path: str = Field(..., min_length=1, max_length=1000)


class GroupMoveRequest(GroupPlacementRequest):
    """A drag between two groups.

    ``from_group_id`` is optional so that a drop onto a group the document is
    not in works without the caller having to check first. When it is given, the
    document leaves that group in the same request.
    """

    to_group_id: int
    from_group_id: int | None = None



class SendMessageIn(BaseModel):
    content: str = Field(..., min_length=1)
    # The document the reader was looking at when they asked. Optional, because
    # a conversation can start from anywhere, and nullable on purpose: absent
    # and empty both mean "no document in view", which is different from "a
    # document named ''". A path is a hint for where to look, never content --
    # the agent still has to read the file through its tools, so this cannot be
    # used to smuggle text in ahead of the file's real contents.
    document_path: str | None = Field(
        default=None, max_length=500, description="Repo-relative path of the document in view"
    )


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
# AI Pulse
# --------------------------------------------------------------------------
class PulseConnection(BaseModel):
    """A link between two documents, as proposed by the Pulse model.

    ``path`` is the target document; ``relation`` is one of relates-to /
    supports / contradicts / extends; ``why`` is the model's one-sentence
    justification, so a human can check the claim instead of trusting it.
    """

    path: str
    relation: str
    why: str = ""


class PulseItemOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    file_path: str
    # The repository the path is relative to: a run covers the documentation
    # repository and the inbox, and the client needs to know which tree to
    # open the document from. Absent for items written before runs scanned
    # the inbox; the client then opens it from the documentation repository.
    repository_id: int | None = None
    summary: str | None = None
    tags: list = Field(default_factory=list)
    connections: list = Field(default_factory=list)
    confidence: float | None = None
    decision: str  # pending | applied | skipped
    # Which halves are already in the document, so the client does not have to
    # keep its own copy of a decision the server has to know about anyway.
    applied_parts: list[Literal["tags", "connections"]] = Field(default_factory=list)


class PulseRunOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    workspace_id: int
    status: str
    summary: str | None = None
    mode: str  # suggest | apply
    created_at: dt.datetime
    items: list[PulseItemOut] = Field(default_factory=list)


class PulseRunRequest(BaseModel):
    """Trigger a Pulse scan. The mode is read from the workspace's Pulse
    settings (suggest by default, apply when the user opted in), so a client
    cannot pick apply-mode on its own."""

    repository_id: int | None = None


class PulseApplyRequest(BaseModel):
    item_ids: list[int] = Field(default_factory=list)


class PulsePartRequest(BaseModel):
    """Which halves of a suggestion to write.

    Tags and connections are separable because they are separable in judgement:
    the tags are a fair description of a document, while the connections are
    inferences that deserve their own review. An empty list means "both", so
    the default request shape is unchanged.
    """

    parts: list[Literal["tags", "connections"]] = Field(default_factory=list)


class PulseApplyOut(BaseModel):
    run_id: int
    applied: list[str] = Field(default_factory=list)
    skipped: list[dict[str, str]] = Field(default_factory=list)
    requires_manual_commit: bool = True


class PulseDecideOut(BaseModel):
    run_id: int
    item: PulseItemOut
    applied_path: str | None = None
    requires_manual_commit: bool = True


class PulseSettingsOut(BaseModel):
    """The workspace's Delphi Pulse configuration: mode and schedule."""

    model_config = ConfigDict(from_attributes=True)

    mode: str  # suggest | apply
    schedule_enabled: bool = False
    schedule_kind: str = "interval"  # interval | weekly
    interval_hours: int = 1
    weekly_day: int = 0
    weekly_hour: int = 0


class PulseSettingsUpdate(BaseModel):
    mode: str = Field(pattern="^(suggest|apply)$")
    schedule_enabled: bool = False
    schedule_kind: str = Field(default="interval", pattern="^(interval|weekly)$")
    interval_hours: int = Field(default=1, ge=1, le=24)
    weekly_day: int = Field(default=0, ge=0, le=6)
    weekly_hour: int = Field(default=0, ge=0, le=23)


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



# ---------------------------------------------------------------------------
# Semantic indexing and retrieval (embeddings + pgvector)
# ---------------------------------------------------------------------------


class IndexCountsOut(BaseModel):
    files_discovered: int = 0
    files_processed: int = 0
    code_units_indexed: int = 0
    documents_indexed: int = 0
    document_chunks_indexed: int = 0
    embeddings_generated: int = 0
    failed_files: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)


class IndexStatusOut(BaseModel):
    status: str
    message: str | None = None
    started_at: str | None = None
    finished_at: str | None = None
    counts: IndexCountsOut = Field(default_factory=IndexCountsOut)
    code_embedding_model: str
    document_embedding_model: str
    reindex_required: bool = False
    reindex_reasons: list[str] = Field(default_factory=list)


class SemanticSearchHitOut(BaseModel):
    kind: str
    repository_id: int
    repository: str
    file_path: str
    content: str
    score: float
    symbol: str | None = None
    node_type: str | None = None
    start_line: int | None = None
    end_line: int | None = None
    section: str | None = None
    source: str


class SemanticSearchOut(BaseModel):
    query: str
    mode: str
    repository_id: int | None = None
    lexical_hits: int = 0
    semantic_hits: int = 0
    hits: list[SemanticSearchHitOut] = Field(default_factory=list)


# --------------------------------------------------------------------------
# The inbox: documents dropped in from the desktop
# --------------------------------------------------------------------------


class InboxFileOut(BaseModel):
    """One document in the inbox."""

    #: Repository-relative, so the reading pane can open it directly.
    path: str
    name: str
    size: int


class InboxOut(BaseModel):
    """The inbox listing.

    ``repository_id`` is null until the first upload creates the storage
    repository, and that is the point of returning it at all: listing must not
    create one, because looking at a workspace is not a decision to keep
    documents in it, and the folder would otherwise appear for every workspace
    somebody merely opened. The null also tells the interface which reading pane
    to use -- there is nothing to open yet.
    """

    repository_id: int | None = None
    directory: str
    files: list[InboxFileOut] = Field(default_factory=list)


class WorkingDirIn(BaseModel):
    """The folder to work in, or an empty string to go back to the default."""

    path: str = Field(..., max_length=1000)


class AdoptFolderOut(BaseModel):
    """What happened when the reader asked for their folder to be made safe.

    ``committed`` is the number of files recorded, and it is the whole point of
    reading the response rather than just seeing it succeed: zero means there
    was nothing to record, which is a different outcome from a failure and worth
    telling apart.
    """

    ok: bool
    committed: int = 0
    message: str


class WorkingDirOut(BaseModel):
    """Where this workspace keeps its own documents, and what is in there."""

    #: Null when the reader has not chosen, and everything falls back to Apollo's
    #: own storage. Reported rather than hidden, because "Apollo made this folder
    #: up" and "you chose this one" are different answers.
    working_dir: str | None = None
    #: Whether the chosen folder is empty. Empty is the recommended state and this
    #: is how the interface can say so before the reader commits to it.
    empty: bool = True
    #: How many entries a non-empty folder holds, so the warning can be concrete
    #: rather than a shrug.
    entries: int = 0
    #: Set when the folder is not empty, saying what that means. A folder with
    #: somebody's documents in it is a legitimate choice -- they may want Apollo to
    #: work in a folder they already keep -- but it is a decision, not a default,
    #: and it deserves saying once, out loud.
    warning: str | None = None
    #: The folder a document dropped in right now would end up in, so the reader
    #: can see the consequence of the choice rather than infer it.
    inbox_dir: str
    #: Documents in the folder that Git does not track yet, and so cannot be
    #: moved. Non-zero means the engine will refuse every move here, which is
    #: exactly what the reader must be told before they try one -- the refusal
    #: names Git rather than this application, and reads as a bug in it.
    untracked: int = 0


class InboxUploadOut(BaseModel):
    """What became of one dropped file."""

    repository_id: int
    path: str
    name: str
    size: int
    #: Whether Apollo can read the file back. The bytes are stored either way: a
    #: false here is something the reader can act on, not a failed upload.
    readable: bool = True
    unreadable_reason: str | None = None


class ImportedFileOut(BaseModel):
    """One document copied out of a folder the reader offered."""

    #: Where it came from, in the reader's own terms, so a mistake in the import
    #: can be traced back to a file they recognise.
    source_path: str
    path: str
    name: str
    size: int
    readable: bool = True
    unreadable_reason: str | None = None


class RefusedFileOut(BaseModel):
    """One document that was not copied, and the reason.

    The reason is not decoration: a folder added as a source is read once, and a
    file that silently did not arrive is a document the reader believes Apollo has.
    """

    source_path: str
    reason: str


class InboxImportFolderOut(BaseModel):
    """What adding a folder as a source did."""

    repository_id: int
    #: The folder's name as it is known inside the inbox, which may not be the
    #: name on disk.
    folder_name: str
    #: Files considered, including the ones that are not documents.
    found: int
    copied: list[ImportedFileOut]
    refused: list[RefusedFileOut]
    #: True when the folder holds more documents than one import copies, so the
    #: collection above is only part of what is on disk.
    truncated: bool = False


class InboxImportFolderRequest(BaseModel):
    """Which folder to copy in.

    A path, not an upload: the folder is already on this machine and stays there.
    """

    path: str = Field(..., min_length=1, max_length=1000)


# --------------------------------------------------------------------------
# Delphi's findings
# --------------------------------------------------------------------------


class SignalOut(BaseModel):
    """One finding, as the reading pane shows it.

    ``label`` is the name the reader reads and ``kind`` the one stored, so the
    wording can be improved without a migration. ``why`` is never empty: a claim
    without its evidence is refused at the boundary and never reaches here.
    """

    id: int
    repository_id: int
    file_path: str
    kind: str
    label: str
    reference: str | None = None
    why: str
    confidence: float | None = None
    status: str
    created_at: dt.datetime


class GroupProposalOut(BaseModel):
    """What one proposed group actually did.

    ``placed`` and ``left_alone`` are both reported because the difference is the
    point: a document the reader dragged somewhere else is not a member, and a
    group that quietly lost one would read as a group of the size it shows.
    """

    group_id: int
    name: str
    placed: list[str] = Field(default_factory=list)
    left_alone: list[str] = Field(default_factory=list)
    unavailable: list[str] = Field(default_factory=list)


class AnalyseRequest(BaseModel):
    """Which collection to analyse.
    Omitted, the workspace's documentation is analysed, falling back to the inbox
    when there is no documentation. Named, it must be a documentation repository
    or the inbox: a source repository holds code, and judging whether code is out
    of date is not what this does.
    """

    repository_id: int | None = None


class OpenSignalsOut(BaseModel):
    """How many findings are still waiting for a decision.

    Its own endpoint because the listing deliberately refuses to answer for a
    whole workspace -- too much for a panel above one document -- and a badge
    still has to know the total. Reporting it as its own number rather than as
    the length of a listing is what keeps the badge true after a restart.
    """

    open_signals: int


class AnalyseOut(BaseModel):
    """What one pass found, in a sentence and in rows.

    ``analysed`` is how many documents were actually read, which is not
    necessarily ``documents``: a file that could not be read, or a batch that did
    not fit the window, is a document the reader was not told about, and a
    summary claiming full coverage would be a lie.
    """

    repository_id: int
    documents: int
    analysed: int
    signals: list[SignalOut]
    #: Findings still waiting for a decision, across the whole workspace.
    open_signals: int
    #: The groups Delphi proposed from those findings, and what became of them.
    groups: list[GroupProposalOut] = Field(default_factory=list)
    summary: str
    errors: list[str] = Field(default_factory=list)
