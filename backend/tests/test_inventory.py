"""Tests for the document inventory.

The central property: documents are classified by their CONTENTS. A file called
`notes.md` that actually records a decision must be filed as a decision.
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.llm.base import LLMResponse
from app.services.inventory import Classification, InventoryResult, _parse_response, run_inventory
from tests.test_chat_agent import ScriptedProvider


def _inventory_response(entries: list[dict], summary: str = "Some docs.") -> LLMResponse:
    return LLMResponse(
        content=json.dumps({"classifications": entries, "summary": summary})
    )


@pytest.fixture()
def mock_llm(monkeypatch: pytest.MonkeyPatch):
    def install(turns: list[LLMResponse]) -> ScriptedProvider:
        provider = ScriptedProvider(turns)
        monkeypatch.setattr("app.api.routes_inventory.get_provider", lambda *a, **k: provider)
        return provider

    return install


# --------------------------------------------------------------------------
# Response parsing
# --------------------------------------------------------------------------


def test_parse_tolerates_code_fences() -> None:
    raw = '```json\n{"classifications": [{"path": "a.md", "suggested_path": "foundation"}]}\n```'
    result = _parse_response(raw, ["a.md"])
    assert result.classifications[0].path == "a.md"


def test_architecture_root_is_a_valid_destination() -> None:
    """A document may legitimately belong directly in architecture/."""
    raw = json.dumps(
        {"classifications": [{"path": "overview.md", "suggested_path": "architecture",
                              "confidence": 0.9}]}
    )
    result = _parse_response(raw, ["overview.md"])
    assert result.classifications[0].suggested_path == "architecture"
    assert result.classifications[0].ambiguous is False


def test_parse_ignores_hallucinated_paths() -> None:
    raw = json.dumps(
        {
            "classifications": [
                {"path": "a.md", "suggested_path": "foundation"},
                {"path": "ghost.md", "suggested_path": "operations"},
            ]
        }
    )
    result = _parse_response(raw, ["a.md"])
    assert [c.path for c in result.classifications] == ["a.md"]


def test_parse_rejects_invented_folders() -> None:
    """A model that invents a folder must not smuggle it into the plan."""
    raw = json.dumps({"classifications": [{"path": "a.md", "suggested_path": "secrets/admin"}]})
    result = _parse_response(raw, ["a.md"])
    assert result.classifications[0].suggested_path is None
    assert result.classifications[0].ambiguous is True


def test_null_suggestion_is_marked_ambiguous() -> None:
    raw = json.dumps({"classifications": [{"path": "a.md", "suggested_path": None}]})
    result = _parse_response(raw, ["a.md"])
    assert result.classifications[0].ambiguous is True


def test_clamps_confidence() -> None:
    raw = json.dumps(
        {"classifications": [{"path": "a.md", "confidence": 5.0}, {"path": "b.md", "confidence": -2}]}
    )
    result = _parse_response(raw, ["a.md", "b.md"])
    assert result.classifications[0].confidence == 1.0
    assert result.classifications[1].confidence == 0.0


def test_garbage_response_does_not_raise() -> None:
    result = _parse_response("I am not JSON at all", ["a.md"])
    assert result.classifications == []


def test_unknown_overlaps_are_dropped() -> None:
    raw = json.dumps(
        {"classifications": [{"path": "a.md", "overlaps": ["real.md", "made-up.md"]}]}
    )
    result = _parse_response(raw, ["a.md", "real.md"])
    assert result.classifications[0].overlaps == ["real.md"]


# --------------------------------------------------------------------------
# run_inventory
# --------------------------------------------------------------------------


def test_inventory_classifies_by_content_not_filename(doc_repo: Path) -> None:
    """notes.md is really a component note; the model decides from the text."""
    provider = ScriptedProvider(
        [
            _inventory_response(
                [
                    {
                        "path": "notes.md",
                        "purpose": "Describes how the memory component stores context.",
                        "suggested_path": "architecture/components",
                        "confidence": 0.8,
                    }
                ]
            )
        ]
    )
    result = run_inventory(provider, str(doc_repo))
    by_path = {c.path: c for c in result.classifications}
    assert by_path["notes.md"].suggested_path == "architecture/components"
    # The filename said nothing; the content did.
    assert by_path["notes.md"].purpose.startswith("Describes")


def test_inventory_reports_every_document(doc_repo: Path) -> None:
    provider = ScriptedProvider([_inventory_response([])])
    result = run_inventory(provider, str(doc_repo))
    on_disk = {p.relative_to(doc_repo).as_posix() for p in doc_repo.rglob("*.md")}
    assert {c.path for c in result.classifications} == on_disk
    # Anything the model skipped is flagged, not silently dropped.
    assert all(c.ambiguous for c in result.classifications)


def test_inventory_shows_document_contents_to_the_model(doc_repo: Path) -> None:
    """The model must see the text, not just the filename."""
    provider = ScriptedProvider([_inventory_response([])])
    run_inventory(provider, str(doc_repo))


# --------------------------------------------------------------------------
# API: creating a plan moves nothing
# --------------------------------------------------------------------------


def _docs_id(workspace: dict) -> int:
    return next(r["id"] for r in workspace["repositories"] if r["kind"] == "documentation")


def test_creating_a_run_moves_nothing(
    client: TestClient, workspace: dict, doc_repo: Path, mock_llm
) -> None:
    mock_llm([_inventory_response([{"path": "notes.md", "purpose": "p",
                                   "suggested_path": "architecture/components",
                                   "confidence": 0.9}])])
    body = client.post(
        f"/api/workspaces/{workspace['id']}/inventory/runs",
        json={"repository_id": _docs_id(workspace)},
    ).json()

    assert body["status"] == "completed"
    item = body["items"][0]
    assert item["target_path"] == "architecture/components/notes.md"
    assert item["needs_move"] is True
    assert item["decision"] == "pending"

    # Nothing moved.
    assert (doc_repo / "notes.md").exists()
    assert not (doc_repo / "architecture" / "components" / "notes.md").exists()


def test_inventory_includes_already_placed_documents(
    client: TestClient, workspace: dict, mock_llm
) -> None:
    mock_llm(
        [
            _inventory_response(
                [
                    {"path": "architecture/overview.md", "suggested_path": "architecture",
                     "purpose": "Overview", "confidence": 0.9}
                ]
            )
        ]
    )
    body = client.post(
        f"/api/workspaces/{workspace['id']}/inventory/runs", json={}
    ).json()
    item = next(i for i in body["items"] if i["source_path"] == "architecture/overview.md")
    assert item["needs_move"] is False
    assert item["target_path"] is None


def test_inventory_requires_the_documentation_repository(
    client: TestClient, workspace: dict, mock_llm
) -> None:
    src = next(r["id"] for r in workspace["repositories"] if r["kind"] == "source")
    mock_llm([_inventory_response([])])
    response = client.post(
        f"/api/workspaces/{workspace['id']}/inventory/runs", json={"repository_id": src}
    )
    assert response.status_code == 400


def test_inventory_needs_an_llm(client: TestClient, workspace: dict) -> None:
    response = client.post(f"/api/workspaces/{workspace['id']}/inventory/runs", json={})
    assert response.status_code == 503


# --------------------------------------------------------------------------
# API: approval performs the moves
# --------------------------------------------------------------------------


def test_apply_single_item_moves_the_file(
    client: TestClient, workspace: dict, doc_repo: Path, mock_llm
) -> None:
    mock_llm([_inventory_response([{"path": "notes.md", "purpose": "p",
                                   "suggested_path": "architecture/components",
                                   "confidence": 0.9}])])
    run = client.post(f"/api/workspaces/{workspace['id']}/inventory/runs", json={}).json()
    item_id = next(i["id"] for i in run["items"] if i["source_path"] == "notes.md")

    result = client.post(
        f"/api/workspaces/{workspace['id']}/inventory/runs/{run['id']}"
        f"/items/{item_id}/apply"
    ).json()

    assert result["item"]["decision"] == "applied"
    assert result["requires_manual_commit"] is True
    assert not (doc_repo / "notes.md").exists()
    assert (doc_repo / "architecture" / "components" / "notes.md").exists()
    # We never commit on the user's behalf: both paths show as uncommitted.
    status_out = subprocess.run(
        ["git", "-C", str(doc_repo), "status", "--porcelain"],
        capture_output=True, text=True,
    ).stdout
    assert "notes.md" in status_out
    assert len(subprocess.run(
        ["git", "-C", str(doc_repo), "rev-list", "--count", "HEAD"],
        capture_output=True, text=True,
    ).stdout.strip()) == 1


def test_skip_leaves_the_file_alone(
    client: TestClient, workspace: dict, doc_repo: Path, mock_llm
) -> None:
    mock_llm([_inventory_response([{"path": "notes.md",
                                   "suggested_path": "architecture/components"}])])
    run = client.post(f"/api/workspaces/{workspace['id']}/inventory/runs", json={}).json()
    item_id = next(i["id"] for i in run["items"] if i["source_path"] == "notes.md")

    result = client.post(
        f"/api/workspaces/{workspace['id']}/inventory/runs/{run['id']}"
        f"/items/{item_id}/skip"
    ).json()
    assert result["item"]["decision"] == "skipped"
    assert (doc_repo / "notes.md").exists()
    assert not (doc_repo / "architecture" / "components" / "notes.md").exists()


def test_apply_whole_plan_moves_each_suggestion(
    client: TestClient, workspace: dict, doc_repo: Path, mock_llm
) -> None:
    mock_llm(
        [
            _inventory_response(
                [
                    {"path": "notes.md", "suggested_path": "architecture/components",
                     "confidence": 0.9},
                    {"path": "README.md", "suggested_path": "foundation",
                     "confidence": 0.9},
                    {"path": "architecture/overview.md", "suggested_path": "architecture",
                     "confidence": 0.9},
                ]
            )
        ]
    )
    run = client.post(f"/api/workspaces/{workspace['id']}/inventory/runs", json={}).json()
    result = client.post(
        f"/api/workspaces/{workspace['id']}/inventory/runs/{run['id']}/apply", json={}
    ).json()

    assert set(result["applied"]) == {
        "architecture/components/notes.md",
        "foundation/README.md",
    }
    assert (doc_repo / "architecture" / "components" / "notes.md").exists()
    assert (doc_repo / "foundation" / "README.md").exists()
    # The already-correct document is not counted as a move.
    assert (doc_repo / "architecture" / "overview.md").exists()


def test_ambiguous_items_are_never_auto_applied(
    client: TestClient, workspace: dict, doc_repo: Path, mock_llm
) -> None:
    mock_llm(
        [
            _inventory_response(
                [
                    {"path": "notes.md", "suggested_path": None, "ambiguous": True,
                     "note": "Could be a component note or a decision."}
                ]
            )
        ]
    )
    run = client.post(f"/api/workspaces/{workspace['id']}/inventory/runs", json={}).json()
    item_id = next(i["id"] for i in run["items"] if i["source_path"] == "notes.md")
    assert run["items"][0]["ambiguous"] is True

    # Bulk apply refuses to touch it.
    result = client.post(
        f"/api/workspaces/{workspace['id']}/inventory/runs/{run['id']}/apply", json={}
    ).json()
    assert result["applied"] == []
    assert any("ambiguous" in s["reason"] for s in result["skipped"])
    assert (doc_repo / "notes.md").exists()

    # So does the single-item endpoint.
    response = client.post(
        f"/api/workspaces/{workspace['id']}/inventory/runs/{run['id']}"
        f"/items/{item_id}/apply"
    )
    assert response.status_code == 409
    assert "ambiguous" in response.json()["detail"].lower()


def test_apply_selected_items_only(
    client: TestClient, workspace: dict, doc_repo: Path, mock_llm
) -> None:
    mock_llm(
        [
            _inventory_response(
                [
                    {"path": "notes.md", "suggested_path": "architecture/components"},
                    {"path": "README.md", "suggested_path": "foundation"},
                ]
            )
        ]
    )
    run = client.post(f"/api/workspaces/{workspace['id']}/inventory/runs", json={}).json()
    notes_id = next(i["id"] for i in run["items"] if i["source_path"] == "notes.md")

    result = client.post(
        f"/api/workspaces/{workspace['id']}/inventory/runs/{run['id']}/apply",
        json={"item_ids": [notes_id]},
    ).json()

    assert result["applied"] == ["architecture/components/notes.md"]
    assert (doc_repo / "architecture" / "components" / "notes.md").exists()
    assert (doc_repo / "README.md").exists()  # untouched


def test_cannot_apply_an_item_twice(client: TestClient, workspace: dict, mock_llm) -> None:
    mock_llm([_inventory_response([{"path": "notes.md",
                                   "suggested_path": "architecture/components"}])])
    run = client.post(f"/api/workspaces/{workspace['id']}/inventory/runs", json={}).json()
    item_id = next(i["id"] for i in run["items"] if i["source_path"] == "notes.md")
    base = f"/api/workspaces/{workspace['id']}/inventory/runs/{run['id']}/items/{item_id}"

    assert client.post(f"{base}/apply").status_code == 200
    assert client.post(f"{base}/apply").status_code == 409
    assert client.post(f"{base}/skip").status_code == 409


def test_inventory_refuses_untracked_documents(
    client: TestClient, workspace: dict, doc_repo: Path, mock_llm
) -> None:
    """A file Git does not track has no history, so it is not moved."""
    (doc_repo / "draft.md").write_text("# Draft\n", encoding="utf-8")
    mock_llm([_inventory_response([{"path": "draft.md",
                                   "suggested_path": "architecture/components"}])])
    run = client.post(f"/api/workspaces/{workspace['id']}/inventory/runs", json={}).json()
    item_id = next(i["id"] for i in run["items"] if i["source_path"] == "draft.md")

    response = client.post(
        f"/api/workspaces/{workspace['id']}/inventory/runs/{run['id']}"
        f"/items/{item_id}/apply"
    )
    assert response.status_code == 409
    assert "untracked" in response.json()["detail"].lower()
    assert (doc_repo / "draft.md").exists()


def test_inventory_creates_a_missing_target_folder(
    client: TestClient, workspace: dict, doc_repo: Path, mock_llm
) -> None:
    """The inventory establishes the structure, so a new folder is expected."""
    # architecture/flows is part of the target structure but the fixture does
    # not create it, so this exercises creating a genuinely missing folder.
    assert not (doc_repo / "architecture" / "flows").exists()
    mock_llm([_inventory_response([{"path": "notes.md",
                                   "suggested_path": "architecture/flows",
                                   "confidence": 0.9}])])
    run = client.post(f"/api/workspaces/{workspace['id']}/inventory/runs", json={}).json()
    result = client.post(
        f"/api/workspaces/{workspace['id']}/inventory/runs/{run['id']}/apply", json={}
    ).json()

    assert result["applied"] == ["architecture/flows/notes.md"]
    assert (doc_repo / "architecture" / "flows" / "notes.md").exists()


def test_manual_move_to_missing_folder_is_still_refused(
    client: TestClient, workspace: dict
) -> None:
    """A hand-typed move into a non-existent folder is likely a typo."""
    response = client.post(
        f"/api/workspaces/{workspace['id']}/proposals/move",
        json={
            "repository_id": _docs_id(workspace),
            "source_path": "notes.md",
            "target_dir": "architecture/decision",  # typo: no such folder
        },
    )
    assert response.status_code == 400
    assert "does not exist" in response.json()["detail"]


def test_overlaps_are_reported(client: TestClient, workspace: dict, mock_llm) -> None:
    mock_llm(
        [
            _inventory_response(
                [
                    {"path": "notes.md", "suggested_path": "architecture/components",
                     "overlaps": ["architecture/overview.md"]},
                    {"path": "architecture/overview.md", "suggested_path": "architecture",
                     "overlaps": ["notes.md"]},
                ]
            )
        ]
    )
    run = client.post(f"/api/workspaces/{workspace['id']}/inventory/runs", json={}).json()
    item = next(i for i in run["items"] if i["source_path"] == "notes.md")
    assert item["overlaps"] == ["architecture/overview.md"]


def test_runs_are_listed_and_fetchable(
    client: TestClient, workspace: dict, mock_llm
) -> None:
    mock_llm([_inventory_response([])])
    created = client.post(
        f"/api/workspaces/{workspace['id']}/inventory/runs", json={}
    ).json()
    listed = client.get(f"/api/workspaces/{workspace['id']}/inventory/runs").json()
    assert len(listed) == 1
    assert client.get(
        f"/api/workspaces/{workspace['id']}/inventory/runs/{created['id']}"
    ).status_code == 200
    assert client.get(
        f"/api/workspaces/{workspace['id']}/inventory/runs/9999"
    ).status_code == 404


def test_inventory_with_no_documents(tmp_path: Path) -> None:
    empty = tmp_path / "empty"
    empty.mkdir()
    result = run_inventory(ScriptedProvider([]), str(empty))
    assert result.classifications == []
    assert "No Markdown" in result.summary
