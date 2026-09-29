"""Groups get a reviewed flag.

Adds ``document_groups.reviewed``: whether the reader has looked at a group and
kept it. A group the reader made is reviewed the moment it exists -- making it
is the decision. A group Delphi proposes starts unreviewed, so the Groups board
can offer "accept or reject" instead of presenting Delphi's guess as already
settled.

Existing rows default to reviewed (``server_default=true``), deliberately: this
governs new proposals from here on, not a retroactive audit of groups a reader
has already been living with. Rejecting an unreviewed group deletes it outright
(``delete_group``, unchanged) -- its documents are left exactly where they were,
simply no longer in that group.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0014_group_reviewed"
down_revision = "0013_group_folder"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "document_groups",
        sa.Column(
            "reviewed",
            sa.Boolean(),
            nullable=False,
            server_default=sa.true(),
        ),
    )


def downgrade() -> None:
    op.drop_column("document_groups", "reviewed")
