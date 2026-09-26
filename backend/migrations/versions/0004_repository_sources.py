"""Repository sources: source_type, source_url, status and last_synced_at.

A ``kind='source'`` repository can now be a local Git checkout or a GitHub
repository. The new columns store which flavour it is, the remote identity for
GitHub sources, the current synchronization status, and when it was last
synchronized. All are nullable: a documentation repository has none of them,
and a source row created by the original code (or by a raw insert) keeps
working -- its status is backfilled to 'ready', which matches how a local
checkout that has never been synchronized reads today.

Revision ID: 0004_repository_sources
Revises: 0003_decision_superseded_by
Create Date: 2026-10-04
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "0004_repository_sources"
down_revision = "0003_decision_superseded_by"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("repositories") as batch:
        batch.add_column(sa.Column("source_type", sa.String(length=20), nullable=True))
        batch.add_column(sa.Column("source_url", sa.String(length=1000), nullable=True))
        batch.add_column(sa.Column("status", sa.String(length=20), nullable=True))
        batch.add_column(sa.Column("status_message", sa.Text(), nullable=True))
        batch.add_column(sa.Column("last_synced_at", sa.DateTime(timezone=True), nullable=True))

    # Existing documentation rows keep NULL status; existing source rows (and
    # anything inserted without one) get 'ready', which matches how a local
    # checkout that has never been synchronized reads today.
    op.execute("UPDATE repositories SET status = 'ready' WHERE status IS NULL")

    with op.batch_alter_table("repositories") as batch:
        batch.alter_column(
            "status",
            existing_type=sa.String(length=20),
            nullable=False,
            server_default=sa.text("'ready'"),
        )
        batch.create_check_constraint(
            "ck_repositories_source_type", "source_type IN ('local', 'github')"
        )
        batch.create_check_constraint(
            "ck_repositories_status", "status IN ('pending', 'ready', 'error', 'missing')"
        )


def downgrade() -> None:
    with op.batch_alter_table("repositories") as batch:
        batch.drop_constraint("ck_repositories_status", type_="check")
        batch.drop_constraint("ck_repositories_source_type", type_="check")
        batch.alter_column(
            "status", existing_type=sa.String(length=20), nullable=True, server_default=None
        )
        batch.drop_column("last_synced_at")
        batch.drop_column("status_message")
        batch.drop_column("status")
        batch.drop_column("source_url")
        batch.drop_column("source_type")
