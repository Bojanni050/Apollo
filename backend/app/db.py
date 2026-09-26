"""Database engine, session factory and declarative base.

Two things matter here for production safety.

**The schema is owned by Alembic, not by this module.** ``init_db()`` no longer
calls ``create_all`` on PostgreSQL: with a migration tool in place, having the
application silently create tables is how a database and its migrations drift
apart. The schema is applied by ``alembic upgrade head`` and verified at startup.

**Engine options are per-dialect.** SQLite needs ``check_same_thread`` disabled
because the dev server serves requests from a thread pool; PostgreSQL needs
``pool_pre_ping`` so a connection killed by a firewall or a restart is replaced
instead of raising mid-request. The two are genuinely different databases and
are not configured as if they were the same.
"""
from __future__ import annotations

import logging
from collections.abc import Iterator
from urllib.parse import urlsplit

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.config import settings

logger = logging.getLogger("gaia_docs_architect")


class Base(DeclarativeBase):
    pass


def _dialect(url: str) -> str:
    return url.split("://", 1)[0].lower().split("+", 1)[0]


def is_postgres(url: str) -> bool:
    """True for PostgreSQL URLs, whether written as postgresql:// or +psycopg."""
    return _dialect(url) in {"postgres", "postgresql"}


def is_sqlite(url: str) -> bool:
    return _dialect(url) == "sqlite"


def _engine_kwargs(url: str) -> dict:
    """Dialect-appropriate engine options.

    Kept separate so the difference between the two databases is explicit
    rather than hidden inside a single ``create_engine`` call.
    """
    if is_sqlite(url):
        return {
            # The dev server and the test client are multithreaded, but SQLite
            # serialises writes itself; the check would otherwise reject any
            # cross-thread session.
            "connect_args": {"check_same_thread": False},
        }

    return {
        # Replace a connection killed by a firewall or a server restart,
        # instead of raising "server closed the connection" mid-request.
        "pool_pre_ping": True,
        # Recycle before a typical proxy/firewall idle timeout (30 min).
        "pool_recycle": 1_800,
        "pool_size": 10,
        "max_overflow": 20,
    }


def _register_postgres_hooks(engine: Engine) -> None:
    """Pin the session timezone so timestamptz behaviour is predictable.

    Python hands psycopg timezone-aware datetimes, but the *session* timezone
    decides how ``now()`` and any server-side default are interpreted. Pinning
    it to UTC means a value written from one host and read from another is
    always the same instant.
    """

    @event.listens_for(engine, "connect")
    def _set_timezone(dbapi_connection, _record):  # pragma: no cover - driver hook
        with dbapi_connection.cursor() as cursor:
            cursor.execute("SET TIME ZONE 'UTC'")


def build_engine(url: str, **overrides) -> Engine:
    """Create an engine configured for the dialect in ``url``."""
    kwargs = _engine_kwargs(url)
    kwargs.update(overrides)
    engine = create_engine(url, echo=False, future=True, **kwargs)

    if is_sqlite(url) and (":memory:" in url or url.endswith("sqlite://")):
        # Without a shared pool each connection would get its *own* empty
        # in-memory database, so a migrated schema would vanish immediately.
        from sqlalchemy.pool import StaticPool

        engine = engine.execution_options(poolclass=StaticPool)

    if is_postgres(url):
        _register_postgres_hooks(engine)

    return engine


engine = build_engine(settings.database_url)

SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)


def session_factory(bind: Engine | None = None) -> sessionmaker:
    """A session factory, optionally bound to another engine (used by tests)."""
    return sessionmaker(bind=bind or engine, autoflush=False, autocommit=False, future=True)


def get_db() -> Iterator[Session]:
    """FastAPI dependency yielding a request-scoped session."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Schema management
# ---------------------------------------------------------------------------


def import_models() -> None:
    """Import the models module so every table is registered on the metadata."""
    from app import models  # noqa: F401  (registration side effect)


def applied_revisions(bind: Engine | None = None) -> list[str]:
    """Alembic revisions recorded as applied to the database."""
    from alembic.runtime.migration import MigrationContext

    bind = bind or engine
    with bind.connect() as connection:
        return list(MigrationContext.configure(connection).get_current_heads())


def pending_revisions(bind: Engine | None = None) -> list[str]:
    """Revisions that exist as migrations but are not applied to the database.

    Walks *down* from head and stops at the first revision that is already
    applied. Walking the whole chain upwards would list every applied ancestor
    as pending, which is harmless with a single revision and wrong as soon as
    there are two.
    """
    from alembic.runtime.migration import MigrationContext

    from app.migrations import script_directory

    bind = bind or engine
    script = script_directory()
    with bind.connect() as connection:
        current = set(MigrationContext.configure(connection).get_current_heads())

    if not current:
        # Nothing recorded: the whole chain is outstanding.
        return [r.revision for r in script.walk_revisions("base", "heads")]

    pending: list[str] = []
    for revision in script.iterate_revisions("heads", "base"):
        if revision.revision in current:
            # Everything at or below this point is already applied.
            break
        pending.append(revision.revision)
    return pending


def init_db(bind: Engine | None = None) -> None:
    """Bring the database up to date at application startup.

    PostgreSQL is the production path, and the rule there is explicit:
    migrations are applied, or the server refuses to start. It never silently
    creates tables, because a schema that appears without a migration record is
    exactly the drift this exists to prevent.

    ``bind`` exists for tests, which need to point this at a disposable
    database; production always uses the process-wide engine.
    """
    import_models()

    bind = engine if bind is None else bind
    # render_as_string(hide_password=False) is essential: the default renders
    # the password as "***", which would then be sent to the server as a real
    # password and make every migration fail to authenticate.
    target_url = bind.url.render_as_string(hide_password=False)

    if is_postgres(target_url):
        from alembic import command

        from app.migrations import alembic_config

        if settings.db_migrate_on_startup:
            logger.info("Applying database migrations to PostgreSQL...")
            command.upgrade(alembic_config(target_url), "head")
        else:
            missing = pending_revisions(bind)
            if missing:
                raise RuntimeError(
                    "The database schema is out of date. Missing migration(s): "
                    f"{', '.join(missing)}. Run `alembic upgrade head`, or set "
                    "DB_MIGRATE_ON_STARTUP=true to apply them at startup."
                )
        logger.info(
            "Database ready (PostgreSQL, revision %s).",
            ",".join(applied_revisions(bind)) or "none",
        )
        return

    # SQLite is a local convenience with no migration history; the schema is
    # derived directly from the models. Never used for a real deployment.
    import_models()
    Base.metadata.create_all(bind=bind)
    logger.info("Database ready (SQLite at %s).", urlsplit(target_url).path or ":memory:")

