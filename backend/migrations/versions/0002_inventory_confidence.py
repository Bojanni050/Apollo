"""Inventory classification confidence and provenance.

Adds the columns that let a classification be *reviewed* rather than trusted:

* ``alternatives`` -- other categories the model weighed, so a contested
  judgement is visible as contested.
* ``reason`` -- the model's stated evidence.
* ``partial`` -- true when the classification rests on a structural digest
  rather than the whole document, which explains a lower confidence.

Every column is nullable or has a default, so existing rows are preserved
untouched. Adding a NOT NULL column without a default would fail on a populated
table, which is why ``partial`` is added in two steps: nullable first, backfilled
with the default, then constrained. That is safe on both PostgreSQL and SQLite.

Revision ID: 0002_inventory_confidence
Revises: 0001_initial
Create Date: 2026-09-26
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0002_inventory_confidence"
down_revision = "0001_initial"
branch_labels = None
depends_on = None


def _json() -> sa.types.TypeEngine:
    """JSONB on PostgreSQL, plain JSON on SQLite (no JSONB equivalent)."""
    return postgresql.JSONB().with_variant(sa.JSON(), "sqlite")


def upgrade() -> None:
    # 1. Add as nullable, so an existing table is never rewritten mid-constraint.
    op.add_column("inventory_items", sa.Column("alternatives", _json(), nullable=True))
    op.add_column("inventory_items", sa.Column("reason", sa.Text(), nullable=True))
    op.add_column(
        "inventory_items",
        sa.Column("partial", sa.Boolean(), server_default=sa.text("false"), nullable=True),
    )

    # 2. Backfill existing rows with the same defaults the model uses, so a
    #    historical item is not reported as "no alternatives" because the column
    #    was simply absent when it was written.
    #
    #    The update is expressed through a lightweight table construct rather
    #    than raw SQL: SQLAlchemy then applies each dialect's own type
    #    handling. A hand-written literal would have to be `'[]'::jsonb` on
    #    PostgreSQL and something else on SQLite.
    items = sa.table(
        "inventory_items",
        sa.column("alternatives", _json()),
        sa.column("partial", sa.Boolean()),
    )
    op.execute(
        items.update()
        .where(items.c.alternatives.is_(None))
        .values(alternatives=[])
    )
    op.execute(
        items.update().where(items.c.partial.is_(None)).values(partial=False)
    )

    # 3. Now constrain. batch_alter_table issues a plain ALTER on PostgreSQL and
    #    rebuilds the table on SQLite, which cannot do "SET NOT NULL" in place.
    with op.batch_alter_table("inventory_items") as batch:
        batch.alter_column(
            "partial", existing_type=sa.Boolean(),
            nullable=False, server_default=sa.text("false"),
        )


def downgrade() -> None:
    op.drop_column("inventory_items", "partial")
    op.drop_column("inventory_items", "reason")
    op.drop_column("inventory_items", "alternatives")