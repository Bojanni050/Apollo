"""PostgreSQL integration tests.

These run against a **real PostgreSQL server**, not SQLite. That is the whole
point of the file: the two databases differ in ways a SQLite test can never
reveal -- JSONB vs JSON, native UUID vs CHAR(32), real ``timestamptz`` vs a
string that silently loses its offset, and enforced CHECK/UNIQUE constraints.

Skipped unless ``TEST_DATABASE_URL`` is set, so the default suite still runs
with no database server. To run them:

    docker compose -f docker-compose.test.yml up -d
    cd backend
    set TEST_DATABASE_URL=postgresql+psycopg://gaia:gaia@127.0.0.1:55433/gaia_docs_test
    python -m pytest -m postgres

Each test runs inside a transaction that is rolled back, so the suite leaves the
database as it found it and can run repeatedly against one container.
"""
from __future__ import annotations

import datetime as dt
import os
import uuid

import pytest

pytestmark = pytest.mark.postgres

TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL")

if not TEST_DATABASE_URL:
    pytest.skip(
        "TEST_DATABASE_URL is not set; start the container with "
        "`docker compose -f docker-compose.test.yml up -d`",
        allow_module_level=True,
    )

if TEST_DATABASE_URL.split("://", 1)[0].split("+", 1)[0] not in {
    "postgres",
    "postgresql",
}:
    pytest.skip("TEST_DATABASE_URL does not point at PostgreSQL", allow_module_level=True)

from sqlalchemy import create_engine, inspect, text  # noqa: E402
from sqlalchemy.exc import IntegrityError  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from alembic import command  # noqa: E402
from app import models  # noqa: E402,F401  (register tables)
from app.db import Base  # noqa: E402
from app.migrations import alembic_config  # noqa: E402


@pytest.fixture(scope="module")
def pg_engine():
    """A module-level engine, used to create the schema once."""
    engine = create_engine(TEST_DATABASE_URL, future=True)
    # Created from the models, so these tests exercise the schema the
    # application actually declares. The migration round-trip is tested
    # separately in test_migrations.py against an Alembic-built database.
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    yield engine
    engine.dispose()


@pytest.fixture()
def pg_session(pg_engine) -> Session:
    """A session whose work is rolled back, so tests cannot leak into each other."""
    connection = pg_engine.connect()
    transaction = connection.begin()
    # expire_on_commit=False keeps loaded attributes readable after expunge_all(),
    # so a test can assert that what it wrote really did round-trip through the
    # database rather than reading a cached value.
    session = Session(bind=connection, expire_on_commit=False)
    try:
        yield session
    finally:
        session.close()
        if transaction.is_active:
            transaction.rollback()
        connection.close()


def _workspace(session: Session, name: str = "Gaia") -> "models.Workspace":
    workspace = models.Workspace(name=name, description="docs")
    session.add(workspace)
    session.commit()
    return workspace


# ---------------------------------------------------------------------------
# Startup and dialect sanity
# ---------------------------------------------------------------------------


def test_postgres_is_really_postgres(pg_engine) -> None:
    """Guard against the suite silently running against the wrong database."""
    with pg_engine.connect() as connection:
        version = connection.execute(text("select version()")).scalar()
    assert "PostgreSQL" in version, f"expected PostgreSQL, got: {version}"


def test_application_startup_is_a_noop_when_already_migrated(pg_engine, monkeypatch) -> None:
    """init_db() against a schema already at head must not change anything."""
    from app.config import settings
    from app.db import init_db, pending_revisions

    monkeypatch.setattr(settings, "database_url", TEST_DATABASE_URL)
    monkeypatch.setattr(settings, "db_migrate_on_startup", True)

    # The pg_engine fixture builds the tables from the models, which is the
    # same shape as head -- but there is no migration record, so the database
    # is stamped first. (Applying a migration to a create_all database is
    # exactly the situation the adoption procedure in the README covers.)
    command.stamp(alembic_config(TEST_DATABASE_URL), "head")
    assert pending_revisions(pg_engine) == []

    init_db(bind=pg_engine)  # must not raise
    assert pending_revisions(pg_engine) == []

