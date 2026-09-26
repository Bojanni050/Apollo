"""AI Pulse: runs, per-document suggestions and per-workspace settings.

A Pulse run walks the documentation repository with the *background* model
(the cheap tier) and produces, per document:

* ``pulse_items`` -- a one-sentence summary, 1-5 thematic tags, and
  connections to other documents in the same repository (relation + why);
* ``workspace_pulse_settings`` -- the workspace's choice between "suggest"
  (a human approves every write) and "apply" (Pulse writes front matter
  directly, still through the guarded write path).

Applied suggestions are written into the documents as YAML front matter
(``pulse-tags`` / ``pulse-connections`` keys), not stored in the database:
the Markdown repository remains the single source of truth, and the
database holds only what the application needs to track -- run history and
approval state.

SQLite: guarded, as the models already carry plain JSON variants for the
JSONB columns, so the same table definitions serve both dialects. Only
created on PostgreSQL via Alembic; SQLite derives its schema from the
models (see app.db.init_db).

Revision ID: 0006_pulse_runs
Revises: 0005_semantic_chunks
Create Date: 2026-10-04
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0006_pulse_runs"
down_revision = "0005_semantic_chunks"
branch_labels = None
depends_on = None


def _json() -> sa.types.TypeEngine:
    """JSONB on PostgreSQL, plain JSON on SQLite (no JSONB equivalent)."""
    return postgresql.JSONB().with_variant(sa.JSON(), "sqlite")


def _check(column: str, allowed: tuple[str, ...], name: str) -> sa.CheckConstraint:
    values = ", ".join(f"'{v}'" for v in allowed)
    return sa.CheckConstraint(f"{column} IN ({values})", name=name)


def _timestamps() -> list[sa.Column]:
    return [
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
    ]


def upgrade() -> None:
    op.create_table(
        "pulse_runs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "workspace_id",
            sa.Integer(),
            sa.ForeignKey("workspaces.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "status", sa.String(length=20), server_default="pending", nullable=False
        ),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column("scanned_state", _json(), nullable=True),
        sa.Column(
            "mode", sa.String(length=20), server_default="suggest", nullable=False
        ),
        *_timestamps(),
        _check("status", ("pending", "completed", "failed"), "ck_pulse_runs_status"),
    )
    op.create_index("ix_pulse_runs_workspace_id", "pulse_runs", ["workspace_id"])

    op.create_table(
        "pulse_items",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "run_id",
            sa.Integer(),
            sa.ForeignKey("pulse_runs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("file_path", sa.String(length=1000), nullable=False),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column("tags", _json(), nullable=True),
        sa.Column("connections", _json(), nullable=True),
        sa.Column("confidence", sa.Float(), nullable=True),
        sa.Column(
            "decision", sa.String(length=20), server_default="pending", nullable=False
        ),
        _check(
            "decision",
            ("pending", "applied", "skipped"),
            "ck_pulse_items_decision",
        ),
    )
    op.create_index("ix_pulse_items_run_id", "pulse_items", ["run_id"])

    op.create_table(
        "workspace_pulse_settings",
        sa.Column(
            "workspace_id",
            sa.Integer(),
            sa.ForeignKey("workspaces.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "mode", sa.String(length=20), server_default="suggest", nullable=False
        ),
        *_timestamps(),
    )


def downgrade() -> None:
    op.drop_table("workspace_pulse_settings")
    op.drop_index("ix_pulse_items_run_id", table_name="pulse_items")
    op.drop_table("pulse_items")
    op.drop_index("ix_pulse_runs_workspace_id", table_name="pulse_runs")
    op.drop_table("pulse_runs")
