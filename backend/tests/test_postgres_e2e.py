"""PostgreSQL end-to-end verification tests.

This test suite bridges the critical test gap identified in the audit:
    "The application has not been tested end-to-end against a PostgreSQL database
    whose schema was created by the actual Alembic migrations."

Guarantees verified here:
1. A fresh PostgreSQL database schema is created EXCLUSIVELY by `alembic upgrade head`.
   `Base.metadata.create_all()` is forbidden and intercepted.
2. The real FastAPI application starts against the migrated database (through
   the real `lifespan` context manager and `init_db()`) without modifying the schema.
3. Realistic application flows (Workspace CRUD, Document registration & tree/content/search,
   Conversations & Message ordering with JSONB citations/tool calls, Inventory runs & items
   with approval/skip, and Proposals) execute successfully against the migrated database.
4. All 9 domain models in the migration chain persist and round-trip successfully.
5. PostgreSQL-specific features (native UUID, JSONB decomposition, timezone-aware
   timestamps, CHECK constraints, UNIQUE constraints, FOREIGN KEY cascade & SET NULL)
   behave correctly at the database and application levels.
6. Migration integrity: downgrade/upgrade cycle, idempotence of `alembic upgrade head`,
   and safe backfill of existing data during migration upgrades.

Skipped cleanly with an explicit message if TEST_DATABASE_URL is not configured
or does not point to PostgreSQL. Never silently substitutes SQLite.
"""
from __future__ import annotations

import datetime as dt
import json
from pathlib import Path
from typing import Any
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import Engine, make_url
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

pytestmark = pytest.mark.postgres

import os

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
    pytest.skip(
        "TEST_DATABASE_URL does not point at PostgreSQL",
        allow_module_level=True,
    )

from alembic import command
import app.db
from app import models
from app.config import settings
from app.db import Base, build_engine, init_db, pending_revisions, session_factory
from app.llm.base import LLMResponse, ToolCall
from app.main import create_app
from app.migrations import alembic_config, script_directory
from tests.test_chat_agent import ScriptedProvider

E2E_DB_NAME = "gaia_docs_e2e"
E2E_DATABASE_URL = make_url(TEST_DATABASE_URL).set(
    database=E2E_DB_NAME
).render_as_string(hide_password=False)
ADMIN_URL = make_url(TEST_DATABASE_URL).set(
    database="postgres"
).render_as_string(hide_password=False)

EXPECTED_TABLES = frozenset({
    "workspaces",
    "repositories",
    "conversations",
    "messages",
    "open_questions",
    "decisions",
    "change_proposals",
    "inventory_runs",
    "inventory_items",
})


def _drop_and_create_e2e_db(db_name: str = E2E_DB_NAME) -> None:
    """Ensure a completely empty PostgreSQL database.

    Terminates any active backends to prevent 'database is being accessed by other users'
    errors before dropping.
    """
    engine = create_engine(ADMIN_URL, isolation_level="AUTOCOMMIT")
    try:
        with engine.connect() as conn:
            conn.execute(
                text(
                    f"""
                    SELECT pg_terminate_backend(pid)
                    FROM pg_stat_activity
                    WHERE datname = '{db_name}' AND pid <> pg_backend_pid();
                    """
                )
            )
            conn.execute(text(f'DROP DATABASE IF EXISTS "{db_name}"'))
            conn.execute(text(f'CREATE DATABASE "{db_name}"'))
    finally:
        engine.dispose()


@pytest.fixture(scope="module")
def fresh_migrated_db():
    """Create a completely fresh PostgreSQL database and migrate it exclusively via Alembic.

    Asserts that Base.metadata.create_all is NEVER called during creation.
    """
    _drop_and_create_e2e_db(E2E_DB_NAME)

    # Strictly disallow create_all during the migration phase
    def _forbidden_create_all(*args, **kwargs):
        raise AssertionError("Base.metadata.create_all() must not be called!")

    original_create_all = Base.metadata.create_all
    Base.metadata.create_all = _forbidden_create_all
    try:
        cfg = alembic_config(E2E_DATABASE_URL)
        command.upgrade(cfg, "head")
    finally:
        Base.metadata.create_all = original_create_all

    engine = build_engine(E2E_DATABASE_URL)
    yield engine
    engine.dispose()


@pytest.fixture()
def e2e_session(fresh_migrated_db: Engine):
    """Direct ORM session to the migrated PostgreSQL DB, with transaction isolation."""
    connection = fresh_migrated_db.connect()
    transaction = connection.begin()
    session = Session(bind=connection, expire_on_commit=False)
    try:
        yield session
    finally:
        session.close()
        if transaction.is_active:
            transaction.rollback()
        connection.close()


@pytest.fixture()
def e2e_client(fresh_migrated_db: Engine, monkeypatch: pytest.MonkeyPatch):
    """FastAPI TestClient wired to the migrated PostgreSQL database.

    Runs through the actual application lifespan (init_db) while verifying
    that create_all() is never called by the application.
    """
    # Guard against create_all: the app must rely exclusively on Alembic
    def _forbidden_create_all(*args, **kwargs):
        raise AssertionError(
            "Base.metadata.create_all() was invoked during application runtime! "
            "Schema must be managed exclusively by Alembic migrations."
        )

    monkeypatch.setattr(Base.metadata, "create_all", _forbidden_create_all)

    # Point engine and session factory to the migrated PostgreSQL DB
    monkeypatch.setattr(app.db, "engine", fresh_migrated_db)
    monkeypatch.setattr(app.db, "SessionLocal", session_factory(fresh_migrated_db))

    # Configure application settings for e2e testing
    monkeypatch.setattr(settings, "database_url", E2E_DATABASE_URL)
    monkeypatch.setattr(settings, "app_env", "development")
    monkeypatch.setattr(settings, "auth_enabled", False)
    monkeypatch.setattr(settings, "allow_unrestricted_workspace_roots", True)
    monkeypatch.setattr(settings, "db_migrate_on_startup", False)

    application = create_app(settings)
    application.dependency_overrides.clear()

    # 'with TestClient' executes the lifespan (settings.validate_security + init_db)
    with TestClient(application) as client:
        yield client
        application.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# 1. Fresh Database & Migration Schema Verification
