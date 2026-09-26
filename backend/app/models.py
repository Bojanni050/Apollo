"""SQLAlchemy models: application state only.

Document *content* is never stored here. Markdown files in the documentation
repository remain the single source of truth; these tables hold workspace
configuration, conversations, questions, decisions and analysis results.

PostgreSQL notes
----------------
This module is the single source of truth for the schema; the Alembic
migrations mirror it. Types are chosen so one set of models serves both
databases without pretending they behave identically:

* **JSON vs JSONB.** :data:`JSONType` renders as ``JSONB`` on PostgreSQL and
  ``JSON`` on SQLite. JSONB stores a decomposed binary form, which is smaller,
  validates its input, and -- unlike ``JSON`` -- can be indexed. Citations and
  tool calls are queried by content, so that difference is worth having.
* **UUID.** :class:`OpenQuestion.uid` is a real ``UUID`` column, not a string
  that happens to look like one: native 16 bytes on PostgreSQL, ``CHAR(32)``
  on SQLite.
* **Timestamps** are ``TIMESTAMP WITH TIME ZONE`` on PostgreSQL.
* **CHECK constraints** on the enum-like columns, so an invalid status is
  rejected by the database and not only by application code.

The SQLite caveat worth knowing: SQLAlchemy's SQLite dialect stores
``DateTime(timezone=True)`` as a *string* and returns **naive** datetimes on
read, discarding the offset. The same column on PostgreSQL round-trips as a
true ``timestamptz``. Code must not depend on a tz-aware value coming back on
SQLite -- which is why the timestamp assertions are PostgreSQL-marked.
"""
from __future__ import annotations

import datetime as dt
import uuid

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Index,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base

#: Structured columns. JSONB on PostgreSQL (indexable, validated, compact),
#: plain JSON on SQLite, which has no JSONB equivalent.
JSONType = JSON().with_variant(JSONB(), "postgresql")

#: Embedding vectors. On PostgreSQL this is pgvector's ``vector`` type (the
#: extension itself is enabled by migration 0005). The column is deliberately
#: untyped -- ``vector`` without a fixed dimension -- because the dimension is
#: a property of the *configured embedding model*, not of the schema: Jina
#: code embeddings are 768-d, BGE-M3 document embeddings are 1024-d, and a
#: model change must not require a column rewrite. Each row carries its own
#: ``embedding_model`` and ``embedding_dimension`` so searches never compare
#: vectors from different models, and the HNSW index (see migration 0005) is
#: an expression index casting to the configured model's dimension. On
#: SQLite, where pgvector does not exist, vectors fall back to JSON: local
#: development and the unit suite get exact (non-indexed) similarity search
#: instead of approximate indexed search -- same semantics, no fake indexes.
try:
    from pgvector.sqlalchemy import Vector

    EmbeddingType = JSON().with_variant(Vector(), "postgresql")
except ImportError:  # pragma: no cover - pgvector is a declared dependency
    EmbeddingType = JSON()

#: Allowed values for the small enum-like columns. Kept here so the CHECK
#: constraints, the schemas and the documentation cannot drift apart.
REPOSITORY_KINDS = ("documentation", "source")
SOURCE_TYPES = ("local", "github")
SOURCE_STATUSES = ("pending", "ready", "error", "missing")
CHUNK_KINDS = ("code", "document")
MESSAGE_ROLES = ("user", "assistant", "system")
CONVERSATION_MODES = ("explore", "investigate", "apply")
PROPOSAL_STATUSES = ("pending", "accepted", "rejected")
INVENTORY_RUN_STATUSES = ("pending", "running", "completed", "failed")
INVENTORY_DECISIONS = ("pending", "applied", "skipped")
QUESTION_STATUSES = ("open", "answered", "resolved")
DECISION_STATUSES = ("proposed", "approved", "rejected", "superseded")


def utcnow() -> dt.datetime:
    """An aware UTC timestamp, used as the Python-side default.

    Aware rather than naive because the value may be compared with one read
    back from another host; a naive datetime is ambiguous.
    """
    return dt.datetime.now(dt.timezone.utc)


def _check(column: str, allowed: tuple[str, ...], name: str) -> CheckConstraint:
    """A CHECK constraint restricting ``column`` to a fixed set of values.

    The database is the last line of defence: application validation can be
    bypassed by a hand-run migration, a direct psql session, or a future code
    path -- and an invalid status stored today is unreadable data tomorrow.
    """
    values = ", ".join(f"'{v}'" for v in allowed)
    return CheckConstraint(f"{column} IN ({values})", name=name)


