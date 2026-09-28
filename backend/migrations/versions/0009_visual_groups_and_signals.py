"""Visual groups, the archive flag, and Delphi's information signals.

Two independent additions, both answering the same product question -- "what
belongs with what, and what is still current?" -- at different layers.

**Visual grouping.** ``document_groups`` and ``group_placements`` hold the
arrangement. A group is a view, not a folder: it points at documents by
``(repository_id, file_path)`` and moving a document between groups writes a row
and touches no file. That is what lets the arrangement be edited constantly
while the folders on disk stay stable and reviewable in Git.

**Signals.** ``pulse_items`` gains the signals Delphi raises about a document's
standing (``outdated``, ``duplicate``, ``new``, ``update``, ``conflict``), the
documents those signals point at, and the grouping it can propose.

Archiving is deliberately NOT represented here. A document is archived by being
placed in the archive group, which is a row in ``group_placements`` like any
other placement -- one answer to "is this archived?", reversible by dragging, and
never a deletion. An ``archived`` flag on ``pulse_items`` was considered and
dropped: it would be a second answer to the same question, and the two could
disagree after a drag.

Existing rows get empty lists, which read as "no signals yet" -- the state every
existing row is already in.

Revision ID: 0009_visual_groups_and_signals
Revises: 0008_pulse_applied_parts
Create Date: 2026-10-12
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "0009_visual_groups_and_signals"
down_revision = "0008_pulse_applied_parts"
branch_labels = None
depends_on = None

_JSON_LIST = sa.text("'[]'")
_JSON_MAP = sa.text("'{}'")
# No CHECK constraint on `signals`: it is a JSON list of values drawn from
# PULSE_SIGNALS, and a CHECK cannot inspect list membership. Unknown values are
# therefore tolerated on write and filtered out on read (see
# services/signals.py) rather than refused by the database -- the alternative
# would be a join table, which is a lot of machinery for a set of suggestions
# a person is about to review and mostly edit by hand.


def upgrade() -> None:
    op.create_table(
        "document_groups",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("workspace_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("description", sa.Text()),
        sa.Column("source", sa.String(length=20), nullable=False, server_default="ai"),
        sa.Column(
            "is_archive", sa.Boolean(), nullable=False, server_default=sa.text("false")
        ),
        sa.Column("position", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("layout", sa.String(length=20), nullable=False, server_default="grid"),
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
        sa.UniqueConstraint("workspace_id", "name", name="uq_document_groups_ws_name"),
        sa.CheckConstraint(
            "source IN ('ai', 'user')", name="ck_document_groups_source"
        ),
        sa.CheckConstraint("layout IN ('grid', 'list')", name="ck_document_groups_layout"),
    )
    op.create_index(
        "ix_document_groups_workspace_position",
        "document_groups",
        ["workspace_id", "position"],
    )
    op.create_index("ix_document_groups_workspace_id", "document_groups", ["workspace_id"])

    op.create_table(
        "group_placements",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("group_id", sa.Integer(), nullable=False),
        sa.Column("workspace_id", sa.Integer(), nullable=False),
        sa.Column("repository_id", sa.Integer(), nullable=False),
        sa.Column("file_path", sa.String(length=1000), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("placed_by", sa.String(length=20)),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.ForeignKeyConstraint(
            ["group_id"], ["document_groups.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"], ["workspaces.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["repository_id"], ["repositories.id"], ondelete="CASCADE"
        ),
        # One row per (group, repository, path). A drag onto a group it is
        # already in is a no-op, not a second copy.
        sa.UniqueConstraint(
            "group_id", "repository_id", "file_path", name="uq_group_placements_member"
        ),
    )
    op.create_index("ix_group_placements_group_id", "group_placements", ["group_id"])
    op.create_index(
        "ix_group_placements_workspace_id", "group_placements", ["workspace_id"]
    )
    op.create_index(
        "ix_group_placements_repository_id", "group_placements", ["repository_id"]
    )
    # The inverse lookup that runs on every document selection: "which groups is
    # this document in?"
    op.create_index(
        "ix_group_placements_document",
        "group_placements",
        ["workspace_id", "repository_id", "file_path"],
    )

    op.add_column(
        "pulse_items",
        sa.Column(
            "signals",
            sa.JSON(),
            # An empty list rather than NULL: the column answers "what did Delphi
            # flag", and "nothing" is a real answer worth storing.
            server_default=_JSON_LIST,
            nullable=False,
        ),
    )
    op.add_column(
        "pulse_items",
        sa.Column("signal_refs", sa.JSON(), server_default=_JSON_MAP, nullable=False),
    )


def downgrade() -> None:
    # Deliberately drops the grouping. The tables are app state, not documents:
    # every row here can be rebuilt by re-running Delphi, and no file is
    # involved, so nothing is lost that the files on disk do not already hold.
    op.drop_column("pulse_items", "signal_refs")
    op.drop_column("pulse_items", "signals")

    op.drop_index("ix_group_placements_document", table_name="group_placements")
    op.drop_index("ix_group_placements_repository_id", table_name="group_placements")
    op.drop_index("ix_group_placements_workspace_id", table_name="group_placements")
    op.drop_index("ix_group_placements_group_id", table_name="group_placements")
    op.drop_table("group_placements")

    op.drop_index("ix_document_groups_workspace_id", table_name="document_groups")
    op.drop_index(
        "ix_document_groups_workspace_position", table_name="document_groups"
    )
    op.drop_table("document_groups")