# ---------------------------------------------------------------------------


def test_fresh_database_schema_created_exclusively_by_alembic(
    fresh_migrated_db: Engine,
) -> None:
    """Requirement 2: Fresh database created exclusively through `alembic upgrade head`.

    Verifies tables, columns, types, indexes, and constraints exist as defined by migrations.
    """
    inspector = inspect(fresh_migrated_db)
    tables = set(inspector.get_table_names())

    assert EXPECTED_TABLES <= tables
    assert "alembic_version" in tables

    # Current head revision is recorded
    with fresh_migrated_db.connect() as conn:
        current_rev = conn.execute(
            text("SELECT version_num FROM alembic_version")
        ).scalar()
    assert current_rev == script_directory().get_current_head()

    # Column count and names match Base.metadata exactly
    migrated_tables = tables - {"alembic_version"}
    for table_name in EXPECTED_TABLES:
        assert table_name in migrated_tables
        actual_cols = {c["name"] for c in inspector.get_columns(table_name)}
        expected_cols = set(Base.metadata.tables[table_name].columns.keys())
        assert actual_cols == expected_cols, f"Mismatch in columns for table {table_name}"


def test_migrated_schema_uses_true_postgresql_data_types(
    fresh_migrated_db: Engine,
) -> None:
    """Requirement 5: Verify PostgreSQL-specific data types produced by migrations."""
    with fresh_migrated_db.connect() as conn:
        jsonb_cols = set(
            conn.execute(
                text(
                    """
                    SELECT table_name, column_name
                    FROM information_schema.columns
                    WHERE table_schema = 'public' AND data_type = 'jsonb'
                    """
                )
            ).all()
        )
        uuid_type = conn.execute(
            text(
                """
                SELECT data_type
                FROM information_schema.columns
                WHERE table_name = 'open_questions' AND column_name = 'uid'
                """
            )
        ).scalar()
        tz_type = conn.execute(
            text(
                """
                SELECT data_type
                FROM information_schema.columns
                WHERE table_name = 'workspaces' AND column_name = 'created_at'
                """
            )
        ).scalar()

    assert ("messages", "citations") in jsonb_cols
    assert ("messages", "tool_calls") in jsonb_cols
    assert ("open_questions", "evidence") in jsonb_cols
    assert ("open_questions", "affected") in jsonb_cols
    assert ("decisions", "related_documents") in jsonb_cols
    assert ("change_proposals", "changes") in jsonb_cols
    assert ("inventory_items", "overlaps") in jsonb_cols
    assert ("inventory_items", "alternatives") in jsonb_cols

    assert uuid_type == "uuid"
    assert tz_type == "timestamp with time zone"


def test_migrated_schema_check_constraints_and_indexes(
    fresh_migrated_db: Engine,
) -> None:
    """Requirement 5: Enforced check constraints and indexes from migrations."""
    inspector = inspect(fresh_migrated_db)

    all_checks = set()
    for table in (
        "conversations",
        "messages",
        "repositories",
        "change_proposals",
        "inventory_items",
        "inventory_runs",
        "open_questions",
        "decisions",
    ):
        all_checks |= {c["name"] for c in inspector.get_check_constraints(table)}

    expected_checks = {
        "ck_conversations_mode",
        "ck_messages_role",
        "ck_repositories_kind",
        "ck_change_proposals_status",
        "ck_inventory_items_decision",
        "ck_inventory_runs_status",
        "ck_open_questions_status",
        "ck_decisions_status",
    }
    assert expected_checks <= all_checks

    # Indexes
    conv_indexes = {i["name"] for i in inspector.get_indexes("conversations")}
    assert "ix_conversations_workspace_updated" in conv_indexes

    prop_indexes = {i["name"] for i in inspector.get_indexes("change_proposals")}
    assert "ix_change_proposals_workspace_status" in prop_indexes
    assert "ix_change_proposals_conversation" in prop_indexes

    question_indexes = {i["name"]: i for i in inspector.get_indexes("open_questions")}
    assert "ix_open_questions_uid" in question_indexes
    assert question_indexes["ix_open_questions_uid"]["unique"] is True


# ---------------------------------------------------------------------------
# 2. Application Startup Against Migrated Database
# ---------------------------------------------------------------------------


