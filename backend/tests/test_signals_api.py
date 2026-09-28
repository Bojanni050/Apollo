"""The findings' HTTP surface, the way the signal bar will use it.

Two properties are worth more than the shape of the responses:

* **The analysis cannot change a document.** Checked against the route table
  rather than hoped for, because that is the only place the property lives.
* **"Nothing stood out" and "nobody looked" must not read the same way.** A
  workspace with no model gets a refusal, and a collection that cannot answer
  the question gets a sentence saying why -- never a clean, confident, empty
  result.
"""
from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from app.llm.base import LLMError, LLMNotConfigured, LLMResponse
from app.main import create_app
from tests.test_chat_agent import ScriptedProvider

EVIDENCE = "Planning 2026 states May 2026 where planning 2025 states January 2026."


def _response(entries: list[dict]) -> LLMResponse:
    return LLMResponse(content=json.dumps({"documents": entries}))


@pytest.fixture()
def mock_llm(monkeypatch: pytest.MonkeyPatch):
    def install(turns: list[LLMResponse]) -> ScriptedProvider:
        provider = ScriptedProvider(turns)
        monkeypatch.setattr("app.api.routes_signals.get_provider", lambda *a, **k: provider)
        return provider

    return install


def _doc_repo_id(workspace: dict) -> int:
    return next(r["id"] for r in workspace["repositories"] if r["name"] == "gaia-docs")


def _one_finding() -> LLMResponse:
    return _response(
        [
            {
                "path": "notes.md",
                "signals": [
                    {
                        "kind": "outdated",
                        "reference": "README.md",
                        "why": EVIDENCE,
                    }
                ],
                "confidence": 0.9,
            }
        ]
    )


# ---------------------------------------------------------------------------
# The one button
# ---------------------------------------------------------------------------


def test_the_button_reports_what_it_found(
    client: TestClient, workspace: dict, mock_llm
) -> None:
    mock_llm([_one_finding()])

    response = client.post(f"/api/workspaces/{workspace['id']}/delphi/analyze", json={})

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["documents"] == 6
    assert body["analysed"] == 6
    assert body["open_signals"] == 1
    # The sentence says what happened, and says what did not happen.
    assert "Nothing was moved or changed." in body["summary"]
    (finding,) = body["signals"]
    assert finding["kind"] == "outdated"
    assert finding["label"] == "Older information"
    assert finding["reference"] == "README.md"
    assert finding["why"] == EVIDENCE
    assert finding["status"] == "new"


