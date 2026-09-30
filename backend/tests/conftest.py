"""Tests run against an in-memory SQLite database and throwaway repos on disk.

The suite runs in *development* mode so that existing tests do not each have to
authenticate. That relaxation is opt-in per test run, never the application
default: ``app.config.Settings`` still defaults to production and still refuses
to start without credentials. The dedicated security tests
(``tests/test_security.py``) deliberately re-enable authentication and exercise
the production rules.
"""
from __future__ import annotations

import os
import subprocess
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

# Must happen before app.config is imported anywhere, because Settings() reads
# the environment at construction time.
os.environ.setdefault("APP_ENV", "development")
os.environ.setdefault("AUTH_ENABLED", "false")
os.environ.setdefault("ALLOW_UNRESTRICTED_WORKSPACE_ROOTS", "true")
# Keep any developer .env or shell exports from leaking into the test run.
os.environ.pop("ALLOWED_WORKSPACE_ROOTS", None)
os.environ.pop("CORS_ORIGINS", None)

from app import models  # noqa: E402,F401  (registers tables on Base.metadata)
from app.config import settings  # noqa: E402
from app.db import Base, get_db  # noqa: E402
from app.llm.base import LLMProvider, LLMResponse, ToolCall  # noqa: E402
from app.main import app  # noqa: E402


class ScriptedProvider(LLMProvider):
    """Replays prepared turns; records the messages it was given.

    Canonical fake provider for the suite. Previously duplicated in
    test_chat_agent / test_chat_tooling / test_consistency_and_links;
    keep all new tests importing from here (``from tests.conftest import
    ScriptedProvider``). ``test_chat_agent`` re-exports it so existing
    ``from tests.test_chat_agent import ScriptedProvider`` imports keep working.
    """

    def __init__(self, turns: list[LLMResponse]) -> None:
        self.turns = list(turns)
        self.calls: list[list[dict]] = []
        self.tools_offered: list = []

    def chat(self, messages, tools=None, temperature=None, max_output_tokens=None):
        self.calls.append(messages)
        self.tools_offered.append(tools)
        if not self.turns:
            return LLMResponse(content="done")
        return self.turns.pop(0)


def tool_turn(name: str, arguments: dict, call_id: str = "c1") -> LLMResponse:
    """One assistant turn requesting a single tool call."""
    return LLMResponse(
        content="",
        tool_calls=[ToolCall(id=call_id, name=name, arguments=arguments)],
    )


def llm_is_configured() -> bool:
    """Whether this machine can actually build a provider.

    ``app.llm.get_provider`` needs a base URL and a model, for the background
    role or the primary fallback; anything less raises ``LLMNotConfigured``.

    A few tests assert what the API does when *no* provider exists. A developer
    .env with a working model inverts that premise, so those tests can only run
    on a machine without one. They are marked with this predicate rather than
    skipped outright, so a fresh checkout and CI -- where the LLM is genuinely
    absent -- still exercise the refusal path.
    """
    return bool((settings.background_llm_model or settings.llm_model) and settings.llm_base_url)


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


@pytest.fixture(autouse=True)
def apollo_storage_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point Apollo's own storage at a throwaway directory.

    Autouse, and that is the reason it exists: the configured default is a real
    folder beside the backend, so a single test that forgot to redirect it would
    write into the developer's own inbox and leave files behind. Every test gets
    a fresh root whether or not it is about the inbox.
    """
    root = tmp_path / "apollo_storage"
    monkeypatch.setattr(settings, "apollo_storage_root", str(root))
    return root


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