class TimestampMixin:
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True),
        default=utcnow,
        server_default=text("CURRENT_TIMESTAMP"),
        nullable=False,
    )
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True),
        default=utcnow,
        onupdate=utcnow,
        server_default=text("CURRENT_TIMESTAMP"),
        nullable=False,
    )


class Workspace(TimestampMixin, Base):
    __tablename__ = "workspaces"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)

    repositories: Mapped[list[Repository]] = relationship(
        back_populates="workspace", cascade="all, delete-orphan"
    )
    conversations: Mapped[list[Conversation]] = relationship(
        back_populates="workspace", cascade="all, delete-orphan"
    )
    # Added so that deleting a workspace cascades cleanly at the database
    # level. Previously these were only cascaded in the ORM, which means a
    # row inserted by a migration or a psql session would leave orphans.
    proposals: Mapped[list[ChangeProposal]] = relationship(
        back_populates="workspace", cascade="all, delete-orphan"
    )
    questions: Mapped[list[OpenQuestion]] = relationship(
        back_populates="workspace", cascade="all, delete-orphan"
    )
    decisions: Mapped[list[Decision]] = relationship(
        back_populates="workspace", cascade="all, delete-orphan"
    )
    inventory_runs: Mapped[list[InventoryRun]] = relationship(
        back_populates="workspace", cascade="all, delete-orphan"
    )
    pulse_runs: Mapped[list[PulseRun]] = relationship(
        back_populates="workspace", cascade="all, delete-orphan"
    )


class Repository(TimestampMixin, Base):
    """A repository registered with a workspace: documentation or source.

    ``kind='documentation'`` is the single Markdown repository (writable via
    the approval-gated proposal flow). ``kind='source'`` is a code repository
    used as architecture evidence, always read-only.

    Source repositories come in two flavours, unified by ``source_type`` and
    the services/sources.py abstraction:

    * ``local`` -- an existing local Git checkout the app never clones or
      copies (``local_path`` is authoritative);
    * ``github`` -- a remote repository cloned into the managed checkout
      directory on first sync (``source_url`` is the stable identity;
      ``local_path`` points at the machine-specific checkout).

    Nothing here stores credentials: GitHub sources are read-oriented and
    synchronize without authentication.
    """

    __tablename__ = "repositories"
    __table_args__ = (
        UniqueConstraint("workspace_id", "name", name="uq_repo_ws_name"),
        _check("kind", REPOSITORY_KINDS, "ck_repositories_kind"),
        _check("source_type", SOURCE_TYPES, "ck_repositories_source_type"),
        _check("status", SOURCE_STATUSES, "ck_repositories_status"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    workspace_id: Mapped[int] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    local_path: Mapped[str] = mapped_column(String(1000), nullable=False)
    branch: Mapped[str] = mapped_column(
        String(200), default="main", server_default="main", nullable=False
    )
    kind: Mapped[str] = mapped_column(
        String(20), default="source", server_default="source", nullable=False
    )
    writable: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default=text("false"), nullable=False
    )
    description: Mapped[str | None] = mapped_column(Text)

    # ---- Source-repository metadata (nullable: documentation rows have none) --
    # ``source_type`` distinguishes the RepositorySource specializations
    # (local Git repository vs GitHub repository).
    source_type: Mapped[str | None] = mapped_column(
        String(20), default=None, server_default=None, nullable=True
    )
    # The remote identity for GitHub sources (normalized URL). For local
    # sources the resolved local_path is the identity and this stays NULL.
    source_url: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    # Current status: pending (not yet synchronized), ready, error, missing.
    status: Mapped[str] = mapped_column(
        String(20), default="ready", server_default="ready", nullable=False
    )
    status_message: Mapped[str | None] = mapped_column(Text)
    last_synced_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))

    workspace: Mapped[Workspace] = relationship(back_populates="repositories")

    @property
    def is_documentation(self) -> bool:
        return self.kind == "documentation"


