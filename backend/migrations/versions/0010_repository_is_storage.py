"""Repositories Apollo keeps itself: mark the inbox storage as such.

Adds ``repositories.is_storage``, true for the one repository a workspace has
that the *application* created rather than the operator: the directory dropped-in
documents are stored in (see services/storage.py).

Why a column. A workspace can hold two documentation repositories at once -- the
folder the operator registered, and Apollo's intake -- and the pulse and
inventory runs have to know which one is the documentation. They are not
interchangeable: scanning the intake would report "nothing found" about an empty
inbox while the reader's actual documentation sat unexamined, and scanning the
registered folder would silently skip everything they had dropped in.

Deriving it from the path was the alternative and is worse: it would make the
answer depend on where APOLLO_STORAGE_ROOT is configured, so moving the storage
directory on disk would change which folder the app considers documentation.

Existing rows get false, which is what they are: before this column there was no
way to create an application-owned repository at all.

Revision ID: 0010_repository_is_storage
Revises: 0009_visual_groups_and_signals
Create Date: 2026-09-28
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "0010_repository_is_storage"
down_revision = "0009_visual_groups_and_signals"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "repositories",
        sa.Column(
            "is_storage",
            sa.Boolean(),
            nullable=False,
            # Every row that exists now was registered by the operator, so false
            # states the truth rather than a placeholder. Nothing is reclassified
            # by this migration.
            server_default=sa.text("false"),
        ),
    )


def downgrade() -> None:
    # Drops the marker, not the repository. The rows, and every document they
    # point at, stay exactly as they are; only the distinction between an
    # operator's folder and the app's intake is lost.
    op.drop_column("repositories", "is_storage")