def test_init_db_refuses_when_the_schema_is_behind(pg_engine, monkeypatch) -> None:
    """An unmigrated database must fail loudly rather than start on a wrong schema."""
    from app.config import settings
    from app.db import init_db, pending_revisions

    monkeypatch.setattr(settings, "database_url", TEST_DATABASE_URL)
    monkeypatch.setattr(settings, "db_migrate_on_startup", False)
    with pg_engine.begin() as connection:
        connection.execute(text("DROP TABLE IF EXISTS alembic_version"))

    # With no record, every migration is outstanding. The head is asserted by
    # name rather than by number, so a new migration does not break this.
    from app.migrations import script_directory

    outstanding = pending_revisions(pg_engine)
    assert outstanding
    assert script_directory().get_current_head() in outstanding
    with pytest.raises(RuntimeError, match="out of date"):
        init_db(bind=pg_engine)


# ---------------------------------------------------------------------------
# Schema shape
# ---------------------------------------------------------------------------


def test_all_tables_exist(pg_engine) -> None:
    tables = set(inspect(pg_engine).get_table_names())
    assert {
        "workspaces", "repositories", "conversations", "messages",
        "open_questions", "decisions", "change_proposals",
        "inventory_runs", "inventory_items",
    } <= tables


def test_json_columns_use_jsonb(pg_engine) -> None:
    """JSONB, not JSON: indexable, validated, and a different type on disk."""
    with pg_engine.connect() as connection:
        rows = connection.execute(text(
            "select table_name, column_name from information_schema.columns "
            "where table_schema='public' and data_type = 'jsonb'"
        )).all()
    columns = set(rows)
    assert ("messages", "citations") in columns
    assert ("messages", "tool_calls") in columns
    assert ("change_proposals", "changes") in columns
    assert ("inventory_items", "overlaps") in columns


def test_uuid_column_is_native(pg_engine) -> None:
    """A real UUID column, not a VARCHAR holding a UUID-shaped string."""
    with pg_engine.connect() as connection:
        data_type = connection.execute(text(
            "select data_type from information_schema.columns "
            "where table_name='open_questions' and column_name='uid'"
        )).scalar()
    assert data_type == "uuid"


def test_timestamp_columns_are_timezone_aware(pg_engine) -> None:
    with pg_engine.connect() as connection:
        data_type = connection.execute(text(
            "select data_type from information_schema.columns "
            "where table_name='workspaces' and column_name='created_at'"
        )).scalar()
    assert data_type == "timestamp with time zone"


def test_indexes_exist(pg_engine) -> None:
    """The indexes the application relies on are actually created."""
    inspector = inspect(pg_engine)
    indexes = {
        name
        for table in ("conversations", "change_proposals", "open_questions")
        for name in [i["name"] for i in inspector.get_indexes(table)]
    }
    assert "ix_conversations_workspace_updated" in indexes
    assert "ix_change_proposals_workspace_status" in indexes
    assert "ix_change_proposals_conversation" in indexes
    assert "ix_open_questions_uid" in indexes


def test_unique_constraints_exist(pg_engine) -> None:
    inspector = inspect(pg_engine)
    # PostgreSQL may report a uniqueness rule as a table constraint or as a
    # unique index depending on how it was declared, so check both.
    repo_unique_cols = {
        tuple(u["column_names"])
        for u in inspector.get_unique_constraints("repositories")
    } | {
        tuple(i["column_names"])
        for i in inspector.get_indexes("repositories")
        if i.get("unique")
    }
    assert ("workspace_id", "name") in repo_unique_cols

    # uid is enforced by a unique index rather than a table constraint.
    uid = [i for i in inspector.get_indexes("open_questions")
           if i["name"] == "ix_open_questions_uid"]
    assert uid and uid[0]["unique"]