class Conversation(TimestampMixin, Base):
    __tablename__ = "conversations"
    __table_args__ = (
        _check("mode", CONVERSATION_MODES, "ck_conversations_mode"),
        # The sidebar lists a workspace's conversations newest-first; without
        # this, every listing was a sort over the whole table.
        Index("ix_conversations_workspace_updated", "workspace_id", "updated_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    workspace_id: Mapped[int] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), index=True
    )
    # Previously nullable despite having a default, so a row inserted without
    # a title stored NULL. Now enforced, with a server default for raw inserts.
    title: Mapped[str] = mapped_column(
        String(300),
        default="New conversation",
        server_default="New conversation",
        nullable=False,
    )
    # explore | investigate | apply -- switching modes never discards messages.
    mode: Mapped[str] = mapped_column(
        String(20), default="explore", server_default="explore", nullable=False
    )
    question_id: Mapped[int | None] = mapped_column(
        # use_alter tells SQLAlchemy this constraint is added after the tables
        # exist. conversations and open_questions reference each other, and
        # without this, metadata.create_all() cannot order the two tables and
        # raises CircularDependencyError. The migration applies the same
        # constraint by ALTER TABLE (see migrations/versions/0001_initial.py).
        ForeignKey(
            "open_questions.id",
            ondelete="SET NULL",
            name="fk_conversations_question_id_open_questions",
            use_alter=True,
        )
    )
    archived: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default=text("false"), nullable=False
    )

    workspace: Mapped[Workspace] = relationship(back_populates="conversations")
    messages: Mapped[list[Message]] = relationship(
        back_populates="conversation",
        cascade="all, delete-orphan",
        order_by="Message.id",
    )


class Message(Base):
    __tablename__ = "messages"
    __table_args__ = (
        _check("role", MESSAGE_ROLES, "ck_messages_role"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    conversation_id: Mapped[int] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE"), index=True
    )
    role: Mapped[str] = mapped_column(String(20), nullable=False)  # user|assistant|system
    content: Mapped[str] = mapped_column(Text, nullable=False)
    mode: Mapped[str | None] = mapped_column(String(20))
    # Structured references: [{repository, path, start_line, end_line, revision,
    # evidence_type}] with evidence_type in verified_implementation /
    # explicit_decision / documented_intention / ai_interpretation / uncertainty.
    citations: Mapped[list | None] = mapped_column(JSONType, default=list)
    tool_calls: Mapped[list | None] = mapped_column(JSONType, default=list)
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow,
        server_default=text("CURRENT_TIMESTAMP"), nullable=False, index=True,
    )

    conversation: Mapped[Conversation] = relationship(back_populates="messages")


class OpenQuestion(TimestampMixin, Base):
    __tablename__ = "open_questions"
    __table_args__ = (
        _check("status", QUESTION_STATUSES, "ck_open_questions_status"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    workspace_id: Mapped[int] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), index=True
    )
    # A real UUID rather than a VARCHAR that happens to hold one: stored
    # natively as 16 bytes on PostgreSQL, and generated in Python so two rows
    # cannot collide under concurrent inserts. Declared as a *unique index*
    # (not a table constraint) so the model and the migration name it
    # identically -- otherwise reflection and migration disagree.
    uid: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), default=uuid.uuid4, index=True, unique=True, nullable=False
    )
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    description: Mapped[str] = mapped_column(
        Text, default="", server_default="", nullable=False
    )
    evidence: Mapped[list | None] = mapped_column(JSONType, default=list)
    affected: Mapped[list | None] = mapped_column(JSONType, default=list)
    # open | answered | resolved
    status: Mapped[str] = mapped_column(
        String(20), default="open", server_default="open", nullable=False
    )
    source: Mapped[str] = mapped_column(
        String(20), default="manual", server_default="manual", nullable=False
    )
    conversation_id: Mapped[int | None] = mapped_column(
        ForeignKey("conversations.id", ondelete="SET NULL")
    )
    resolution: Mapped[str | None] = mapped_column(Text)
    resolved_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))

    workspace: Mapped[Workspace] = relationship(back_populates="questions")