def test_without_a_model_the_button_says_so_rather_than_finding_nothing(
    client: TestClient, workspace: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An empty result and a refusal must not be indistinguishable."""

    def refuse(*args, **kwargs):
        raise LLMNotConfigured("No LLM is configured. Add one in Settings.")

    monkeypatch.setattr("app.api.routes_signals.get_provider", refuse)

    response = client.post(f"/api/workspaces/{workspace['id']}/delphi/analyze", json={})

    assert response.status_code == 503
    assert "No LLM is configured" in response.json()["detail"]


def test_a_workspace_whose_only_collection_is_the_inbox_can_be_analysed(
    client: TestClient, mock_llm
) -> None:
    """The common workspace in the basis workflow: files dropped in, nothing
    registered. The button has to work there, or the first thing the reader does
    is the thing that fails."""
    ws = client.post("/api/workspaces", json={"name": "Verzameling"}).json()
    for name in ("planning-2025.md", "planning-2026.md"):
        client.post(
            f"/api/workspaces/{ws['id']}/inbox/upload",
            files={"file": (name, b"# Planning\n\nWe migreren in mei.\n", "text/markdown")},
        )
    mock_llm([_one_finding()])

    response = client.post(f"/api/workspaces/{ws['id']}/delphi/analyze", json={})

    assert response.status_code == 200
    body = response.json()
    assert body["documents"] == 2
    assert body["repository_id"] is not None


def test_an_empty_workspace_is_told_to_add_documents_first(client: TestClient) -> None:
    ws = client.post("/api/workspaces", json={"name": "Leeg"}).json()

    response = client.post(f"/api/workspaces/{ws['id']}/delphi/analyze", json={})

    assert response.status_code == 409
    assert "no documents yet" in response.json()["detail"]


def test_a_source_repository_is_not_something_delphi_judges(
    client: TestClient, workspace: dict, mock_llm
) -> None:
    """Code is not a document, and "is this code out of date" is another
    question."""
    mock_llm([_one_finding()])
    source = next(r for r in workspace["repositories"] if r["kind"] == "source")

    response = client.post(
        f"/api/workspaces/{workspace['id']}/delphi/analyze",
        json={"repository_id": source["id"]},
    )

    assert response.status_code == 400
    assert "not source repositories" in response.json()["detail"]


def test_a_provider_that_is_misconfigured_is_a_server_error_not_a_silent_empty(
    client: TestClient, workspace: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A configured-but-broken provider must not be reported as "nothing found"."""

    def broken(*args, **kwargs):
        raise LLMError("the provider rejected our credentials")

    monkeypatch.setattr("app.api.routes_signals.get_provider", broken)

    response = client.post(f"/api/workspaces/{workspace['id']}/delphi/analyze", json={})

    assert response.status_code == 500
    assert "credentials" in response.json()["detail"]


# ---------------------------------------------------------------------------
# Listing the findings
# ---------------------------------------------------------------------------


def _analyse(client: TestClient, workspace: dict, mock_llm) -> dict:
    mock_llm([_one_finding()])
    return client.post(
        f"/api/workspaces/{workspace['id']}/delphi/analyze", json={}
    ).json()


def test_a_finding_is_listed_for_its_document(
    client: TestClient, workspace: dict, mock_llm
) -> None:
    _analyse(client, workspace, mock_llm)

    listed = client.get(
        f"/api/workspaces/{workspace['id']}/signals",
        params={"repository_id": _doc_repo_id(workspace), "path": "notes.md"},
    )

    assert listed.status_code == 200
    (finding,) = listed.json()
    assert finding["kind"] == "outdated"
    assert finding["why"] == EVIDENCE


def test_dismissing_hides_the_finding_and_leaves_the_document(
    client: TestClient, workspace: dict, mock_llm, doc_repo
) -> None:
    _analyse(client, workspace, mock_llm)
    finding = client.get(
        f"/api/workspaces/{workspace['id']}/signals",
        params={"repository_id": _doc_repo_id(workspace), "path": "notes.md"},
    ).json()[0]
    before = (doc_repo / "notes.md").read_text(encoding="utf-8")

    dismissed = client.post(
        f"/api/workspaces/{workspace['id']}/signals/{finding['id']}/dismiss"
    )

    assert dismissed.status_code == 200
    assert dismissed.json()["status"] == "dismissed"
    assert client.get(
        f"/api/workspaces/{workspace['id']}/signals",
        params={"repository_id": _doc_repo_id(workspace), "path": "notes.md"},
    ).json() == []
    # The document is exactly as it was: dismissing a finding is not an action on
    # the file it is about.
    assert (doc_repo / "notes.md").read_text(encoding="utf-8") == before


def test_a_dismissed_finding_can_still_be_asked_for(
    client: TestClient, workspace: dict, mock_llm
) -> None:
    """Hiding is not deleting: the reader can always see what they dismissed."""
    _analyse(client, workspace, mock_llm)
    finding = client.get(
        f"/api/workspaces/{workspace['id']}/signals",
        params={"repository_id": _doc_repo_id(workspace), "path": "notes.md"},
    ).json()[0]
    client.post(f"/api/workspaces/{workspace['id']}/signals/{finding['id']}/dismiss")

    hidden = client.get(
        f"/api/workspaces/{workspace['id']}/signals",
        params={
            "repository_id": _doc_repo_id(workspace),
            "path": "notes.md",
            "include_dismissed": "true",
        },
    )

    assert [f["status"] for f in hidden.json()] == ["dismissed"]


def test_the_findings_of_a_group_come_back_together(
    client: TestClient, workspace: dict, mock_llm
) -> None:
    _analyse(client, workspace, mock_llm)
    group = client.post(
        f"/api/workspaces/{workspace['id']}/groups", json={"name": "Scratch"}
    ).json()
    client.post(
        f"/api/workspaces/{workspace['id']}/groups/{group['id']}/documents",
        json={"repository_id": _doc_repo_id(workspace), "path": "notes.md"},
    )

    listed = client.get(
        f"/api/workspaces/{workspace['id']}/signals", params={"group_id": group["id"]}
    )

    assert listed.status_code == 200
    assert [f["file_path"] for f in listed.json()] == ["notes.md"]


def test_a_group_from_another_workspace_is_not_found(
    client: TestClient, workspace: dict
) -> None:
    """Otherwise an id from elsewhere would answer with somebody else's findings."""
    other = client.post("/api/workspaces", json={"name": "Ander"}).json()
    group = client.post(
        f"/api/workspaces/{other['id']}/groups", json={"name": "Scratch"}
    ).json()

    response = client.get(
        f"/api/workspaces/{workspace['id']}/signals", params={"group_id": group["id"]}
    )

    assert response.status_code == 404


def test_asking_for_neither_a_document_nor_a_group_is_refused(
    client: TestClient, workspace: dict
) -> None:
    """The whole workspace is a different question, and one that would drown the
    reading pane the panel sits above."""
    response = client.get(f"/api/workspaces/{workspace['id']}/signals")

    assert response.status_code == 400
    assert "not both and not neither" in response.json()["detail"]


def test_asking_for_both_is_refused(client: TestClient, workspace: dict) -> None:
    response = client.get(
        f"/api/workspaces/{workspace['id']}/signals",
        params={
            "repository_id": _doc_repo_id(workspace),
            "path": "notes.md",
            "group_id": 1,
        },
    )

    assert response.status_code == 400


def test_a_path_without_a_repository_is_refused(
    client: TestClient, workspace: dict
) -> None:
    """A path is only a document together with the repository it is in: two
    repositories can both hold notes.md."""
    response = client.get(
        f"/api/workspaces/{workspace['id']}/signals", params={"path": "notes.md"}
    )

    assert response.status_code == 400
    assert "repository it is in" in response.json()["detail"]


def test_a_finding_cannot_be_dismissed_in_another_workspace(
    client: TestClient, workspace: dict, mock_llm
) -> None:
    _analyse(client, workspace, mock_llm)
    finding = client.get(
        f"/api/workspaces/{workspace['id']}/signals",
        params={"repository_id": _doc_repo_id(workspace), "path": "notes.md"},
    ).json()[0]
    other = client.post("/api/workspaces", json={"name": "Ander"}).json()

    response = client.post(
        f"/api/workspaces/{other['id']}/signals/{finding['id']}/dismiss"
    )

    assert response.status_code == 404


def test_the_badge_counts_what_is_waiting_not_what_was_just_found(
    client: TestClient, workspace: dict, mock_llm
) -> None:
    """A count that only rises while the app is open teaches the reader to
    ignore it, so it has to come from the stored decisions rather than from what
    this session happened to see."""
    _analyse(client, workspace, mock_llm)
    finding = client.get(
        f"/api/workspaces/{workspace['id']}/signals",
        params={"repository_id": _doc_repo_id(workspace), "path": "notes.md"},
    ).json()[0]
    client.post(f"/api/workspaces/{workspace['id']}/signals/{finding['id']}/dismiss")

    counted = client.get(f"/api/workspaces/{workspace['id']}/signals/count")

    assert counted.json()["open_signals"] == 0


def test_a_workspace_nobody_analysed_yet_counts_zero(
    client: TestClient, workspace: dict
) -> None:
    assert client.get(f"/api/workspaces/{workspace['id']}/signals/count").json() == {
        "open_signals": 0
    }


# ---------------------------------------------------------------------------
# The route table
# ---------------------------------------------------------------------------


def test_there_is_no_route_here_that_can_change_a_document() -> None:
    """The phase's promise, checked where it could be broken.

    The analysis reads documents and stores what it found. A PUT, PATCH or DELETE
    anywhere in this module would mean a finding could become a file operation,
    which is the one thing a signal must never be.
    """
    schema = create_app().openapi()
    paths = schema["paths"]
    # Sanity: the endpoints this test is about have to be in the table, or the
    # assertion below would pass over an empty set.
    assert "/api/workspaces/{workspace_id}/delphi/analyze" in paths
    assert "/api/workspaces/{workspace_id}/signals" in paths

    writes = [
        (path, method)
        for path, operations in paths.items()
        if "signal" in path or "delphi" in path
        for method in operations
        if method in ("put", "patch", "delete")
    ]

    assert writes == []