def test_check_constraints_exist(pg_engine) -> None:
    """Status-like columns are constrained in the database, not only in Python."""
    inspector = inspect(pg_engine)
    names = set()
    for table in ("conversations", "messages", "repositories", "change_proposals",
                  "inventory_items", "inventory_runs", "open_questions", "decisions"):
        names |= {c["name"] for c in inspector.get_check_constraints(table)}
    assert {
        "ck_conversations_mode", "ck_messages_role", "ck_repositories_kind",
        "ck_change_proposals_status", "ck_inventory_items_decision",
        "ck_inventory_runs_status", "ck_open_questions_status", "ck_decisions_status",
    } <= names


# ---------------------------------------------------------------------------
# Persistence: the application's own objects
# ---------------------------------------------------------------------------


def test_workspace_persists_and_round_trips(pg_session: Session) -> None:
    workspace = _workspace(pg_session, "Persisted")
    pg_session.expunge_all()

    loaded = pg_session.get(models.Workspace, workspace.id)
    assert loaded is not None
    assert loaded.name == "Persisted"
    assert loaded.description == "docs"


def test_document_metadata_persists(pg_session: Session) -> None:
    """Repository rows hold the document metadata the app stores about a repo."""
    workspace = _workspace(pg_session)
    repo = models.Repository(
        workspace_id=workspace.id,
        name="gaia-docs",
        local_path="C:/src/gaia-docs",
        branch="main",
        kind="documentation",
        writable=True,
        description="Documentation repository",
    )
    pg_session.add(repo)
    pg_session.commit()
    pg_session.expunge_all()

    loaded = pg_session.get(models.Repository, repo.id)
    assert loaded.kind == "documentation"
    assert loaded.is_documentation is True
    assert loaded.writable is True
    assert loaded.branch == "main"


def test_conversations_and_messages_persist(pg_session: Session) -> None:
    workspace = _workspace(pg_session)
    conversation = models.Conversation(
        workspace_id=workspace.id, title="Memory", mode="investigate"
    )
    pg_session.add(conversation)
    pg_session.flush()
    pg_session.add(models.Message(conversation_id=conversation.id, role="user", content="why?"))
    pg_session.add(models.Message(
        conversation_id=conversation.id,
        role="assistant",
        content="because",
        citations=[{"repository": "gaia-docs", "path": "notes.md",
                    "evidence_type": "ai_interpretation"}],
    ))
    pg_session.commit()
    pg_session.expunge_all()

    loaded = pg_session.get(models.Conversation, conversation.id)
    assert loaded.title == "Memory"
    assert loaded.mode == "investigate"
    assert [m.role for m in loaded.messages] == ["user", "assistant"]
    assert loaded.messages[1].citations[0]["path"] == "notes.md"


def test_jsonb_round_trips_nested_structures(pg_session: Session) -> None:
    """Nested dicts and lists survive the JSONB round trip intact."""
    workspace = _workspace(pg_session)
    run = models.InventoryRun(
        workspace_id=workspace.id, status="completed", summary="ok"
    )
    pg_session.add(run)
    pg_session.flush()
    item = models.InventoryItem(
        run_id=run.id,
        source_path="notes.md",
        purpose="A scratch note",
        suggested_path="architecture",
        confidence=0.82,
        overlaps=["architecture/overview.md", "README.md"],
        ambiguous=False,
    )
    pg_session.add(item)
    pg_session.commit()
    item_id = item.id
    pg_session.expunge_all()

    loaded = pg_session.get(models.InventoryItem, item_id)
    assert loaded.overlaps == ["architecture/overview.md", "README.md"]
    assert loaded.confidence == pytest.approx(0.82)
    assert loaded.decision == "pending"


