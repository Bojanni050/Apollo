"""Alembic wiring.

The configuration is built in code rather than only in ``alembic.ini`` so that
the database URL always comes from application settings. An ``alembic.ini`` with
a hardcoded URL is a second source of truth that silently goes stale, and it is
the usual reason a migration is applied to the wrong database.

For the test suite, ``TEST_DATABASE_URL`` overrides the application URL, so a
migration can be exercised against a disposable database without touching the
configured one.
"""
from __future__ import annotations

import os
from pathlib import Path

from alembic.config import Config
from sqlalchemy import engine_from_config, pool

from app.config import settings

#: Repository root (``backend/``). Migrations live beside this package.
BACKEND_DIR = Path(__file__).resolve().parent.parent
ALEMBIC_INI = BACKEND_DIR / "alembic.ini"
MIGRATIONS_DIR = BACKEND_DIR / "migrations"


def database_url() -> str:
    """The URL migrations should run against.

    ``TEST_DATABASE_URL`` wins when present: the migration tests must never be
    able to touch a real database by accident.
    """
    return os.environ.get("TEST_DATABASE_URL") or settings.database_url


def alembic_config(url: str | None = None) -> Config:
    """A configured Alembic ``Config`` bound to the right database URL."""
    config = Config(str(ALEMBIC_INI)) if ALEMBIC_INI.exists() else Config()
    config.set_main_option("script_location", str(MIGRATIONS_DIR))
    # '%' is Alembic's interpolation character, so a URL containing one (a
    # percent-encoded password) must be escaped or it is parsed as a variable.
    config.set_main_option("sqlalchemy.url", (url or database_url()).replace("%", "%%"))
    return config


def script_directory():
    """The migration script directory."""
    from alembic.script import ScriptDirectory

    return ScriptDirectory.from_config(alembic_config())


def run_migrations_offline() -> None:
    """Emit SQL to stdout without connecting -- for review before applying."""
    from alembic import command

    command.upgrade(alembic_config(), "head", sql=True)


def run_migrations_online() -> None:
    """Apply migrations against a live connection.

    A connection is opened from the configured URL directly rather than reusing
    the application's engine, so ``alembic`` is a genuinely independent path:
    if the application's pooling or hooks were wrong, migrations would still be
    applied correctly.
    """
    from alembic import command

    config = alembic_config()
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context = connection.begin()
        try:
            context.run_migrations()
        except Exception:
            # A failed migration must not leave a half-applied transaction
            # committed; SQLite and PostgreSQL both honour the rollback.
            context.rollback()
            raise
