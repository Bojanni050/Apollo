"""Delphi Pulse: track which halves of a suggestion were accepted.

Adds ``pulse_items.applied_parts``, a JSON list holding the halves already
written to the document (``["tags"]``, ``["connections"]`` or both).

Tags and connections are separately acceptable, so ``decision`` alone can no
longer describe the state: after accepting only the tags the item is still
``pending``, and without this column the UI would show the same choices again
for something already on disk. It has to survive a reload, which is why it is a
column rather than component state.

Existing rows get an empty list, which reads as "nothing written yet" -- the
same state they were already in.

Revision ID: 0008_pulse_applied_parts
Revises: 0007_pulse_schedules
Create Date: 2026-10-05
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "0008_pulse_applied_parts"
down_revision = "0007_pulse_schedules"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "pulse_items",
        sa.Column(
            "applied_parts",
            sa.JSON(),
            # An empty JSON array rather than a bare NULL: the column answers
            # "which halves are written", and an empty list is a clearer answer
            # than unknown.
            server_default=sa.text("'[]'"),
            nullable=False,
        ),
    )


def downgrade() -> None:
    op.drop_column("pulse_items", "applied_parts")