class Decision(TimestampMixin, Base):
    """A proposed or approved architectural decision.

    Approval is always an explicit human action; only then is the decision
    rendered to Markdown in the documentation repository.
    """

    __tablename__ = "decisions"
    __table_args__ = (
        _check("status", DECISION_STATUSES, "ck_decisions_status"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    workspace_id: Mapped[int] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), index=True
    )
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    context: Mapped[str] = mapped_column(Text, default="", server_default="", nullable=False)
    decision: Mapped[str] = mapped_column(Text, default="", server_default="", nullable=False)
    rationale: Mapped[str] = mapped_column(Text, default="", server_default="", nullable=False)
    consequences: Mapped[str] = mapped_column(Text, default="", server_default="", nullable=False)
    status: Mapped[str] = mapped_column(
        String(20), default="proposed", server_default="proposed", nullable=False
    )
    decided_on: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    approved_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    markdown_path: Mapped[str | None] = mapped_column(String(1000))
    related_documents: Mapped[list | None] = mapped_column(JSONType, default=list)
    related_questions: Mapped[list | None] = mapped_column(JSONType, default=list)
    superseded_by_id: Mapped[int | None] = mapped_column(
        ForeignKey("decisions.id", ondelete="SET NULL"), nullable=True, index=True
    )

    workspace: Mapped[Workspace] = relationship(back_populates="decisions")
    superseded_by: Mapped[Decision | None] = relationship(
        "Decision",
        remote_side="Decision.id",
        foreign_keys=[superseded_by_id],
        back_populates="supersedes",
    )
    supersedes: Mapped[list[Decision]] = relationship(
        "Decision",
        foreign_keys=[superseded_by_id],
        back_populates="superseded_by",
    )

    @property
    def supersedes_ids(self) -> list[int]:
        return [d.id for d in self.supersedes] if self.supersedes else []



class ChangeProposal(TimestampMixin, Base):
    """A set of proposed documentation modifications awaiting approval.

    Nothing here is written to disk until the user accepts the proposal.
    """

    __tablename__ = "change_proposals"
    __table_args__ = (
        _check("status", PROPOSAL_STATUSES, "ck_change_proposals_status"),
        # conversation_id was the only foreign key with no index, yet proposals
        # are always fetched for one conversation.
        Index("ix_change_proposals_conversation", "conversation_id"),
        # The context panel lists a workspace's pending proposals; without this
        # that listing filtered and sorted the entire table.
        Index("ix_change_proposals_workspace_status", "workspace_id", "status"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    workspace_id: Mapped[int] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), index=True
    )
    conversation_id: Mapped[int | None] = mapped_column(
        ForeignKey("conversations.id", ondelete="SET NULL")
    )
    # Previously nullable despite having a default; now enforced.
    kind: Mapped[str] = mapped_column(
        String(30), default="edit", server_default="edit", nullable=False
    )  # edit | move | create
    title: Mapped[str] = mapped_column(
        String(300), default="", server_default="", nullable=False
    )
    reason: Mapped[str] = mapped_column(Text, default="", server_default="", nullable=False)
    evidence: Mapped[list | None] = mapped_column(JSONType, default=list)
    changes: Mapped[list | None] = mapped_column(JSONType, default=list)
    expected_consequences: Mapped[str] = mapped_column(
        Text, default="", server_default="", nullable=False
    )
    diff: Mapped[str | None] = mapped_column(Text)
    # pending | accepted | rejected
    status: Mapped[str] = mapped_column(
        String(20), default="pending", server_default="pending", nullable=False
    )
    decided_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))

    workspace: Mapped[Workspace] = relationship(back_populates="proposals")


class InventoryRun(TimestampMixin, Base):
    __tablename__ = "inventory_runs"
    __table_args__ = (
        _check("status", INVENTORY_RUN_STATUSES, "ck_inventory_runs_status"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    workspace_id: Mapped[int] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), index=True
    )
    status: Mapped[str] = mapped_column(
        String(20), default="pending", server_default="pending", nullable=False
    )
    summary: Mapped[str | None] = mapped_column(Text)
    applied_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))

    workspace: Mapped[Workspace] = relationship(back_populates="inventory_runs")
    items: Mapped[list[InventoryItem]] = relationship(
        back_populates="run", cascade="all, delete-orphan", order_by="InventoryItem.id"
    )


class InventoryItem(Base):
    """One document's classification, derived from its *contents*."""

    __tablename__ = "inventory_items"
    __table_args__ = (
        _check("decision", INVENTORY_DECISIONS, "ck_inventory_items_decision"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[int] = mapped_column(
        ForeignKey("inventory_runs.id", ondelete="CASCADE"), index=True
    )
    source_path: Mapped[str] = mapped_column(String(1000), nullable=False)
    purpose: Mapped[str] = mapped_column(Text, default="", server_default="", nullable=False)
    suggested_path: Mapped[str | None] = mapped_column(String(1000))
    confidence: Mapped[float | None] = mapped_column(Float)
    overlaps: Mapped[list | None] = mapped_column(JSONType, default=list)
    ambiguous: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default=text("false"), nullable=False
    )
    note: Mapped[str | None] = mapped_column(Text)
    # Other categories the model seriously weighed. A classification the user
    # can see was contested is one they can review; one presented as settled
    # is not.
    alternatives: Mapped[list | None] = mapped_column(JSONType, default=list)
    # The model's stated evidence for the decision.
    reason: Mapped[str | None] = mapped_column(Text)
    # True when the classification rests on a structural digest of the document
    # rather than the whole document, so a lower confidence is explained.
    partial: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default=text("false"), nullable=False
    )
    # pending | applied | skipped
    decision: Mapped[str] = mapped_column(
        String(20), default="pending", server_default="pending", nullable=False
    )

    run: Mapped[InventoryRun] = relationship(back_populates="items")



