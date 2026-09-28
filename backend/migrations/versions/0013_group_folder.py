"""Groups get a folder.

Adds ``document_groups.folder``: the folder under the working directory where a
group's documents belong. This is the column that connects the arrangement to
the disk, and it is nullable on purpose -- a group without a folder stays a pure
view, and putting a document in it changes the board and nothing else.

Why nothing is backfilled. The obvious move would be to derive a folder from
each group's name. That would be wrong twice over. It would give every group
Delphi proposed a directory it never asked for, and a generated name is only
ever approximately the name the reader would have typed -- so the folder would
be created on the first placement, with whatever the generation produced,
including a character the filesystem dislikes. A folder is a thing on disk that
the reader will meet in their file manager; it is written down by them, one at
a time, and the first one is the archive.

Why a group folder is not a move. Adding the column changes nothing about where
files are. Placement still writes a row, and a document only travels when a
proposal built from this folder is accepted. The column says where a document
*belongs*; the proposal is what makes it go there.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0013_group_folder"
down_revision = "0012_workspace_working_dir"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "document_groups",
        sa.Column("folder", sa.String(length=200), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("document_groups", "folder")
