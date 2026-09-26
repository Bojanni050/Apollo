"""Alembic migration tests, against a real PostgreSQL server.

These run against a **separate, throwaway database** (``..._migrations``), not
the one the other PostgreSQL tests use. That separation is the point: a
migration test is destructive by nature -- it drops and recreates every table --
and must never be able to touch the database another test is relying on.

Covered: upgrade from empty, the resulting schema, downgrade to base, and the
full round trip. Also the adoption path for a database that already exists from
the old ``create_all`` scheme, which must be *stamped*, not migrated.
"""
from __future__ import annotations

import os

import pytest

pytestmark = pytest.mark.postgres

TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL")

if not TEST_DATABASE_URL or TEST_DATABASE_URL.split("://", 1)[0].split("+", 1)[0] not in {
    "postgres",
    "postgresql",
}:
    pytest.skip(
        "TEST_DATABASE_URL must point at PostgreSQL for the migration tests",
        allow_module_level=True,
    )

from alembic import command  # noqa: E402
from sqlalchemy import create_engine, inspect, text  # noqa: E402
from sqlalchemy.engine import make_url  # noqa: E402

from app import models  # noqa: E402,F401
from app.db import Base  # noqa: E402
from app.migrations import alembic_config  # noqa: E402

#: A dedicated database, so dropping every table here cannot affect the
#: database used by test_postgres.py.
MIGRATION_DB_NAME = "gaia_docs_migrations"
# render_as_string(hide_password=False) is required: the default renders the
# password as "***", which would then be used as the real password.
MIGRATION_URL = make_url(TEST_DATABASE_URL).set(
    database=MIGRATION_DB_NAME
).render_as_string(hide_password=False)
ADMIN_URL = make_url(TEST_DATABASE_URL).set(
    database="postgres"
).render_as_string(hide_password=False)


def _config():
    return alembic_config(MIGRATION_URL)


def _drop_and_create() -> None:
    """Start from a genuinely empty database.

    CREATE DATABASE cannot run inside a transaction, hence AUTOCOMMIT. An
    Engine is not itself a context manager -- the Connection is.
    """
    engine = create_engine(ADMIN_URL, isolation_level="AUTOCOMMIT")
    try:
        with engine.connect() as connection:
            connection.execute(text(f'DROP DATABASE IF EXISTS "{MIGRATION_DB_NAME}"'))
            connection.execute(text(f'CREATE DATABASE "{MIGRATION_DB_NAME}"'))
    finally:
        engine.dispose()


def _tables() -> set[str]:
    engine = create_engine(MIGRATION_URL)
    try:
        return set(inspect(engine).get_table_names())
    finally:
        engine.dispose()


def _scalar(sql: str):
    engine = create_engine(MIGRATION_URL)
    try:
        with engine.connect() as connection:
            return connection.execute(text(sql)).scalar()
    finally:
        engine.dispose()


EXPECTED_TABLES = {
    "workspaces", "repositories", "conversations", "messages",
    "open_questions", "decisions", "change_proposals",
    "inventory_runs", "inventory_items",
}


def head_revision() -> str:
    """The current head revision, read from the migration scripts.

    Deliberately not hardcoded. A test that asserts ``== "0001_initial"`` breaks
    on every future migration, which trains people to update the test instead of
    reading the failure. What matters is that *the* head is applied, not which
    number it happens to be.
    """
    from app.migrations import script_directory

    return script_directory().get_current_head()


# ---------------------------------------------------------------------------
# Upgrade
# ---------------------------------------------------------------------------


def test_upgrade_from_empty_creates_the_schema() -> None:
    _drop_and_create()
    command.upgrade(_config(), "head")
    assert EXPECTED_TABLES <= _tables()
    assert "alembic_version" in _tables()


def test_migration_records_its_revision() -> None:
    _drop_and_create()
    command.upgrade(_config(), "head")
    assert _scalar("select version_num from alembic_version") == head_revision()


def test_migrated_schema_matches_the_models() -> None:
    """The migration and the models must describe the same schema.

    This is the test that stops them drifting apart: a table or column added to
    the models but never migrated shows up here as a difference.
    """
    _drop_and_create()
    command.upgrade(_config(), "head")

    engine = create_engine(MIGRATION_URL)
    try:
        inspector = inspect(engine)
        migrated = set(inspector.get_table_names()) - {"alembic_version"}
        assert migrated == set(Base.metadata.tables)
        for table in sorted(migrated):
            migrated_cols = {c["name"] for c in inspector.get_columns(table)}
            assert migrated_cols == set(Base.metadata.tables[table].columns.keys()), table
    finally:
        engine.dispose()


def test_already_applied_ancestors_are_not_pending() -> None:
    """Regression: 'pending' must mean 'not applied', not 'in the history'.

    Walking the revision chain upwards would report every applied ancestor as
    outstanding. That is invisible with a single revision and wrong as soon as
    there is a second one, so it is asserted explicitly.
    """
    from app.db import pending_revisions

    _drop_and_create()
    config = _config()
    command.upgrade(config, "head")

    engine = create_engine(MIGRATION_URL)
    try:
        assert pending_revisions(engine) == []
    finally:
        engine.dispose()

    # Stepping back one revision must report exactly one outstanding migration.
    command.downgrade(config, "-1")
    engine = create_engine(MIGRATION_URL)
    try:
        assert pending_revisions(engine) == ["0002_inventory_confidence"]
    finally:
        engine.dispose()


