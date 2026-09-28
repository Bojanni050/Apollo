"""The folder the reader works in.

Adds ``workspaces.working_dir``: the directory a workspace's own documents live in,
chosen by the reader rather than invented by the application. Everything Apollo
keeps for a workspace -- the inbox, and later the group folders and the archive --
lives under it.

Why the reader chooses it. Apollo's own storage is a sensible default and the wrong
one: a folder the application picked is a folder the reader has never looked at, and
the first thing this product does with a document is rearrange it on disk. A folder
somebody chose, preferably one that was empty, is theirs, and the arrangement that
follows is visible in a file manager they already use.

Nullable, and null is meaningful: it means "no choice made", and then the workspace
behaves exactly as it did before this column existed -- everything under
``APOLLO_STORAGE_ROOT``. That is what makes the migration safe: an existing
workspace changes nothing until somebody decides otherwise, and the reader decides
for themselves rather than inheriting a move they did not ask for.

Stored absolute and resolved, so the folder a workspace points at cannot change
meaning when the application is started from a different directory.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0012_workspace_working_dir"
down_revision = "0011_doc_signals"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "workspaces",
        sa.Column("working_dir", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("workspaces", "working_dir")
