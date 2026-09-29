"""Pulse items name their repository.

Adds ``pulse_items.repository_id``. A Pulse run used to scan exactly one
repository -- the workspace's own documentation repository -- so an item's
``file_path`` was unambiguous: relative to that one root. Runs now scan the
inbox storage as well (new documents arrive in the inbox, Delphi analyses
them there, and only then are they filed out of it), and the same relative
path can exist in both trees. The item therefore has to say which repository
it was found in, or opening and applying it would resolve against whichever
root the route happened to pick.

Nullable on purpose, and existing rows keep NULL: at the time of the
migration every existing item did come from the documentation repository,
and the routes fall back to that repository when the column is empty. No
guessing is written into the data; the fallback lives in one function
(``_item_root`` in routes_pulse.py) where it can be found and argued with.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0014_pulse_item_repository"
down_revision = "0013_group_folder"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "pulse_items",
        sa.Column("repository_id", sa.Integer(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("pulse_items", "repository_id")