def test_application_startup_lifespan_against_migrated_db(
    e2e_client: TestClient, fresh_migrated_db: Engine
) -> None:
    """Requirement 3: Verify the application starts and runs healthcheck.

    Lifespan executes init_db(), which discovers the schema is up to date
    and does not call create_all().
    """
    assert pending_revisions(fresh_migrated_db) == []
    response = e2e_client.get("/api/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_init_db_refuses_startup_if_schema_is_behind(
    fresh_migrated_db: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Startup must fail loudly if an unmigrated PostgreSQL DB is encountered."""
    monkeypatch.setattr(settings, "database_url", E2E_DATABASE_URL)
    monkeypatch.setattr(settings, "db_migrate_on_startup", False)

    # Simulate missing migration by dropping alembic_version temporarily
    with fresh_migrated_db.begin() as conn:
        conn.execute(text("DROP TABLE alembic_version"))

    try:
        assert pending_revisions(fresh_migrated_db) != []
        with pytest.raises(RuntimeError, match="The database schema is out of date"):
            init_db(bind=fresh_migrated_db)
    finally:
        # Restore stamp
        command.stamp(alembic_config(E2E_DATABASE_URL), "head")


# ---------------------------------------------------------------------------
# 3. Realistic Application Flows via FastAPI TestClient
# ---------------------------------------------------------------------------


def test_e2e_workspace_flow(e2e_client: TestClient) -> None:
    """Requirement 4: Workspace CRUD against migrated PostgreSQL DB."""
    # 1. Create workspace
    create_resp = e2e_client.post(
        "/api/workspaces",
        json={"name": "Postgres E2E Workspace", "description": "Verification test"},
    )
    assert create_resp.status_code == 201, create_resp.text
    ws = create_resp.json()
    ws_id = ws["id"]
    assert ws["name"] == "Postgres E2E Workspace"
    assert ws["description"] == "Verification test"
    assert ws["created_at"] is not None

    # 2. Retrieve workspace
    read_resp = e2e_client.get(f"/api/workspaces/{ws_id}")
    assert read_resp.status_code == 200
    assert read_resp.json()["name"] == "Postgres E2E Workspace"

    # 3. List workspaces
    list_resp = e2e_client.get("/api/workspaces")
    assert list_resp.status_code == 200
    assert any(w["id"] == ws_id for w in list_resp.json())

    # 4. Update workspace
    patch_resp = e2e_client.patch(
        f"/api/workspaces/{ws_id}",
        json={"name": "Updated E2E Workspace", "description": "Updated description"},
    )
    assert patch_resp.status_code == 200
    assert patch_resp.json()["name"] == "Updated E2E Workspace"
    assert patch_resp.json()["description"] == "Updated description"

    # Cleanup
    del_resp = e2e_client.delete(f"/api/workspaces/{ws_id}")
    assert del_resp.status_code == 204


def test_e2e_documents_and_repositories_flow(
    e2e_client: TestClient, doc_repo: Path, source_repo: Path
) -> None:
    """Requirement 4: Document repository registration, metadata indexing, tree, and search."""
    # Create workspace
    ws = e2e_client.post(
        "/api/workspaces", json={"name": "Docs Flow Workspace"}
    ).json()
    ws_id = ws["id"]

    try:
        # Register documentation repository
        doc_resp = e2e_client.post(
            f"/api/workspaces/{ws_id}/repositories",
            json={
                "name": "gaia-docs",
                "local_path": str(doc_repo),
                "branch": "main",
                "kind": "documentation",
                "writable": True,
                "description": "Primary docs",
            },
        )
        assert doc_resp.status_code == 201, doc_resp.text
        doc_repo_data = doc_resp.json()
        doc_repo_id = doc_repo_data["id"]
        assert doc_repo_data["is_git_repo"] is True
        assert doc_repo_data["writable"] is True

        # Register source repository
        src_resp = e2e_client.post(
            f"/api/workspaces/{ws_id}/repositories",
            json={
                "name": "gaia-service",
                "local_path": str(source_repo),
                "branch": "main",
                "kind": "source",
                "writable": False,
            },
        )
        assert src_resp.status_code == 201
        src_repo_id = src_resp.json()["id"]

        # Retrieve document tree
        tree_resp = e2e_client.get(
            f"/api/workspaces/{ws_id}/repositories/{doc_repo_id}/tree"
        )
        assert tree_resp.status_code == 200
        tree = tree_resp.json()
        assert tree["repository"] == "gaia-docs"
        assert tree["root"]["children"]

        # Retrieve document content & metadata
        doc_content_resp = e2e_client.get(
            f"/api/workspaces/{ws_id}/repositories/{doc_repo_id}/document",
            params={"path": "architecture/overview.md"},
        )
        assert doc_content_resp.status_code == 200
        doc_data = doc_content_resp.json()
        assert doc_data["title"] == "Architecture overview"
        assert "split into services and components" in doc_data["raw_markdown"]

        # Search documents
        search_resp = e2e_client.get(
            f"/api/workspaces/{ws_id}/repositories/{doc_repo_id}/search",
            params={"q": "Memory"},
        )
        assert search_resp.status_code == 200
        hits = search_resp.json()["hits"]
        assert len(hits) >= 1
        assert any("memory.md" in h["path"] for h in hits)

        # Check git changes
        git_resp = e2e_client.get(
            f"/api/workspaces/{ws_id}/repositories/{doc_repo_id}/git"
        )
        assert git_resp.status_code == 200
        assert git_resp.json()["branch"] == "main"

        # Update repository metadata
        patch_repo = e2e_client.patch(
            f"/api/workspaces/{ws_id}/repositories/{doc_repo_id}",
            json={"description": "Updated primary docs"},
        )
        assert patch_repo.status_code == 200
        assert patch_repo.json()["description"] == "Updated primary docs"

        # Unique constraint on (workspace_id, name)
        dup_resp = e2e_client.post(
            f"/api/workspaces/{ws_id}/repositories",
            json={
                "name": "gaia-docs",
                "local_path": str(doc_repo),
                "kind": "source",
            },
        )
        assert dup_resp.status_code == 409
    finally:
        e2e_client.delete(f"/api/workspaces/{ws_id}")


def test_e2e_conversations_and_messages_ordering(
    e2e_client: TestClient,
    doc_repo: Path,
    monkeypatch: pytest.MonkeyPatch,
    fresh_migrated_db: Engine,
) -> None:
    """Requirement 4: Create conversation, store messages, verify message ordering and JSONB round-trip."""
    ws = e2e_client.post(
        "/api/workspaces", json={"name": "Chat Flow Workspace"}
    ).json()
    ws_id = ws["id"]

    try:
        e2e_client.post(
            f"/api/workspaces/{ws_id}/repositories",
            json={
                "name": "gaia-docs",
                "local_path": str(doc_repo),
                "kind": "documentation",
                "writable": True,
            },
        )

        # 1. Create conversation
        conv_resp = e2e_client.post(
            f"/api/workspaces/{ws_id}/conversations",
            json={"title": "Architecture Exploration", "mode": "explore"},
        )
        assert conv_resp.status_code == 201
        conv_id = conv_resp.json()["id"]

        # 2. Update conversation mode
        patch_resp = e2e_client.patch(
            f"/api/workspaces/{ws_id}/conversations/{conv_id}",
            json={"mode": "investigate"},
        )
        assert patch_resp.status_code == 200
        assert patch_resp.json()["mode"] == "investigate"

        # 3. Send message with mock LLM (testing agent execution, tool calling, and message storage)
        turn1_tool = LLMResponse(
            content="",
            tool_calls=[
                ToolCall(
                    id="call_1",
                    name="read_document",
                    arguments={"repository": "gaia-docs", "path": "architecture/components/memory.md"},
                )
            ],
        )
        turn1_answer = LLMResponse(
            content="The memory component stores context and embeddings.",
        )
        provider = ScriptedProvider([turn1_tool, turn1_answer])
        monkeypatch.setattr("app.api.routes_chat.get_provider", lambda: provider)

        send_resp = e2e_client.post(
            f"/api/workspaces/{ws_id}/conversations/{conv_id}/messages",
            json={"content": "How does memory work?"},
        )
        assert send_resp.status_code == 201
        send_data = send_resp.json()
        assert send_data["user_message"]["content"] == "How does memory work?"
        assert "memory component stores" in send_data["assistant_message"]["content"]

        # Send second turn
        second_reply = LLMResponse(content="Yes, SQLite is used per ADR 001.")
        provider2 = ScriptedProvider([second_reply])
        monkeypatch.setattr("app.api.routes_chat.get_provider", lambda: provider2)

        send_resp2 = e2e_client.post(
            f"/api/workspaces/{ws_id}/conversations/{conv_id}/messages",
            json={"content": "What database is used?"},
        )
        assert send_resp2.status_code == 201

        # 4. Retrieve conversation and verify chronological ordering and JSONB structure
        detail_resp = e2e_client.get(
            f"/api/workspaces/{ws_id}/conversations/{conv_id}"
        )
        assert detail_resp.status_code == 200
        detail = detail_resp.json()
        assert detail["message_count"] == 4
        messages = detail["messages"]

        assert messages[0]["role"] == "user"
        assert messages[0]["content"] == "How does memory work?"
        assert messages[1]["role"] == "assistant"
        assert "memory component stores" in messages[1]["content"]
        # Verify JSONB citations round-trip intact through API
        assert len(messages[1]["citations"]) == 1
        assert (
            messages[1]["citations"][0]["path"]
            == "architecture/components/memory.md"
        )
        assert (
            messages[1]["citations"][0]["evidence_type"]
            == "documented_intention"
        )

        # Verify JSONB tool_calls persisted in PostgreSQL database
        with fresh_migrated_db.connect() as conn:
            db_tool_calls = conn.execute(
                text("SELECT tool_calls FROM messages WHERE id = :id"),
                {"id": messages[1]["id"]},
            ).scalar()
            assert len(db_tool_calls) == 1
            assert db_tool_calls[0]["tool"] == "read_document"
            assert "architecture/components/memory.md" in db_tool_calls[0]["arguments"]["path"]

        assert messages[2]["role"] == "user"
        assert messages[2]["content"] == "What database is used?"
        assert messages[3]["role"] == "assistant"
        assert "SQLite is used" in messages[3]["content"]

        # Verify ordering IDs strictly ascend
        msg_ids = [m["id"] for m in messages]
        assert msg_ids == sorted(msg_ids)
    finally:
        e2e_client.delete(f"/api/workspaces/{ws_id}")


def test_e2e_inventory_job_and_results_flow(
    e2e_client: TestClient,
    doc_repo: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Requirement 4: Create inventory run, update state, persist results, and retrieve."""
    ws = e2e_client.post(
        "/api/workspaces", json={"name": "Inventory Flow Workspace"}
    ).json()
    ws_id = ws["id"]

    try:
        e2e_client.post(
            f"/api/workspaces/{ws_id}/repositories",
            json={
                "name": "gaia-docs",
                "local_path": str(doc_repo),
                "kind": "documentation",
                "writable": True,
            },
        )

        inventory_json = json.dumps(
            {
                "classifications": [
                    {
                        "path": "notes.md",
                        "purpose": "Component notes for memory",
                        "suggested_path": "architecture/components",
                        "confidence": 0.88,
                        "overlaps": ["architecture/components/memory.md"],
                        "ambiguous": False,
                        "alternatives": ["foundation", "development"],
                        "reason": "Mentions memory architecture component",
                        "partial": False,
                    },
                    {
                        "path": "README.md",
                        "purpose": "Root documentation",
                        "suggested_path": "foundation",
                        "confidence": 0.95,
                        "overlaps": [],
                        "ambiguous": False,
                        "alternatives": [],
                        "reason": "Root documentation index",
                        "partial": False,
                    },
                ],
                "summary": "Classified 2 documentation files.",
            }
        )
        provider = ScriptedProvider([LLMResponse(content=inventory_json)])
        monkeypatch.setattr(
            "app.api.routes_inventory.get_provider", lambda: provider
        )

        # 1. Create inventory run
        run_resp = e2e_client.post(
            f"/api/workspaces/{ws_id}/inventory/runs", json={}
        )
        assert run_resp.status_code == 201, run_resp.text
        run = run_resp.json()
        run_id = run["id"]
        assert run["status"] == "completed"
        assert run["summary"] == "Classified 2 documentation files."
        assert len(run["items"]) == 6

        notes_item = next(
            i for i in run["items"] if i["source_path"] == "notes.md"
        )
        readme_item = next(
            i for i in run["items"] if i["source_path"] == "README.md"
        )
        assert notes_item["confidence"] == pytest.approx(0.88)
        assert notes_item["alternatives"] == ["foundation", "development"]
        assert notes_item["decision"] == "pending"

        # 2. Skip one item
        skip_resp = e2e_client.post(
            f"/api/workspaces/{ws_id}/inventory/runs/{run_id}/items/{readme_item['id']}/skip"
        )
        assert skip_resp.status_code == 200
        assert skip_resp.json()["item"]["decision"] == "skipped"

        # 3. Apply the other item (moves notes.md to architecture/components/notes.md)
        apply_resp = e2e_client.post(
            f"/api/workspaces/{ws_id}/inventory/runs/{run_id}/items/{notes_item['id']}/apply"
        )
        assert apply_resp.status_code == 200
        assert apply_resp.json()["item"]["decision"] == "applied"
        assert (doc_repo / "architecture" / "components" / "notes.md").exists()

        # 4. Retrieve results and verify persisted state in PostgreSQL
        get_run_resp = e2e_client.get(
            f"/api/workspaces/{ws_id}/inventory/runs/{run_id}"
        )
        assert get_run_resp.status_code == 200
        fetched_run = get_run_resp.json()
        decisions = {i["source_path"]: i["decision"] for i in fetched_run["items"]}
        assert decisions["notes.md"] == "applied"
        assert decisions["README.md"] == "skipped"

        # List runs
        list_runs_resp = e2e_client.get(
            f"/api/workspaces/{ws_id}/inventory/runs"
        )
        assert list_runs_resp.status_code == 200
        assert any(r["id"] == run_id for r in list_runs_resp.json())
    finally:
        e2e_client.delete(f"/api/workspaces/{ws_id}")


def test_e2e_proposals_lifecycle(
    e2e_client: TestClient, doc_repo: Path
) -> None:
    """Requirement 4: ChangeProposal planning, inspection, acceptance and decision timestamp."""
    ws = e2e_client.post(
        "/api/workspaces", json={"name": "Proposal Flow Workspace"}
    ).json()
    ws_id = ws["id"]

    try:
        repo_resp = e2e_client.post(
            f"/api/workspaces/{ws_id}/repositories",
            json={
                "name": "gaia-docs",
                "local_path": str(doc_repo),
                "kind": "documentation",
                "writable": True,
            },
        )
        repo_id = repo_resp.json()["id"]

        # 1. Plan edit proposal
        plan_resp = e2e_client.post(
            f"/api/workspaces/{ws_id}/proposals/edit",
            json={
                "repository_id": repo_id,
                "path": "foundation/principles.md",
                "new_content": "# Principles\n\nWe optimise for local-first operation and privacy.\n",
                "reason": "Add privacy principle",
            },
        )
        assert plan_resp.status_code == 201, plan_resp.text
        proposal = plan_resp.json()
        prop_id = proposal["id"]
        assert proposal["kind"] == "edit"
        assert proposal["status"] == "pending"
        assert proposal["changes"][0]["target_path"] == "foundation/principles.md"
        assert "privacy" in proposal["diff"]

        # File is unchanged prior to acceptance
        assert "privacy" not in (
            doc_repo / "foundation" / "principles.md"
        ).read_text(encoding="utf-8")

        # 2. Retrieve proposal
        get_resp = e2e_client.get(
            f"/api/workspaces/{ws_id}/proposals/{prop_id}"
        )
        assert get_resp.status_code == 200
        assert get_resp.json()["status"] == "pending"

        # 3. Accept proposal
        accept_resp = e2e_client.post(
            f"/api/workspaces/{ws_id}/proposals/{prop_id}/accept"
        )
        assert accept_resp.status_code == 200
        accepted_data = accept_resp.json()
        assert accepted_data["proposal"]["status"] == "accepted"
        assert accepted_data["proposal"]["decided_at"] is not None

        # File is now modified on disk
        assert "privacy" in (
            doc_repo / "foundation" / "principles.md"
        ).read_text(encoding="utf-8")
    finally:
        e2e_client.delete(f"/api/workspaces/{ws_id}")


def test_e2e_open_questions_and_decisions_flow(
    e2e_client: TestClient,
    doc_repo: Path,
    fresh_migrated_db: Engine,
) -> None:
    """Requirement 4 & 5: Verify OpenQuestions and Decisions REST APIs end-to-end against migrated PostgreSQL."""
    ws = e2e_client.post(
        "/api/workspaces", json={"name": "Questions & Decisions E2E Workspace"}
    ).json()
    ws_id = ws["id"]

    try:
        # Register doc repo
        e2e_client.post(
            f"/api/workspaces/{ws_id}/repositories",
            json={
                "name": "gaia-docs",
                "local_path": str(doc_repo),
                "kind": "documentation",
                "writable": True,
            },
        )

        # 1. Create an OpenQuestion via REST API
        q_create_resp = e2e_client.post(
            f"/api/workspaces/{ws_id}/questions",
            json={
                "title": "Which caching strategy to adopt for document trees?",
                "description": "Evaluating Redis vs local in-process cache",
                "status": "open",
                "evidence": [{"source": "perf.md", "note": "tree fetch takes 40ms"}],
                "affected": ["architecture/components/tree.md"],
            },
        )
        assert q_create_resp.status_code == 201, q_create_resp.text
        q_data = q_create_resp.json()
        q_id = q_data["id"]
        q_uid = q_data["uid"]
        assert q_data["status"] == "open"
        assert uuid.UUID(q_uid)

        # Verify in PostgreSQL: uid is real UUID, evidence is JSONB
        with fresh_migrated_db.connect() as conn:
            row = conn.execute(
                text("SELECT uid, evidence, affected, status FROM open_questions WHERE id = :id"),
                {"id": q_id},
            ).one()
            assert str(row.uid) == q_uid
            assert len(row.evidence) == 1
            assert row.evidence[0]["note"] == "tree fetch takes 40ms"
            assert row.affected == ["architecture/components/tree.md"]
            assert row.status == "open"

        # 2. Create conversation referencing this OpenQuestion
        conv_resp = e2e_client.post(
            f"/api/workspaces/{ws_id}/conversations",
            json={"title": "Caching Debate", "question_id": q_id},
        )
        assert conv_resp.status_code == 201
        conv_id = conv_resp.json()["id"]

        # 3. Update OpenQuestion to resolved with resolution and link conversation
        q_patch_resp = e2e_client.patch(
            f"/api/workspaces/{ws_id}/questions/{q_id}",
            json={
                "status": "resolved",
                "resolution": "Adopt in-process LRU cache with file watcher invalidation.",
                "conversation_id": conv_id,
            },
        )
        assert q_patch_resp.status_code == 200
        q_updated = q_patch_resp.json()
        assert q_updated["status"] == "resolved"
        assert q_updated["resolved_at"] is not None
        assert q_updated["conversation_id"] == conv_id

        # 4. Create Decision referencing the OpenQuestion and valid document
        d_create_resp = e2e_client.post(
            f"/api/workspaces/{ws_id}/decisions",
            json={
                "title": "ADR 002: In-Process LRU Cache for Tree Index",
                "context": "Document tree queries are frequent on sidebar refresh.",
                "decision": "Use an in-process LRU cache keyed by head revision.",
                "rationale": "Zero network latency, minimal memory overhead for typical workspaces.",
                "consequences": "Multiple backend workers do not share cache entries.",
                "status": "approved",
                "related_documents": ["architecture/overview.md"],
                "related_questions": [q_id, q_uid],
            },
        )
        assert d_create_resp.status_code == 201, d_create_resp.text
        d_data = d_create_resp.json()
        d_id = d_data["id"]
        assert d_data["status"] == "approved"
        assert d_data["approved_at"] is not None
        assert d_data["decided_on"] is not None
        assert d_data["related_documents"] == ["architecture/overview.md"]
        assert d_data["related_questions"] == [q_id, q_uid]

        # Verify in PostgreSQL: JSONB arrays and timestamps
        with fresh_migrated_db.connect() as conn:
            d_row = conn.execute(
                text(
                    "SELECT title, status, approved_at, related_documents, related_questions "
                    "FROM decisions WHERE id = :id"
                ),
                {"id": d_id},
            ).one()
            assert d_row.title == "ADR 002: In-Process LRU Cache for Tree Index"
            assert d_row.status == "approved"
            assert d_row.approved_at is not None
            assert d_row.related_documents == ["architecture/overview.md"]
            assert d_row.related_questions == [q_id, q_uid]

        # 5. Explicitly approve the Decision and verify durable ADR generation
        approve_resp = e2e_client.post(f"/api/workspaces/{ws_id}/decisions/{d_id}/approve")
        assert approve_resp.status_code == 200
        approve_data = approve_resp.json()
        assert approve_data["approved"] is True
        assert approve_data["sync_status"] == "created"
        assert approve_data["markdown_path"] is not None
        assert (doc_repo / approve_data["markdown_path"]).exists()

        # 6. List and filter decisions
        d_list_resp = e2e_client.get(f"/api/workspaces/{ws_id}/decisions?status=approved")
        assert d_list_resp.status_code == 200
        assert any(d["id"] == d_id for d in d_list_resp.json())

        # 6. Retrieve single Decision
        d_get_resp = e2e_client.get(f"/api/workspaces/{ws_id}/decisions/{d_id}")
        assert d_get_resp.status_code == 200
        assert d_get_resp.json()["id"] == d_id

        # 7. Update Decision
        d_patch_resp = e2e_client.patch(
            f"/api/workspaces/{ws_id}/decisions/{d_id}",
            json={"decision": "Use an in-process LRU cache with 1024 max entries."},
        )
        assert d_patch_resp.status_code == 200
        assert d_patch_resp.json()["decision"] == "Use an in-process LRU cache with 1024 max entries."

        # 8. Delete Decision and OpenQuestion
        assert e2e_client.delete(f"/api/workspaces/{ws_id}/decisions/{d_id}").status_code == 204
        assert e2e_client.get(f"/api/workspaces/{ws_id}/decisions/{d_id}").status_code == 404

        assert e2e_client.delete(f"/api/workspaces/{ws_id}/questions/{q_id}").status_code == 204
        assert e2e_client.get(f"/api/workspaces/{ws_id}/questions/{q_id}").status_code == 404
    finally:
        e2e_client.delete(f"/api/workspaces/{ws_id}")


# ---------------------------------------------------------------------------
# 4. Domain Models & PostgreSQL Specifics (Direct Session)
# ---------------------------------------------------------------------------


def test_all_domain_models_lifecycle_on_migrated_db(
    e2e_session: Session,
) -> None:
    """Requirement 4 & 5: Ensure all 9 models in the migration chain persist and round-trip.

    Exercises Workspace, Repository, Conversation, Message, OpenQuestion,
    Decision, ChangeProposal, InventoryRun, and InventoryItem.
    """
    # 1. Workspace
    workspace = models.Workspace(
        name="Domain Model Workspace", description="Testing all 9 models"
    )
    e2e_session.add(workspace)
    e2e_session.flush()

    # 2. Repository
    repo = models.Repository(
        workspace_id=workspace.id,
        name="repo-1",
        local_path="/tmp/repo-1",
        branch="main",
        kind="documentation",
        writable=True,
    )
    e2e_session.add(repo)
    e2e_session.flush()

    # 3. Conversation
    conversation = models.Conversation(
        workspace_id=workspace.id,
        title="Arch Query",
        mode="investigate",
    )
    e2e_session.add(conversation)
    e2e_session.flush()

    # 4. Message
    message = models.Message(
        conversation_id=conversation.id,
        role="assistant",
        content="Here is the decision.",
        citations=[{"doc": "adr-001.md", "type": "explicit_decision"}],
        tool_calls=[{"id": "t1", "tool": "search"}],
    )
    e2e_session.add(message)
    e2e_session.flush()

    # 5. OpenQuestion (real native UUID + circular FK with Conversation)
    test_uuid = uuid.uuid4()
    question = models.OpenQuestion(
        workspace_id=workspace.id,
        uid=test_uuid,
        title="Should we support multi-tenant?",
        description="Scalability investigation",
        evidence=[{"source": "interview", "quote": "Need tenant isolation"}],
        affected=["auth", "database"],
        status="open",
        conversation_id=conversation.id,
    )
    e2e_session.add(question)
    e2e_session.flush()

    # Set circular foreign key: conversation.question_id -> open_questions.id
    conversation.question_id = question.id
    e2e_session.flush()

    # 6. Decision
    decision = models.Decision(
        workspace_id=workspace.id,
        title="ADR 002: Use PostgreSQL in Production",
        context="We need ACID transactions, native JSONB, and migrations.",
        decision="Adopt PostgreSQL with Alembic migrations.",
        rationale="Prevents drift and supports scalable documentation storage.",
        consequences="Requires running PostgreSQL in production.",
        status="approved",
        approved_at=dt.datetime.now(dt.timezone.utc),
        related_documents=["docs/architecture/adr-002.md"],
        related_questions=[str(test_uuid)],
    )
    e2e_session.add(decision)
    e2e_session.flush()

    # 7. ChangeProposal
    proposal = models.ChangeProposal(
        workspace_id=workspace.id,
        conversation_id=conversation.id,
        kind="create",
        title="Create ADR 002",
        reason="Document the database decision",
        evidence=[{"type": "decision_ref", "id": decision.id}],
        changes=[
            {"action": "create", "target_path": "docs/architecture/adr-002.md"}
        ],
        status="pending",
    )
    e2e_session.add(proposal)
    e2e_session.flush()

    # 8. InventoryRun
    run = models.InventoryRun(
        workspace_id=workspace.id,
        status="completed",
        summary="Document inventory complete",
        applied_at=dt.datetime.now(dt.timezone.utc),
    )
    e2e_session.add(run)
    e2e_session.flush()

    # 9. InventoryItem
    item = models.InventoryItem(
        run_id=run.id,
        source_path="adr-002.md",
        purpose="Architectural Decision Record",
        suggested_path="docs/architecture",
        confidence=0.97,
        overlaps=[],
        ambiguous=False,
        alternatives=[],
        reason="Matches ADR template",
        partial=False,
        decision="applied",
    )
    e2e_session.add(item)
    e2e_session.commit()

    # Expunge and reload from database to verify round-trip persistence
    e2e_session.expunge_all()

    loaded_q = e2e_session.get(models.OpenQuestion, question.id)
    assert loaded_q is not None
    assert loaded_q.uid == test_uuid
    assert isinstance(loaded_q.uid, uuid.UUID)
    assert loaded_q.evidence[0]["source"] == "interview"
    assert loaded_q.affected == ["auth", "database"]
    assert loaded_q.created_at.tzinfo is not None

    loaded_dec = e2e_session.get(models.Decision, decision.id)
    assert loaded_dec is not None
    assert loaded_dec.status == "approved"
    assert loaded_dec.approved_at.tzinfo is not None
    assert loaded_dec.related_documents == ["docs/architecture/adr-002.md"]

    loaded_conv = e2e_session.get(models.Conversation, conversation.id)
    assert loaded_conv.question_id == question.id
    assert len(loaded_conv.messages) == 1
    assert loaded_conv.messages[0].citations[0]["doc"] == "adr-001.md"

    # Test SET NULL on circular foreign key
    e2e_session.delete(loaded_q)
    e2e_session.commit()
    e2e_session.expunge_all()

    loaded_conv_after = e2e_session.get(models.Conversation, conversation.id)
    assert loaded_conv_after.question_id is None

    # Test CASCADE on workspace deletion
    ws_to_delete = e2e_session.get(models.Workspace, workspace.id)
    e2e_session.delete(ws_to_delete)
    e2e_session.commit()
    e2e_session.expunge_all()

    assert e2e_session.get(models.Conversation, conversation.id) is None
    assert e2e_session.get(models.Repository, repo.id) is None
    assert e2e_session.get(models.Decision, decision.id) is None
    assert e2e_session.get(models.ChangeProposal, proposal.id) is None
    assert e2e_session.get(models.InventoryRun, run.id) is None
    assert e2e_session.get(models.InventoryItem, item.id) is None


def test_postgres_constraints_enforced_by_database(
    e2e_session: Session,
) -> None:
    """Requirement 5: Check, Unique, and FK constraints are enforced by PostgreSQL."""
    ws = models.Workspace(name="Constraints WS")
    e2e_session.add(ws)
    e2e_session.commit()

    # 1. CHECK constraint: invalid message role
    with pytest.raises(IntegrityError):
        with e2e_session.begin_nested():
            e2e_session.execute(
                text(
                    "INSERT INTO messages (conversation_id, role, content) "
                    f"VALUES (NULL, 'superuser', 'hello')"
                )
            )

    # 2. CHECK constraint: invalid conversation mode
    with pytest.raises(IntegrityError):
        with e2e_session.begin_nested():
            e2e_session.add(
                models.Conversation(
                    workspace_id=ws.id, title="bad", mode="unsupported_mode"
                )
            )
            e2e_session.flush()

    # 3. UNIQUE constraint: duplicate question UUID
    dup_uid = uuid.uuid4()
    e2e_session.add(
        models.OpenQuestion(workspace_id=ws.id, title="Q1", uid=dup_uid)
    )
    e2e_session.flush()
    with pytest.raises(IntegrityError):
        with e2e_session.begin_nested():
            e2e_session.add(
                models.OpenQuestion(workspace_id=ws.id, title="Q2", uid=dup_uid)
            )
            e2e_session.flush()

    # 4. FOREIGN KEY constraint: non-existent workspace ID
    with pytest.raises(IntegrityError):
        with e2e_session.begin_nested():
            e2e_session.add(
                models.Repository(
                    workspace_id=999_999,
                    name="orphan",
                    local_path="/tmp",
                    kind="source",
                )
            )
            e2e_session.flush()

    # 5. NOT NULL on workspace name
    with pytest.raises(IntegrityError):
        with e2e_session.begin_nested():
            e2e_session.add(models.Workspace(name=None))
            e2e_session.flush()


def test_postgres_server_defaults_on_raw_insert(fresh_migrated_db: Engine) -> None:
    """Requirement 5: Server-side defaults populate on raw SQL inserts."""
    with fresh_migrated_db.begin() as conn:
        ws_id = conn.execute(
            text(
                "INSERT INTO workspaces (name) VALUES ('Raw WS') RETURNING id"
            )
        ).scalar()

        # created_at and updated_at are populated by PostgreSQL
        created, updated = conn.execute(
            text(
                "SELECT created_at, updated_at FROM workspaces WHERE id = :id"
            ),
            {"id": ws_id},
        ).one()
        assert created is not None
        assert updated is not None

        # conversations defaults: title, mode, archived
        conv_id = conn.execute(
            text(
                "INSERT INTO conversations (workspace_id) "
                "VALUES (:ws) RETURNING id"
            ),
            {"ws": ws_id},
        ).scalar()
        title, mode, archived = conn.execute(
            text(
                "SELECT title, mode, archived FROM conversations WHERE id = :id"
            ),
            {"id": conv_id},
        ).one()
        assert title == "New conversation"
        assert mode == "explore"
        assert archived is False


# ---------------------------------------------------------------------------
# 5. Migration Integrity & Existing Database Scenarios
# ---------------------------------------------------------------------------


def test_migration_downgrade_and_upgrade_cycle() -> None:
    """Requirement 6: Migration downgrade and re-upgrade cycle on clean PostgreSQL database."""
    db_name = "gaia_docs_cycle_test"
    db_url = make_url(TEST_DATABASE_URL).set(
        database=db_name
    ).render_as_string(hide_password=False)
    _drop_and_create_e2e_db(db_name)

    cfg = alembic_config(db_url)
    engine = build_engine(db_url)
    try:
        # Upgrade to head
        command.upgrade(cfg, "head")
        assert pending_revisions(engine) == []
        assert EXPECTED_TABLES <= set(inspect(engine).get_table_names())

        # Downgrade 1 revision (0002 -> 0001)
        command.downgrade(cfg, "-1")
        assert pending_revisions(engine) == ["0002_inventory_confidence"]

        # Upgrade back to head
        command.upgrade(cfg, "head")
        assert pending_revisions(engine) == []

        # Downgrade all the way to base
        command.downgrade(cfg, "base")
        remaining = set(inspect(engine).get_table_names())
        assert remaining <= {"alembic_version"}

        # Re-upgrade to head
        command.upgrade(cfg, "head")
        assert EXPECTED_TABLES <= set(inspect(engine).get_table_names())
        assert pending_revisions(engine) == []
    finally:
        engine.dispose()


def test_existing_database_scenario_idempotent_upgrade_with_data() -> None:
    """Requirement 7: Re-running `alembic upgrade head` on a database with representative data

    Verifies:
        fresh database -> upgrade head -> insert representative data ->
        application operates -> upgrade head again -> no unintended changes or data loss.
    """
    db_name = "gaia_docs_idempotent_test"
    db_url = make_url(TEST_DATABASE_URL).set(
        database=db_name
    ).render_as_string(hide_password=False)
    _drop_and_create_e2e_db(db_name)

    cfg = alembic_config(db_url)
    engine = build_engine(db_url)
    factory = session_factory(engine)
    try:
        # 1. Upgrade head
        command.upgrade(cfg, "head")

        # 2. Insert representative data across models
        q_uuid = uuid.uuid4()
        with factory() as session:
            ws = models.Workspace(
                name="Existing Data WS", description="Preserve me"
            )
            session.add(ws)
            session.flush()

            repo = models.Repository(
                workspace_id=ws.id,
                name="existing-repo",
                local_path="/tmp/existing",
                kind="documentation",
                writable=True,
            )
            conv = models.Conversation(
                workspace_id=ws.id, title="Existing Conversation"
            )
            session.add_all([repo, conv])
            session.flush()

            msg = models.Message(
                conversation_id=conv.id,
                role="user",
                content="Persistent message",
                citations=[{"p": "a.md"}],
            )
            q = models.OpenQuestion(
                workspace_id=ws.id,
                uid=q_uuid,
                title="Persistent question",
            )
            dec = models.Decision(
                workspace_id=ws.id,
                title="Persistent decision",
                status="approved",
            )
            run = models.InventoryRun(
                workspace_id=ws.id,
                status="completed",
                summary="Run summary",
            )
            session.add_all([msg, q, dec, run])
            session.flush()

            item = models.InventoryItem(
                run_id=run.id,
                source_path="item.md",
                purpose="Test preservation",
                decision="pending",
                confidence=0.9,
            )
            session.add(item)
            session.commit()

        # 3. Application operates normally
        with factory() as session:
            loaded_ws = session.query(models.Workspace).filter_by(name="Existing Data WS").one()
            assert loaded_ws.description == "Preserve me"
            loaded_q = session.query(models.OpenQuestion).filter_by(uid=q_uuid).one()
            assert loaded_q.title == "Persistent question"

        # 4. Run `alembic upgrade head` again (should be completely idempotent)
        command.upgrade(cfg, "head")

        # 5. Assert all data remains completely intact and unchanged
        with factory() as session:
            assert session.query(models.Workspace).count() == 1
            assert session.query(models.Repository).count() == 1
            assert session.query(models.Conversation).count() == 1
            assert session.query(models.Message).count() == 1
            assert session.query(models.OpenQuestion).count() == 1
            assert session.query(models.Decision).count() == 1
            assert session.query(models.InventoryRun).count() == 1
            assert session.query(models.InventoryItem).count() == 1

            ws_check = session.query(models.Workspace).first()
            assert ws_check.name == "Existing Data WS"
            assert ws_check.description == "Preserve me"

            q_check = session.query(models.OpenQuestion).first()
            assert q_check.uid == q_uuid
            assert q_check.title == "Persistent question"
    finally:
        engine.dispose()


def test_upgrade_from_0001_to_head_safely_backfills_existing_data() -> None:
    """Requirement 7 & 10: Upgrade from revision 0001 to 0002 with existing rows in inventory_items.

    Verifies that the backfill step in 0002_inventory_confidence successfully updates
    NULL alternatives and partial columns on existing rows without errors or data loss.
    """
    db_name = "gaia_docs_backfill_test"
    db_url = make_url(TEST_DATABASE_URL).set(
        database=db_name
    ).render_as_string(hide_password=False)
    _drop_and_create_e2e_db(db_name)

    cfg = alembic_config(db_url)
    engine = build_engine(db_url)
    try:
        # Upgrade only to initial revision 0001
        command.upgrade(cfg, "0001_initial")

        # Insert historical rows into 0001 schema
        with engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO workspaces (id, name, created_at, updated_at) "
                    "VALUES (1, 'Historical WS', now(), now())"
                )
            )
            conn.execute(
                text(
                    "INSERT INTO inventory_runs (id, workspace_id, status, created_at, updated_at) "
                    "VALUES (1, 1, 'completed', now(), now())"
                )
            )
            conn.execute(
                text(
                    "INSERT INTO inventory_items (id, run_id, source_path, purpose, decision) "
                    "VALUES (1, 1, 'historical_doc.md', 'Historical classification', 'pending')"
                )
            )

        # Upgrade to head (applies 0002_inventory_confidence)
        command.upgrade(cfg, "head")

        # Verify backfill succeeded: alternatives is [] and partial is False (NOT NULL)
        with engine.connect() as conn:
            row = conn.execute(
                text(
                    "SELECT source_path, alternatives, partial, reason "
                    "FROM inventory_items WHERE id = 1"
                )
            ).one()
            assert row.source_path == "historical_doc.md"
            assert row.alternatives == []
            assert row.partial is False
            assert row.reason is None
    finally:
        engine.dispose()
