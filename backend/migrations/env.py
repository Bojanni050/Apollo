"""Alembic environment.

The database URL is *not* taken from alembic.ini. It is resolved at runtime from
application settings (or ``TEST_DATABASE_URL``), so there is exactly one source
of truth and a stale URL in a checked-in ini file cannot send a migration to the
wrong database.

Autogenerate is intentionally not relied upon. Migrations are written and
committed by hand, because a generated migration is only as good as the diff it
was produced from -- and ``create_all`` cannot express a data backfill, a
``NOT NULL`` change on a populated column, or anything else that needs care.
"""
from __future__ import annotations

from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

from app.db import Base
from app.migrations import database_url

# Importing the models registers every table on Base.metadata, which is what
# autogenerate compares the database against.
from app import models  # noqa: F401,E402

config = context.config

# Resolve the database URL, unless the caller already supplied one.
#
# `alembic_config(url)` sets this explicitly, and that value MUST win -- the
# migration tests point a single Alembic invocation at a throwaway database.
# Overriding it here would silently send every migration to the configured
# database instead, which is exactly the kind of mistake this is meant to
# prevent. With no explicit URL, fall back to application settings (or
# TEST_DATABASE_URL), so a plain `alembic upgrade head` does the right thing.
#
# '%' must be escaped because it is ConfigParser's interpolation char, and a
# percent-encoded password would otherwise be read as a variable.
if not config.get_main_option("sqlalchemy.url", None):
    config.set_main_option(
        "sqlalchemy.url", database_url().replace("%", "%%")
    )

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """Emit SQL to stdout without a DBAPI connection (for review)."""
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        # Makes autogenerate able to detect column type and server-default
        # changes rather than only added/removed tables.
        compare_type=True,
        compare_server_default=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Apply migrations against a live connection."""
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
            compare_server_default=True,
            # Required on SQLite: without it, dropping an unconstrained column
            # (which SQLite does not support) silently fails or is skipped.
            render_as_batch=connection.dialect.name == "sqlite",
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