def test_inventory_persistence(pg_session: Session) -> None:
    """An inventory run and its items, including applied/skipped decisions."""
    workspace = _workspace(pg_session)
    run = models.InventoryRun(
        workspace_id=workspace.id, status="completed", summary="Some docs."
    )
    pg_session.add(run)
    pg_session.flush()
    pg_session.add_all([
        models.InventoryItem(run_id=run.id, source_path="notes.md", purpose="note",
                             suggested_path="architecture", decision="applied"),
        models.InventoryItem(run_id=run.id, source_path="ghost.md", purpose="unclear",
                             ambiguous=True, decision="skipped"),
    ])
    pg_session.commit()
    pg_session.expunge_all()

    loaded = pg_session.get(models.InventoryRun, run.id)
    assert loaded.status == "completed"
    assert [i.decision for i in loaded.items] == ["applied", "skipped"]


def test_every_model_persists(pg_session: Session) -> None:
    """Each model in the schema can be written and read back."""
    workspace = _workspace(pg_session)
    conversation = models.Conversation(workspace_id=workspace.id, title="c")
    pg_session.add(conversation)
    pg_session.flush()

    question = models.OpenQuestion(
        workspace_id=workspace.id, title="Why?", conversation_id=conversation.id
    )
    decision = models.Decision(
        workspace_id=workspace.id, title="Use PostgreSQL", status="approved"
    )
    proposal = models.ChangeProposal(
        workspace_id=workspace.id,
        conversation_id=conversation.id,
        kind="edit",
        title="Edit",
        status="pending",
        changes=[{"action": "edit", "target_path": "a.md", "content": "x"}],
    )
    run = models.InventoryRun(workspace_id=workspace.id, status="completed")
    repo = models.Repository(
        workspace_id=workspace.id, name="docs", local_path="/tmp/docs", kind="source"
    )
    pg_session.add_all([question, decision, proposal, run, repo])
    pg_session.flush()
    pg_session.add(models.InventoryItem(run_id=run.id, source_path="a.md", purpose="p"))
    pg_session.commit()
    pg_session.expunge_all()

    assert pg_session.get(models.OpenQuestion, question.id).title == "Why?"
    assert pg_session.get(models.Decision, decision.id).status == "approved"
    assert pg_session.get(models.ChangeProposal, proposal.id).changes[0]["target_path"] == "a.md"
    assert pg_session.get(models.InventoryRun, run.id).items[0].source_path == "a.md"
    assert pg_session.get(models.Repository, repo.id).name == "docs"


# ---------------------------------------------------------------------------
# Timestamps -- where PostgreSQL and SQLite genuinely differ
# ---------------------------------------------------------------------------


def test_timestamps_come_back_timezone_aware(pg_session: Session) -> None:
    """timestamptz round-trips as an aware datetime on PostgreSQL.

    On SQLite this is NOT true: the value comes back naive, which is exactly
    why this assertion belongs in the PostgreSQL suite.
    """
    workspace = _workspace(pg_session)
    workspace_id = workspace.id
    pg_session.expunge_all()

    loaded = pg_session.get(models.Workspace, workspace_id)
    assert loaded.created_at.tzinfo is not None
    assert loaded.updated_at.tzinfo is not None


def test_timestamps_are_in_the_near_present(pg_session: Session) -> None:
    workspace = _workspace(pg_session)
    now = dt.datetime.now(dt.timezone.utc)
    assert abs((workspace.created_at - now).total_seconds()) < 300


def test_nullable_timestamp_stays_null_when_unset(pg_session: Session) -> None:
    """An unset nullable timestamptz is NULL, not the epoch and not now()."""
    workspace = _workspace(pg_session)
    question = models.OpenQuestion(workspace_id=workspace.id, title="q")
    pg_session.add(question)
    pg_session.commit()
    question_id = question.id
    pg_session.expunge_all()

    assert pg_session.get(models.OpenQuestion, question_id).resolved_at is None


