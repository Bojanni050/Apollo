"""Decision superseded_by relationship.

Adds self-referencing superseded_by_id foreign key and index to decisions table.

Revision ID: 0003_decision_superseded_by
Revises: 0002_inventory_confidence
Create Date: 2026-09-26
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "0003_decision_superseded_by"
down_revision = "0002_inventory_confidence"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("decisions") as batch:
        batch.add_column(sa.Column("superseded_by_id", sa.Integer(), nullable=True))
        batch.create_foreign_key(
            "fk_decisions_superseded_by_id",
            "decisions",
            ["superseded_by_id"],
            ["id"],
            ondelete="SET NULL",
        )
        batch.create_index("ix_decisions_superseded_by_id", ["superseded_by_id"])


def downgrade() -> None:
    with op.batch_alter_table("decisions") as batch:
        batch.drop_index("ix_decisions_superseded_by_id")
        batch.drop_constraint("fk_decisions_superseded_by_id", type_="foreignkey")
        batch.drop_column("superseded_by_id")