#: Pulse modes: "suggest" proposes connections/tags for approval, "apply"
#: writes them immediately. Stored per workspace so different projects can
#: choose different levels of automation.
PULSE_MODES = ("suggest", "apply")

PULSE_RUN_STATUSES = ("pending", "completed", "failed")
PULSE_ITEM_DECISIONS = ("pending", "applied", "skipped")


class PulseRun(TimestampMixin, Base):
    """One AI Pulse scan over the documentation repository.

    A run walks documents that changed since the previous run (or all
    documents on the first run), asks the background model for thematic
    tags and for connections to other documents, and records the outcome
    per document as items -- suggestions to approve, or already-applied
    connections when the workspace is in "apply" mode.
    """

    __tablename__ = "pulse_runs"
    __table_args__ = (
        _check("status", PULSE_RUN_STATUSES, "ck_pulse_runs_status"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    workspace_id: Mapped[int] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), index=True
    )
    status: Mapped[str] = mapped_column(
        String(20), default="pending", server_default="pending", nullable=False
    )
    summary: Mapped[str | None] = mapped_column(Text)
    # The commit/file state the run started from, so the next run can scan
    # only what changed. Kept as JSON rather than joined to git metadata so
    # the run remains meaningful for non-git folders.
    scanned_state: Mapped[dict | None] = mapped_column(JSONType, default=dict)
    # "suggest" or "apply" -- the mode at the time the run ran.
    mode: Mapped[str] = mapped_column(
        String(20), default="suggest", server_default="suggest", nullable=False
    )

    workspace: Mapped[Workspace] = relationship(back_populates="pulse_runs")
    items: Mapped[list[PulseItem]] = relationship(
        back_populates="run", cascade="all, delete-orphan", order_by="PulseItem.id"
    )


class PulseItem(Base):
    """One document's outcome in a Pulse run: tags and connections found."""

    __tablename__ = "pulse_items"
    __table_args__ = (
        _check("decision", PULSE_ITEM_DECISIONS, "ck_pulse_items_decision"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[int] = mapped_column(
        ForeignKey("pulse_runs.id", ondelete="CASCADE"), index=True
    )
    # Path relative to the documentation repository root, as the user sees it.
    file_path: Mapped[str] = mapped_column(String(1000), nullable=False)
    # Short description of what the document is about, per the model.
    summary: Mapped[str | None] = mapped_column(Text)
    # Thematic tags proposed for the document.
    tags: Mapped[list | None] = mapped_column(JSONType, default=list)
    # Connections to other documents in the same repository:
    # [{"path": "...", "relation": "relates-to|supports|contradicts|extends",
    #   "why": "..."}]
    connections: Mapped[list | None] = mapped_column(JSONType, default=list)
    confidence: Mapped[float | None] = mapped_column(Float)
    # pending | applied | skipped
    decision: Mapped[str] = mapped_column(
        String(20), default="pending", server_default="pending", nullable=False
    )

    run: Mapped[PulseRun] = relationship(back_populates="items")


class WorkspacePulseSettings(TimestampMixin, Base):
    """Per-workspace Pulse configuration: suggest or auto-apply."""

    __tablename__ = "workspace_pulse_settings"

    workspace_id: Mapped[int] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), primary_key=True
    )
    # "suggest" or "apply"
    mode: Mapped[str] = mapped_column(
        String(20), default="suggest", server_default="suggest", nullable=False
    )
    # Whether connections should also link *back* from the target document
    # when applied. Simple and predictable for now: always true.
    workspace: Mapped[Workspace] = relationship()


