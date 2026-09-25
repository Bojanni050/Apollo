"""Tests run against an in-memory SQLite database and throwaway repos on disk."""
from __future__ import annotations

import subprocess
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app import models  # noqa: F401  (registers tables on Base.metadata)
from app.db import Base, get_db
from app.main import app


@pytest.fixture()
def db_session_factory():
    """An in-memory database shared by the HTTP client and direct-DB tests.

    StaticPool keeps every session on one connection, so the schema created
    here is visible to both the test client and the `session` fixture.
    """
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
        future=True,
    )
    Base.metadata.create_all(bind=engine)
    factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    yield factory
    engine.dispose()


@pytest.fixture()
def client(db_session_factory):
    def override_get_db():
        db = db_session_factory()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db
    # No `with` block: the real lifespan would bootstrap the configured
    # PostgreSQL database, which must not happen in unit tests.
    test_client = TestClient(app)
    yield test_client
    test_client.close()
    app.dependency_overrides.clear()


@pytest.fixture()
def session(db_session_factory) -> Iterator[Session]:
    """Direct database access, for tests that exercise services rather than HTTP."""
    db = db_session_factory()
    try:
        yield db
    finally:
        db.close()


def _git(repo: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        capture_output=True,
        text=True,
    )


@pytest.fixture()
def doc_repo(tmp_path: Path) -> Path:
    """A small Git documentation repository mirroring the target structure."""
    root = tmp_path / "gaia-docs"
    (root / "architecture" / "components").mkdir(parents=True)
    (root / "architecture" / "decisions").mkdir(parents=True)
    (root / "foundation").mkdir()
    (root / "development").mkdir()
    (root / "operations").mkdir()
    (root / ".git").mkdir()

    (root / "README.md").write_text("# Gaia\n\nDocumentation root.\n", encoding="utf-8")
    (root / "foundation" / "principles.md").write_text(
        "# Principles\n\nWe optimise for local-first operation.\n", encoding="utf-8"
    )
    (root / "architecture" / "overview.md").write_text(
        "# Architecture overview\n\nThe system is split into services and components.\n",
        encoding="utf-8",
    )
    (root / "architecture" / "components" / "memory.md").write_text(
        "# Memory component\n\nStores conversational context and embeddings.\n",
        encoding="utf-8",
    )
    (root / "architecture" / "decisions" / "adr-001-sqlite.md").write_text(
        "# ADR 001: Use SQLite\n\nStatus: accepted\n", encoding="utf-8"
    )
    (root / "notes.md").write_text(
        "# Scratch\n\nRandom thoughts about the memory architecture, unfiled.\n",
        encoding="utf-8",
    )

    _git(root, "init", "-b", "main")
    _git(root, "config", "user.email", "test@example.com")
    _git(root, "config", "user.name", "Test")
    _git(root, "add", "-A")
    _git(root, "commit", "-m", "initial documentation")
    return root


@pytest.fixture()
def source_repo(tmp_path: Path) -> Path:
    root = tmp_path / "gaia-service"
    root.mkdir()
    (root / "src").mkdir()
    (root / "src" / "memory.py").write_text(
        "def remember(turn):\n    '''Store a turn.'''\n    return turn\n", encoding="utf-8"
    )
    (root / "README.md").write_text("# Gaia Service\n", encoding="utf-8")
    _git(root, "init", "-b", "main")
    _git(root, "config", "user.email", "test@example.com")
    _git(root, "config", "user.name", "Test")
    _git(root, "add", "-A")
    _git(root, "commit", "-m", "initial")
    return root


@pytest.fixture()
def workspace(client: TestClient, doc_repo: Path, source_repo: Path) -> dict:
    created = client.post("/api/workspaces", json={"name": "Gaia"}).json()
    client.post(
        f"/api/workspaces/{created['id']}/repositories",
        json={
            "name": "gaia-docs",
            "local_path": str(doc_repo),
            "branch": "main",
            "kind": "documentation",
            "writable": True,
        },
    )
    client.post(
        f"/api/workspaces/{created['id']}/repositories",
        json={"name": "gaia-service", "local_path": str(source_repo), "kind": "source"},
    )
    return client.get(f"/api/workspaces/{created['id']}").json()
