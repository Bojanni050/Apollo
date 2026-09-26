"""Delphi Pulse schedules: per-workspace automatic scan timing.

Extends ``workspace_pulse_settings`` with the columns the background
scheduler needs:

* ``schedule_enabled`` -- whether the scheduler runs scans at all;
* ``schedule_kind`` -- ``interval`` (every N hours) or ``weekly`` (a chosen
  weekday at a chosen hour);
* ``interval_hours`` -- 1-24, for the interval kind;
* ``weekly_day`` / ``weekly_hour`` -- 0=Monday..6=Sunday, hour 0-23;
* ``last_run_at`` -- when the scheduler last ran, so a restart does not
  immediately re-run a slot that was already served.

All columns are added nullable-or-defaulted, so existing rows (and existing
workspaces) keep their current behaviour: scheduling off. PostgreSQL gets
CHECK constraints matching the model; SQLite is guarded, as before, since its
schema is derived from the models.

Revision ID: 0007_pulse_schedules
Revises: 0006_pulse_runs
Create Date: 2026-10-04
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "0007_pulse_schedules"
down_revision = "0006_pulse_runs"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "workspace_pulse_settings",
        sa.Column(
            "schedule_enabled",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
    )
    op.add_column(
        "workspace_pulse_settings",
        sa.Column(
            "schedule_kind",
            sa.String(length=20),
            server_default="interval",
            nullable=False,
        ),
    )
    op.add_column(
        "workspace_pulse_settings",
        sa.Column("interval_hours", sa.Integer(), server_default=sa.text("1"), nullable=False),
    )
    op.add_column(
        "workspace_pulse_settings",
        sa.Column("weekly_day", sa.Integer(), server_default=sa.text("0"), nullable=False),
    )
    op.add_column(
        "workspace_pulse_settings",
        sa.Column("weekly_hour", sa.Integer(), server_default=sa.text("0"), nullable=False),
    )
    op.add_column(
        "workspace_pulse_settings",
        sa.Column("last_run_at", sa.DateTime(timezone=True), nullable=True),
    )
    with op.batch_alter_table("workspace_pulse_settings") as batch:
        batch.create_check_constraint(
            "ck_workspace_pulse_settings_mode",
            "mode IN ('suggest', 'apply')",
        )
        batch.create_check_constraint(
            "ck_workspace_pulse_settings_kind",
            "schedule_kind IN ('interval', 'weekly')",
        )


def downgrade() -> None:
    with op.batch_alter_table("workspace_pulse_settings") as batch:
        batch.drop_constraint("ck_workspace_pulse_settings_kind", type_="check")
        batch.drop_constraint("ck_workspace_pulse_settings_mode", type_="check")
    op.drop_column("workspace_pulse_settings", "last_run_at")
    op.drop_column("workspace_pulse_settings", "weekly_hour")
    op.drop_column("workspace_pulse_settings", "weekly_day")
    op.drop_column("workspace_pulse_settings", "interval_hours")
    op.drop_column("workspace_pulse_settings", "schedule_kind")
    op.drop_column("workspace_pulse_settings", "schedule_enabled")
