"""Groups get a parent: the area a topic belongs under.

Adds ``document_groups.parent_group_id``, a self-referential, nullable foreign
key. Two kinds of group now share the same table without a new column to say
which: an *area* is a group with a folder and no parent (the stable, on-disk
top level -- Architecture, Product, Decisions); a *topic* is a group with a
parent (the area it belongs under), whose own folder -- when it has one --
composes with its area's into a subfolder rather than standing on its own.
One level only: a group that is someone's parent is never itself given one,
so "area" stays flat rather than a tree Delphi could grow arbitrarily deep.
That rule is enforced in ``services/placement.py``, not by the schema.

``ON DELETE SET NULL``, not CASCADE: removing an area ungroups its topics back
to plain views, the same as deleting any other group never touches what was
inside it.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0016_group_parent"
down_revision = "0015_group_reviewed"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "document_groups",
        sa.Column("parent_group_id", sa.Integer(), nullable=True),
    )
    op.create_index(
        "ix_document_groups_parent_group_id",
        "document_groups",
        ["parent_group_id"],
    )
    op.create_foreign_key(
        "fk_document_groups_parent_group_id",
        "document_groups",
        "document_groups",
        ["parent_group_id"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint(
        "fk_document_groups_parent_group_id", "document_groups", type_="foreignkey"
    )
    op.drop_index("ix_document_groups_parent_group_id", table_name="document_groups")
    op.drop_column("document_groups", "parent_group_id")