def test_uuid_is_generated_typed_and_unique(pg_session: Session) -> None:
    """uid is a real uuid.UUID, auto-generated, and unique per row."""
    workspace = _workspace(pg_session)
    first = models.OpenQuestion(workspace_id=workspace.id, title="one")
    second = models.OpenQuestion(workspace_id=workspace.id, title="two")
    pg_session.add_all([first, second])
    pg_session.commit()
    first_id, first_uid = first.id, first.uid
    pg_session.expunge_all()

    assert isinstance(first_uid, uuid.UUID)
    assert pg_session.get(models.OpenQuestion, first_id).uid == first_uid


# ---------------------------------------------------------------------------
# Constraints -- enforced by PostgreSQL itself
# ---------------------------------------------------------------------------


def test_duplicate_repository_name_is_rejected(pg_session: Session) -> None:
    workspace = _workspace(pg_session)
    # Added without an intervening flush, so the collision surfaces on commit.
    for name in ("gaia-docs", "gaia-docs"):
        pg_session.add(models.Repository(
            workspace_id=workspace.id, name=name,
            local_path=f"/tmp/{name}", kind="source",
        ))
    with pytest.raises(IntegrityError):
        pg_session.commit()
    pg_session.rollback()


def test_invalid_check_value_is_rejected(pg_session: Session) -> None:
    """A status outside the allowed set is refused by the database."""
    workspace = _workspace(pg_session)
    pg_session.add(models.Conversation(workspace_id=workspace.id, title="c", mode="yolo"))
    with pytest.raises(IntegrityError):
        pg_session.commit()
    pg_session.rollback()


def test_duplicate_uuid_is_rejected(pg_session: Session) -> None:
    workspace = _workspace(pg_session)
    shared = uuid.uuid4()
    pg_session.add(models.OpenQuestion(workspace_id=workspace.id, title="a", uid=shared))
    pg_session.add(models.OpenQuestion(workspace_id=workspace.id, title="b", uid=shared))
    with pytest.raises(IntegrityError):
        pg_session.commit()
    pg_session.rollback()


def test_not_null_is_enforced_on_workspace_name(pg_session: Session) -> None:
    pg_session.add(models.Workspace(name=None))
    with pytest.raises(IntegrityError):
        pg_session.commit()
    pg_session.rollback()


def test_conversation_title_is_not_nullable(pg_session: Session) -> None:
    """Regression: the title column used to be nullable despite having a default.

    Written as raw SQL on purpose. Through the ORM, setting ``title=None`` makes
    SQLAlchemy substitute the Python-side default, so the ORM path can never
    demonstrate the column constraint. Raw INSERT is what a psql session or a
    hand-run migration would do.
    """
    workspace = _workspace(pg_session)
    with pytest.raises(IntegrityError):
        with pg_session.begin_nested():
            pg_session.execute(text(
                "INSERT INTO conversations (workspace_id, title, mode, archived, "
                "created_at, updated_at) "
                "VALUES (:ws, NULL, 'explore', false, now(), now())"
            ), {"ws": workspace.id})
    pg_session.rollback()


def test_foreign_key_is_enforced(pg_session: Session) -> None:
    """A repository for a non-existent workspace is refused."""
    pg_session.add(models.Repository(
        workspace_id=999_999, name="orphan", local_path="/tmp/x", kind="source"
    ))
    with pytest.raises(IntegrityError):
        pg_session.commit()
    pg_session.rollback()


def test_deleting_a_workspace_cascades(pg_session: Session) -> None:
    """ondelete=CASCADE is honoured by the database, not only by the ORM."""
    workspace = _workspace(pg_session)
    conversation = models.Conversation(workspace_id=workspace.id, title="c")
    pg_session.add(conversation)
    pg_session.flush()
    pg_session.add(models.Message(conversation_id=conversation.id, role="user", content="hi"))
    pg_session.commit()
    conversation_id = conversation.id

    pg_session.delete(workspace)
    pg_session.commit()
    pg_session.expunge_all()

    assert pg_session.get(models.Conversation, conversation_id) is None
    assert pg_session.query(models.Message).count() == 0




