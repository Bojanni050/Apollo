"""Tests for Delphi Pulse.

The central properties:

* a run proposes tags and cross-document connections from document CONTENTS,
  using the *background* model;
* in suggest mode nothing is written until a human approves, and approval
  writes YAML front matter through the guarded write path;
* in apply mode the run writes immediately, still through that same path;
* the incremental hash ignores the Pulse front matter, so applying a
  suggestion does not make the next run treat the document as changed.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.llm.base import LLMResponse
from app.models import WorkspacePulseSettings
from app.services.pulse import (
    _hash_document,
    _parse_response,
    apply_pulse_item,
    run_pulse,
    with_pulse_front_matter,
)
from tests.test_chat_agent import ScriptedProvider


def _pulse_response(entries: list[dict]) -> LLMResponse:
    return LLMResponse(content=json.dumps({"documents": entries}))


@pytest.fixture()
def mock_llm(monkeypatch: pytest.MonkeyPatch):
    def install(turns: list[LLMResponse]) -> ScriptedProvider:
        provider = ScriptedProvider(turns)
        monkeypatch.setattr("app.api.routes_pulse.get_provider", lambda *a, **k: provider)
        return provider

    return install


# --------------------------------------------------------------------------
# Response parsing
# --------------------------------------------------------------------------


def test_parse_tolerates_code_fences() -> None:
    raw = '```json\n{"documents": [{"path": "a.md", "tags": ["db"]}]}\n```'
    result = _parse_response(raw, {"a.md"})
    assert result[0]["path"] == "a.md"
    assert result[0]["tags"] == ["db"]


def test_parse_ignores_hallucinated_paths_and_relations() -> None:
    raw = json.dumps(
        {
            "documents": [
                {
                    "path": "a.md",
                    "tags": ["api"],
                    "connections": [
                        {"path": "ghost.md", "relation": "supports"},
                        {"path": "a.md", "relation": "extends"},  # self-link
                        {"path": "b.md", "relation": "marries"},  # invalid relation
                        {"path": "b.md", "relation": "supports", "why": "ok"},
                    ],
                }
            ]
        }
    )
    result = _parse_response(raw, {"a.md", "b.md"})
    connections = result[0]["connections"]
    assert len(connections) == 1
    assert connections[0] == {"path": "b.md", "relation": "supports", "why": "ok"}


def test_parse_caps_tags_at_five() -> None:
    raw = json.dumps(
        {"documents": [{"path": "a.md", "tags": ["1", "2", "3", "4", "5", "6"]}]}
    )
    result = _parse_response(raw, {"a.md"})
    assert len(result[0]["tags"]) == 5


# --------------------------------------------------------------------------
# Front matter application
# --------------------------------------------------------------------------


def test_front_matter_added_idempotently() -> None:
    base = "# Title\n\nBody\n"
    once = with_pulse_front_matter(base, ["x"], [])
    assert once.startswith("---\npulse-tags:")
    twice = with_pulse_front_matter(once, ["y"], [])
    assert 'pulse-tags: ["y"]' in twice
    assert 'pulse-tags: ["x"]' not in twice
    assert twice.endswith("Body\n")


def test_hash_ignores_pulse_front_matter() -> None:
    """Applying a suggestion must not make the next run re-scan the file."""
    base = "# Title\n\nBody\n"
    applied = with_pulse_front_matter(base, ["x"], [])
    assert _hash_document(applied) == _hash_document(base)


def test_existing_front_matter_is_preserved() -> None:
    base = "---\nauthor: someone\n---\n\n# Title\n\nBody\n"
    out = with_pulse_front_matter(base, ["x"], [])
    assert "author: someone" in out
    assert "pulse-tags:" in out


def test_apply_requires_something_to_write() -> None:
    from app.models import PulseItem

    item = PulseItem(run_id=1, file_path="a.md", tags=[], connections=[])
    with pytest.raises(Exception):
        apply_pulse_item("/does-not-matter", item)


# --------------------------------------------------------------------------
# run_pulse service
# --------------------------------------------------------------------------


def test_run_records_items_and_writes_nothing(doc_repo: Path) -> None:
    provider = ScriptedProvider(
        [
            _pulse_response(
                [
                    {
                        "path": "notes.md",
                        "summary": "Component notes.",
                        "tags": ["memory", "components"],
                        "connections": [],
                        "confidence": 0.7,
                    }
                ]
            )
        ]
    )
    from app.db import SessionLocal
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.db import Base
    import app.db as db_mod

    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    db = Session()
    try:
        run = run_pulse(provider, db, 1, str(doc_repo), mode="suggest")
        db.commit()
        assert run.status == "completed"
        by_path = {i.file_path: i for i in run.items}
        assert by_path["notes.md"].tags == ["memory", "components"]
        assert all(i.decision == "pending" for i in run.items)
        # Nothing was written.
        content = (doc_repo / "notes.md").read_text(encoding="utf-8")
        assert "pulse-tags" not in content
    finally:
        db.close()


def test_run_in_apply_mode_writes_front_matter(doc_repo: Path) -> None:
    provider = ScriptedProvider(
        [
            _pulse_response(
                [
                    {
                        "path": "notes.md",
                        "summary": "Component notes.",
                        "tags": ["memory"],
                        "connections": [
                            {
                                "path": "architecture/components/memory.md",
                                "relation": "relates-to",
                                "why": "Same component.",
                            }
                        ],
                        "confidence": 0.7,
                    }
                ]
            )
        ]
    )
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.db import Base

    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    db = Session()
    try:
        run = run_pulse(provider, db, 1, str(doc_repo), mode="apply")
        db.commit()
        content = (doc_repo / "notes.md").read_text(encoding="utf-8")
        assert "pulse-tags:" in content
        assert "relates-to" in content
        item = run.items[0]
        assert item.decision == "applied"
    finally:
        db.close()


def test_run_is_incremental(doc_repo: Path) -> None:
    """An unchanged document is not re-examined on the second run."""
    provider = ScriptedProvider([_pulse_response([])])
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.db import Base

    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    db = Session()
    try:
        first = run_pulse(provider, db, 1, str(doc_repo), mode="suggest")
        db.commit()
        assert first.summary != "No changed documents since the last run."
        second = run_pulse(provider, db, 1, str(doc_repo), mode="suggest")
        db.commit()
        assert second.summary == "No changed documents since the last run."
    finally:
        db.close()


# --------------------------------------------------------------------------
# API
# --------------------------------------------------------------------------


def test_pulse_endpoints_require_llm_configuration(client: TestClient, workspace: dict) -> None:
    """Without an LLM configured the run is refused, not crashed."""
    response = client.post(f"/api/workspaces/{workspace['id']}/pulse/runs", json={})
    assert response.status_code in (502, 503)


def test_pulse_settings_roundtrip(client: TestClient, workspace: dict) -> None:
    ws = workspace["id"]
    default = client.get(f"/api/workspaces/{ws}/pulse/settings")
    assert default.status_code == 200
    body = default.json()
    assert body["mode"] == "suggest"
    assert body["schedule_enabled"] is False
    assert body["schedule_kind"] == "interval"
    assert body["interval_hours"] == 1

    changed = client.put(
        f"/api/workspaces/{ws}/pulse/settings",
        json={
            "mode": "apply",
            "schedule_enabled": True,
            "schedule_kind": "interval",
            "interval_hours": 6,
        },
    )
    assert changed.status_code == 200
    assert changed.json()["mode"] == "apply"
    assert changed.json()["schedule_enabled"] is True
    assert changed.json()["interval_hours"] == 6

    reread = client.get(f"/api/workspaces/{ws}/pulse/settings")
    assert reread.json()["interval_hours"] == 6

    weekly = client.put(
        f"/api/workspaces/{ws}/pulse/settings",
        json={
            "mode": "suggest",
            "schedule_enabled": True,
            "schedule_kind": "weekly",
            "weekly_day": 4,
            "weekly_hour": 9,
        },
    )
    assert weekly.status_code == 200
    assert weekly.json()["schedule_kind"] == "weekly"
    assert weekly.json()["weekly_day"] == 4
    assert weekly.json()["weekly_hour"] == 9


def test_pulse_settings_rejects_unknown_mode(client: TestClient, workspace: dict) -> None:
    ws = workspace["id"]
    response = client.put(f"/api/workspaces/{ws}/pulse/settings", json={"mode": "auto"})
    assert response.status_code == 422


@pytest.mark.parametrize(
    "payload",
    [
        {"mode": "suggest", "interval_hours": 0},
        {"mode": "suggest", "interval_hours": 25},
        {"mode": "suggest", "schedule_kind": "daily"},
        {"mode": "suggest", "schedule_kind": "weekly", "weekly_day": 7},
        {"mode": "suggest", "schedule_kind": "weekly", "weekly_hour": 24},
    ],
)
def test_pulse_settings_rejects_out_of_range_schedule(
    client: TestClient, workspace: dict, payload: dict
) -> None:
    ws = workspace["id"]
    response = client.put(f"/api/workspaces/{ws}/pulse/settings", json=payload)
    assert response.status_code == 422


def test_suggest_run_never_writes_then_apply_does(
    client: TestClient, workspace: dict, doc_repo: Path, mock_llm
) -> None:
    ws = workspace["id"]
    mock_llm(
        [
            _pulse_response(
                [
                    {
                        "path": "notes.md",
                        "summary": "Component notes.",
                        "tags": ["memory"],
                        "connections": [],
                        "confidence": 0.9,
                    }
                ]
            )
        ]
    )
    created = client.post(f"/api/workspaces/{ws}/pulse/runs", json={})
    assert created.status_code == 201
    run = created.json()
    assert run["mode"] == "suggest"
    assert len(run["items"]) == 1
    item = run["items"][0]
    assert item["decision"] == "pending"

    content = (doc_repo / "notes.md").read_text(encoding="utf-8")
    assert "pulse-tags" not in content

    applied = client.post(
        f"/api/workspaces/{ws}/pulse/runs/{run['id']}/items/{item['id']}/apply"
    )
    assert applied.status_code == 200
    assert applied.json()["item"]["decision"] == "applied"

    content = (doc_repo / "notes.md").read_text(encoding="utf-8")
    assert "pulse-tags:" in content


def test_skip_leaves_file_untouched(
    client: TestClient, workspace: dict, doc_repo: Path, mock_llm
) -> None:
    ws = workspace["id"]
    mock_llm(
        [
            _pulse_response(
                [{"path": "notes.md", "summary": "s", "tags": ["t"], "connections": []}]
            )
        ]
    )
    run = client.post(f"/api/workspaces/{ws}/pulse/runs", json={}).json()
    item = run["items"][0]
    skipped = client.post(
        f"/api/workspaces/{ws}/pulse/runs/{run['id']}/items/{item['id']}/skip"
    )
    assert skipped.status_code == 200
    assert skipped.json()["item"]["decision"] == "skipped"
    content = (doc_repo / "notes.md").read_text(encoding="utf-8")
    assert "pulse-tags" not in content


def test_apply_mode_setting_drives_run(
    client: TestClient, workspace: dict, doc_repo: Path, mock_llm
) -> None:
    ws = workspace["id"]
    client.put(f"/api/workspaces/{ws}/pulse/settings", json={"mode": "apply"})
    mock_llm(
        [
            _pulse_response(
                [{"path": "notes.md", "summary": "s", "tags": ["t"], "connections": []}]
            )
        ]
    )
    run = client.post(f"/api/workspaces/{ws}/pulse/runs", json={}).json()
    assert run["mode"] == "apply"
    assert run["items"][0]["decision"] == "applied"
    content = (doc_repo / "notes.md").read_text(encoding="utf-8")
    assert "pulse-tags:" in content


# --------------------------------------------------------------------------
# Scheduler
# --------------------------------------------------------------------------


def test_scheduler_due_logic() -> None:
    """The pure slot logic, exercised directly (see test_pulse_scheduler.py
    for the same cases; kept here as a fast smoke check)."""
    import datetime as dt

    from app.services.pulse_scheduler import due

    now = dt.datetime(2026, 10, 5, 9, 30, tzinfo=dt.timezone.utc)  # Monday
    s = WorkspacePulseSettings(
        workspace_id=1,
        mode="suggest",
        schedule_enabled=True,
        schedule_kind="interval",
        interval_hours=6,
    )
    assert due(s, now)
    s.last_run_at = now - dt.timedelta(hours=2)
    assert not due(s, now)
    s.last_run_at = now - dt.timedelta(hours=7)
    assert due(s, now)


def test_sqlite_upgrade_adds_missing_pulse_columns(tmp_path) -> None:
    """A database created before the schedule columns existed must be
    upgraded in place at startup, not crash on the first Pulse query."""
    import os

    from sqlalchemy import create_engine, text

    db_path = tmp_path / "old.db"
    engine = create_engine(f"sqlite:///{db_path}")
    with engine.begin() as conn:
        for statement in (
            """
            CREATE TABLE workspaces (
                id INTEGER PRIMARY KEY,
                name VARCHAR(200) NOT NULL,
                description TEXT,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
            )
            """,
            """
            CREATE TABLE workspace_pulse_settings (
                workspace_id INTEGER PRIMARY KEY,
                mode VARCHAR(20) DEFAULT 'suggest' NOT NULL,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
            )
            """,
            "INSERT INTO workspaces (name) VALUES ('Gaia')",
            """
            INSERT INTO workspace_pulse_settings (workspace_id, mode)
                VALUES (1, 'suggest')
            """,
        ):
            conn.exec_driver_sql(statement)
    engine.dispose()

    # Startup path: init_db must upgrade the stale schema in place.
    os.environ["DATABASE_URL"] = f"sqlite:///{db_path}"
    try:
        import app.db as db_mod

        stale = create_engine(f"sqlite:///{db_path}")
        db_mod._upgrade_sqlite_columns(stale)
        stale.dispose()
    finally:
        del os.environ["DATABASE_URL"]

    verify = create_engine(f"sqlite:///{db_path}")
    with verify.begin() as conn:
        cols = {
            r[1]
            for r in conn.execute(text("PRAGMA table_info(workspace_pulse_settings)"))
        }
        assert "schedule_enabled" in cols
        assert "schedule_kind" in cols
        assert "interval_hours" in cols
        assert "weekly_day" in cols
        assert "weekly_hour" in cols
        assert "last_run_at" in cols
        row = conn.execute(
            text("SELECT schedule_enabled, interval_hours FROM workspace_pulse_settings")
        ).fetchone()
        # The pre-existing row keeps working with the defaults applied.
        assert row[0] == 0
        assert row[1] == 1
    verify.dispose()
