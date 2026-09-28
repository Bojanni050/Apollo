"""Delphi's information signals, in a table of their own.

``doc_signals`` holds one row per claim about a document's *standing* -- that it
has been superseded, that it duplicates another, that it disagrees with one.
These are suggestions a person reviews, and three properties follow from that:

* ``why`` is NOT NULL. A claim without its evidence is an assertion, and an
  assertion in a sidebar is the one thing a signal must never be.
* ``status`` exists so the reader's decision survives: a signal the reader
  dismissed must not come back the next time the same analysis runs. Nothing
  else writes that column.
* ``status`` is a CHECK, unlike ``pulse_items.signals`` before it. There are
  three statuses and they are all this table's own, so the database can enforce
  them; the older column is a JSON list whose members a CHECK cannot inspect.

Nothing here deletes a document, and nothing in the application does either on
the strength of a signal. There is no migration on this table: the findings are
app state that the analysis produces and the reader clears, not a record of
anything that happened to their files.

Revision ID: 0011_doc_signals
Revises: 0010_repository_is_storage
Create Date: 2026-09-28
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "0011_doc_signals"
down_revision = "0010_repository_is_storage"
branch_labels = None
depends_on = None

_JSON = sa.text("'{}'")
_BOOL = sa.text("false")


def upgrade() -> None:
    op.create_table(
        "doc_signals",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("workspace_id", sa.Integer(), nullable=False),
        sa.Column("repository_id", sa.Integer(), nullable=False),
        sa.Column("file_path", sa.String(length=1000), nullable=False),
        sa.Column("kind", sa.String(length=20), nullable=False),
        # NULL rather than "" for the one signal that names no document, so
        # "this says something the corpus does not" is stored as the absence of
        # a reference instead of as a reference to nothing.
        sa.Column("reference", sa.String(length=1000), nullable=True),
        sa.Column("why", sa.Text(), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="new"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"], ["workspaces.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["repository_id"], ["repositories.id"], ondelete="CASCADE"
        ),
        sa.CheckConstraint(
            "kind IN ('outdated', 'duplicate', 'new', 'update', 'conflict')",
            name="ck_doc_signals_kind",
        ),
        sa.CheckConstraint(
            "status IN ('new', 'confirmed', 'dismissed')",
            name="ck_doc_signals_status",
        ),
    )
    # The lookup behind "which signals does this document have?", run on every
    # selection in the reading pane.
    op.create_index(
        "ix_doc_signals_document",
        "doc_signals",
        ["workspace_id", "repository_id", "file_path"],
    )
    # The open-signal count per workspace, shown as a badge in the navigation.
    op.create_index("ix_doc_signals_open", "doc_signals", ["workspace_id", "status"])
    op.create_index("ix_doc_signals_workspace_id", "doc_signals", ["workspace_id"])
    op.create_index(
        "ix_doc_signals_repository_id", "doc_signals", ["repository_id"]
    )


def downgrade() -> None:
    # Drops the claims, not a single document. The files they described are
    # untouched; only what Apollo thought about them is lost, and a re-analysis
    # rebuilds it.
    op.drop_index("ix_doc_signals_repository_id", table_name="doc_signals")
    op.drop_index("ix_doc_signals_workspace_id", table_name="doc_signals")
    op.drop_index("ix_doc_signals_open", table_name="doc_signals")
    op.drop_index("ix_doc_signals_document", table_name="doc_signals")
    op.drop_table("doc_signals")