def test_downgrading_to_base_leaves_everything_pending() -> None:
    from app.db import pending_revisions

    _drop_and_create()
    config = _config()
    command.upgrade(config, "head")
    command.downgrade(config, "base")

    engine = create_engine(MIGRATION_URL)
    try:
        pending = pending_revisions(engine)
        assert head_revision() in pending
        assert "0001_initial" in pending
    finally:
        engine.dispose()


def test_migrated_schema_uses_postgres_types() -> None:
    _drop_and_create()
    command.upgrade(_config(), "head")

    engine = create_engine(MIGRATION_URL)
    try:
        with engine.connect() as connection:
            jsonb = set(connection.execute(text(
                "select table_name, column_name from information_schema.columns "
                "where table_schema='public' and data_type='jsonb'"
            )).all())
            uid = connection.execute(text(
                "select data_type from information_schema.columns "
                "where table_name='open_questions' and column_name='uid'"
            )).scalar()
            created = connection.execute(text(
                "select data_type from information_schema.columns "
                "where table_name='workspaces' and column_name='created_at'"
            )).scalar()
        assert ("messages", "citations") in jsonb
        assert ("change_proposals", "changes") in jsonb
        assert uid == "uuid"
        assert created == "timestamp with time zone"
    finally:
        engine.dispose()


def test_migrated_schema_has_constraints_and_indexes() -> None:
    _drop_and_create()
    command.upgrade(_config(), "head")

    engine = create_engine(MIGRATION_URL)
    try:
        inspector = inspect(engine)
        checks = {
            name
            for table in ("conversations", "messages", "repositories",
                          "change_proposals", "inventory_items")
            for name in [c["name"] for c in inspector.get_check_constraints(table)]
        }
        assert "ck_conversations_mode" in checks
        assert "ck_messages_role" in checks
        indexes = {i["name"] for i in inspector.get_indexes("change_proposals")}
        assert "ix_change_proposals_workspace_status" in indexes
    finally:
        engine.dispose()


# ---------------------------------------------------------------------------
# Downgrade
# ---------------------------------------------------------------------------


def test_downgrade_to_base_removes_every_table() -> None:
    _drop_and_create()
    config = _config()
    command.upgrade(config, "head")
    command.downgrade(config, "base")
    # Only alembic_version survives; Alembic drops even that on a full downgrade.
    assert _tables() <= {"alembic_version"}


def test_upgrade_downgrade_upgrade_round_trip() -> None:
    """The full cycle, which is what CI and a rollback would actually do."""
    _drop_and_create()
    config = _config()
    command.upgrade(config, "head")
    command.downgrade(config, "base")
    command.upgrade(config, "head")

    assert EXPECTED_TABLES <= _tables()
    assert _scalar("select version_num from alembic_version") == head_revision()


def test_downgrade_really_drops_tables() -> None:
    """Sanity check that downgrade drops the tables rather than unlinking them."""
    _drop_and_create()
    config = _config()
    command.upgrade(config, "head")

    engine = create_engine(MIGRATION_URL)
    try:
        with engine.begin() as connection:
            connection.execute(text(
                "insert into workspaces (name, created_at, updated_at) "
                "values ('temp', now(), now())"
            ))
    finally:
        engine.dispose()

    command.downgrade(config, "base")
    assert "workspaces" not in _tables()


# ---------------------------------------------------------------------------
# Adopting an existing create_all database
# ---------------------------------------------------------------------------


def test_existing_create_all_database_is_adopted_by_stamping() -> None:
    """The documented path for a database that predates migrations.

    The tables already exist, so `upgrade` would fail with "relation already
    exists". The correct action is `stamp`: it records the revision without
    touching the data. This is the non-destructive adoption procedure, and this
    test is what proves existing data survives it.
    """
    _drop_and_create()

    # Simulate the old behaviour: create the schema with no migration record.
    engine = create_engine(MIGRATION_URL)
    try:
        Base.metadata.create_all(bind=engine)
        with engine.begin() as connection:
            connection.execute(text(
                "insert into workspaces (name, created_at, updated_at) "
                "values ('existing data', now(), now())"
            ))
    finally:
        engine.dispose()

    config = _config()
    # Upgrade would fail here -- stamping is what an operator must do instead.
    with pytest.raises(Exception):
        command.upgrade(config, "head")

    command.stamp(config, "head")

    assert _scalar("select name from workspaces") == "existing data", (
        "stamping must not destroy existing data"
    )
    assert _scalar("select version_num from alembic_version") == head_revision()


def test_stamp_then_upgrade_is_a_noop() -> None:
    """After adoption, future migrations apply normally."""
    _drop_and_create()
    config = _config()
    command.stamp(config, "head")
    command.upgrade(config, "head")  # already at head
    assert _scalar("select version_num from alembic_version") == head_revision()


# ---------------------------------------------------------------------------
# The SQLite path
# ---------------------------------------------------------------------------


def test_migration_also_runs_on_sqlite(tmp_path) -> None:
    """The same revision applies to a local SQLite database.

    A developer running with SQLite gets the same schema, and the same
    upgrade/downgrade behaviour, without a PostgreSQL server.
    """
    url = f"sqlite:///{tmp_path / 'migrate.db'}"
    config = alembic_config(url)

    command.upgrade(config, "head")
    engine = create_engine(url)
    try:
        assert EXPECTED_TABLES <= set(inspect(engine).get_table_names())
    finally:
        engine.dispose()

    command.downgrade(config, "base")
    engine = create_engine(url)
    try:
        assert set(inspect(engine).get_table_names()) <= {"alembic_version"}
    finally:
        engine.dispose()


