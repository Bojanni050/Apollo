"""SQLAlchemy models: application state only.

Document *content* is never stored here. Markdown files in the documentation
repository remain the single source of truth; these tables hold workspace
configuration, conversations, questions, decisions and analysis results.
"""
from __future__ import annotations

import datetime as dt

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    JSON,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


def utcnow() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


class TimestampMixin:
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False
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


class Repository(TimestampMixin, Base):
    """A local Git repository registered with a workspace.

    ``local_path`` points at an existing local checkout; the app never clones on
    the user's behalf. ``writable`` is only ever true for the documentation
    repository.
    """

    __tablename__ = "repositories"
    __table_args__ = (UniqueConstraint("workspace_id", "name", name="uq_repo_ws_name"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    workspace_id: Mapped[int] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    local_path: Mapped[str] = mapped_column(String(1000), nullable=False)
    branch: Mapped[str] = mapped_column(String(200), default="main", nullable=False)
    kind: Mapped[str] = mapped_column(String(20), default="source", nullable=False)
    writable: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    description: Mapped[str | None] = mapped_column(Text)

    workspace: Mapped[Workspace] = relationship(back_populates="repositories")

    @property
    def is_documentation(self) -> bool:
        return self.kind == "documentation"


class Conversation(TimestampMixin, Base):
    __tablename__ = "conversations"

    id: Mapped[int] = mapped_column(primary_key=True)
    workspace_id: Mapped[int] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), index=True
    )
    title: Mapped[str] = mapped_column(String(300), default="New conversation")
    # explore | investigate | apply -- switching modes never discards messages.
    mode: Mapped[str] = mapped_column(String(20), default="explore", nullable=False)
    question_id: Mapped[int | None] = mapped_column(
        ForeignKey("open_questions.id", ondelete="SET NULL")
    )
    archived: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    workspace: Mapped[Workspace] = relationship(back_populates="conversations")
    messages: Mapped[list[Message]] = relationship(
        back_populates="conversation",
        cascade="all, delete-orphan",
        order_by="Message.id",
    )


class Message(Base):
    __tablename__ = "messages"

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
    citations: Mapped[list | None] = mapped_column(JSON, default=list)
    tool_calls: Mapped[list | None] = mapped_column(JSON, default=list)
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False, index=True
    )

    conversation: Mapped[Conversation] = relationship(back_populates="messages")



class OpenQuestion(TimestampMixin, Base):
    __tablename__ = "open_questions"

    id: Mapped[int] = mapped_column(primary_key=True)
    workspace_id: Mapped[int] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), index=True
    )
    uid: Mapped[str] = mapped_column(String(40), nullable=False, unique=True)
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    description: Mapped[str] = mapped_column(Text, default="", nullable=False)
    evidence: Mapped[list | None] = mapped_column(JSON, default=list)
    affected: Mapped[list | None] = mapped_column(JSON, default=list)
    # open | answered | resolved
    status: Mapped[str] = mapped_column(String(20), default="open", nullable=False)
    source: Mapped[str] = mapped_column(String(20), default="manual", nullable=False)
    conversation_id: Mapped[int | None] = mapped_column(
        ForeignKey("conversations.id", ondelete="SET NULL")
    )
    resolution: Mapped[str | None] = mapped_column(Text)
    resolved_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))


class Decision(TimestampMixin, Base):
    """A proposed or approved architectural decision.

    Approval is always an explicit human action; only then is the decision
    rendered to Markdown in the documentation repository.
    """

    __tablename__ = "decisions"

    id: Mapped[int] = mapped_column(primary_key=True)
    workspace_id: Mapped[int] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), index=True
    )
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    context: Mapped[str] = mapped_column(Text, default="", nullable=False)
    decision: Mapped[str] = mapped_column(Text, default="", nullable=False)
    rationale: Mapped[str] = mapped_column(Text, default="", nullable=False)
    consequences: Mapped[str] = mapped_column(Text, default="", nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="proposed", nullable=False)
    decided_on: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    approved_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    markdown_path: Mapped[str | None] = mapped_column(String(1000))
    related_documents: Mapped[list | None] = mapped_column(JSON, default=list)
    related_questions: Mapped[list | None] = mapped_column(JSON, default=list)


class ChangeProposal(TimestampMixin, Base):
    """A set of proposed documentation modifications awaiting approval.

    Nothing here is written to disk until the user accepts the proposal.
    """

    __tablename__ = "change_proposals"

    id: Mapped[int] = mapped_column(primary_key=True)
    workspace_id: Mapped[int] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), index=True
    )
    conversation_id: Mapped[int | None] = mapped_column(
        ForeignKey("conversations.id", ondelete="SET NULL")
    )
    kind: Mapped[str] = mapped_column(String(30), default="edit")  # edit | move | create
    title: Mapped[str] = mapped_column(String(300), default="", nullable=False)
    reason: Mapped[str] = mapped_column(Text, default="", nullable=False)
    evidence: Mapped[list | None] = mapped_column(JSON, default=list)
    changes: Mapped[list | None] = mapped_column(JSON, default=list)
    expected_consequences: Mapped[str] = mapped_column(Text, default="", nullable=False)
    diff: Mapped[str | None] = mapped_column(Text)
    # pending | accepted | rejected
    status: Mapped[str] = mapped_column(String(20), default="pending", nullable=False)
    decided_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))


class InventoryRun(TimestampMixin, Base):
    __tablename__ = "inventory_runs"

    id: Mapped[int] = mapped_column(primary_key=True)
    workspace_id: Mapped[int] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), index=True
    )
    status: Mapped[str] = mapped_column(String(20), default="pending", nullable=False)
    summary: Mapped[str | None] = mapped_column(Text)
    applied_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))

    items: Mapped[list[InventoryItem]] = relationship(
        back_populates="run", cascade="all, delete-orphan", order_by="InventoryItem.id"
    )


class InventoryItem(Base):
    """One document's classification, derived from its *contents*."""

    __tablename__ = "inventory_items"

    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[int] = mapped_column(
        ForeignKey("inventory_runs.id", ondelete="CASCADE"), index=True
    )
    source_path: Mapped[str] = mapped_column(String(1000), nullable=False)
    purpose: Mapped[str] = mapped_column(Text, default="", nullable=False)
    suggested_path: Mapped[str | None] = mapped_column(String(1000))
    confidence: Mapped[float | None] = mapped_column(Float)
    overlaps: Mapped[list | None] = mapped_column(JSON, default=list)
    ambiguous: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    note: Mapped[str | None] = mapped_column(Text)
    # pending | applied | skipped
    decision: Mapped[str] = mapped_column(String(20), default="pending", nullable=False)

    run: Mapped[InventoryRun] = relationship(back_populates="items")