class CodeChunk(TimestampMixin, Base):
    """One complete Tree-sitter node extracted from a source repository.

    A row is the unit of code evidence: the exact source of a function or class
    (``content``), where it lives (``repository_id`` + ``file_path``), what it
    is (``node_type`` + ``symbol`` + ``signature``), its exact line boundaries,
    and its embedding under the model recorded in ``embedding_model``.

    ``identifier`` is deterministic (same repository, file, node and content
    hash -> same identifier), which is what makes indexing idempotent: repeated
    indexing of unchanged content upserts the same row instead of duplicating
    it. ``enriched_content`` is the representation that was embedded -- source
    enriched with repository/file/symbol context -- kept separate so the
    verbatim source is never modified.
    """

    __tablename__ = "code_chunks"
    __table_args__ = (
        UniqueConstraint("repository_id", "identifier", name="uq_code_chunks_identifier"),
        Index("ix_code_chunks_repository_file", "repository_id", "file_path"),
        Index("ix_code_chunks_content_hash", "repository_id", "content_hash"),
        Index(
            "ix_code_chunks_model",
            "repository_id",
            "embedding_model",
            "embedding_dimension",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    repository_id: Mapped[int] = mapped_column(
        ForeignKey("repositories.id", ondelete="CASCADE"), nullable=False, index=True
    )
    repository_name: Mapped[str] = mapped_column(String(200), nullable=False)
    file_path: Mapped[str] = mapped_column(String(1000), nullable=False)
    language: Mapped[str] = mapped_column(String(50), nullable=False)
    node_type: Mapped[str] = mapped_column(String(100), nullable=False)
    symbol: Mapped[str] = mapped_column(String(300), nullable=False)
    signature: Mapped[str] = mapped_column(Text, default="", server_default="", nullable=False)
    start_line: Mapped[int] = mapped_column(nullable=False)
    end_line: Mapped[int] = mapped_column(nullable=False)
    #: The exact, unmodified source of the node.
    content: Mapped[str] = mapped_column(Text, nullable=False)
    #: Repository/file/symbol context + source: what the embedder received.
    enriched_content: Mapped[str] = mapped_column(Text, nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    #: Deterministic per-unit key (see services/parsing.py).
    identifier: Mapped[str] = mapped_column(String(1200), nullable=False)
    embedding_model: Mapped[str] = mapped_column(String(200), nullable=False)
    embedding_dimension: Mapped[int] = mapped_column(nullable=False)
    embedding: Mapped[list | None] = mapped_column(EmbeddingType, nullable=True)

    #: The owning repository; chunk queries join on this to scope a workspace.
    repository: Mapped[Repository] = relationship()


class DocumentChunk(TimestampMixin, Base):
    """One section-aligned chunk of a documentation file.

    Chunks follow the document's own structure -- a Markdown heading opens a
    new section -- rather than arbitrary character cuts, so retrieved evidence
    is a meaningful fragment of the documentation (a section of an ADR, one
    component's description) and a citation can name the section honestly.

    Document *content* remains on disk in the documentation repository; this
    row is the indexing artifact for semantic retrieval, not a copy of the
    document system.
    """

    __tablename__ = "document_chunks"
    __table_args__ = (
        UniqueConstraint("repository_id", "identifier", name="uq_document_chunks_identifier"),
        Index("ix_document_chunks_repository_file", "repository_id", "file_path"),
        Index("ix_document_chunks_content_hash", "repository_id", "content_hash"),
        Index(
            "ix_document_chunks_model",
            "repository_id",
            "embedding_model",
            "embedding_dimension",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    repository_id: Mapped[int] = mapped_column(
        ForeignKey("repositories.id", ondelete="CASCADE"), nullable=False, index=True
    )
    document_id: Mapped[str] = mapped_column(String(1200), nullable=False)
    file_path: Mapped[str] = mapped_column(String(1000), nullable=False)
    #: The heading this chunk sits under; the file's own structure.
    section: Mapped[str] = mapped_column(String(300), default="", server_default="", nullable=False)
    start_line: Mapped[int] = mapped_column(nullable=False)
    end_line: Mapped[int] = mapped_column(nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    #: Deterministic per-chunk key (see services/indexing.py).
    identifier: Mapped[str] = mapped_column(String(1200), nullable=False)
    embedding_model: Mapped[str] = mapped_column(String(200), nullable=False)
    embedding_dimension: Mapped[int] = mapped_column(nullable=False)
    embedding: Mapped[list | None] = mapped_column(EmbeddingType, nullable=True)

    #: The owning repository; chunk queries join on this to scope a workspace.
    repository: Mapped[Repository] = relationship()
