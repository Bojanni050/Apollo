"""API tests for the chat endpoints, with the LLM provider mocked."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.llm.base import LLMResponse
from tests.test_chat_agent import ScriptedProvider, tool_turn


@pytest.fixture()
def mock_llm(monkeypatch: pytest.MonkeyPatch):
    """Replace the provider factory so no network call is ever attempted."""

    def install(turns: list[LLMResponse]) -> ScriptedProvider:
        provider = ScriptedProvider(turns)
        monkeypatch.setattr("app.api.routes_chat.get_provider", lambda: provider)
        return provider

    return install


def test_chat_status_reports_unconfigured(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("app.api.routes_chat.is_configured", lambda: False)
    body = client.get("/api/workspaces/1/chat/status").json()
    assert body["llm_configured"] is False
    assert "openai_compatible" in body["providers"]


def test_conversation_crud(client: TestClient, workspace: dict) -> None:
    base = f"/api/workspaces/{workspace['id']}/conversations"
    created = client.post(base, json={"title": "Memory design", "mode": "explore"})
    assert created.status_code == 201
    cid = created.json()["id"]

    assert client.get(base).json()[0]["title"] == "Memory design"
    assert client.get(f"{base}/{cid}").json()["message_count"] == 0

    updated = client.patch(f"{base}/{cid}", json={"mode": "investigate"}).json()
    assert updated["mode"] == "investigate"
    assert client.delete(f"{base}/{cid}").status_code == 204
    assert client.get(f"{base}/{cid}").status_code == 404


def test_invalid_mode_is_rejected(client: TestClient, workspace: dict) -> None:
    response = client.post(
        f"/api/workspaces/{workspace['id']}/conversations", json={"mode": "yolo"}
    )
    assert response.status_code == 422
    assert "explore" in response.json()["detail"]


def test_mode_switch_preserves_messages(
    client: TestClient, workspace: dict, mock_llm
) -> None:
    mock_llm([LLMResponse(content="first answer")])
    base = f"/api/workspaces/{workspace['id']}/conversations"
    cid = client.post(base, json={}).json()["id"]

    client.post(f"{base}/{cid}/messages", json={"content": "first question"})

    for mode in ("investigate", "apply", "explore"):
        assert client.patch(f"{base}/{cid}", json={"mode": mode}).json()["mode"] == mode

    detail = client.get(f"{base}/{cid}").json()
    assert len(detail["messages"]) == 2  # nothing was discarded


def test_send_message_returns_both_turns(client: TestClient, workspace: dict, mock_llm) -> None:
    mock_llm([LLMResponse(content="Let us examine that.")])
    base = f"/api/workspaces/{workspace['id']}/conversations"
    cid = client.post(base, json={}).json()["id"]

    body = client.post(f"{base}/{cid}/messages", json={"content": "too complex"}).json()
    assert body["user_message"]["content"] == "too complex"
    assert body["assistant_message"]["content"] == "Let us examine that."


def test_send_message_returns_citations(client: TestClient, workspace: dict, mock_llm) -> None:
    mock_llm(
        [
            tool_turn("read_document", {"repository": "gaia-docs", "path": "notes.md"}),
            LLMResponse(content="Your notes mention memory."),
        ]
    )
    base = f"/api/workspaces/{workspace['id']}/conversations"
    cid = client.post(base, json={"mode": "investigate"}).json()["id"]

    body = client.post(f"{base}/{cid}/messages", json={"content": "what about memory"}).json()
    citations = body["assistant_message"]["citations"]
    assert citations[0]["path"] == "notes.md"
    assert citations[0]["repository"] == "gaia-docs"


def test_conversation_is_titled_from_first_question(
    client: TestClient, workspace: dict, mock_llm
) -> None:
    mock_llm([LLMResponse(content="ok")])
    base = f"/api/workspaces/{workspace['id']}/conversations"
    cid = client.post(base, json={}).json()["id"]
    client.post(f"{base}/{cid}/messages", json={"content": "Is the memory layer too complex?"})
    assert client.get(f"{base}/{cid}").json()["title"] == "Is the memory layer too complex?"


def test_conversation_survives_a_new_session(
    client: TestClient, workspace: dict, mock_llm, session
) -> None:
    """Messages are persisted, so a restart does not lose the discussion."""
    mock_llm([LLMResponse(content="remembered")])
    base = f"/api/workspaces/{workspace['id']}/conversations"
    cid = client.post(base, json={}).json()["id"]
    client.post(f"{base}/{cid}/messages", json={"content": "durable?"})

    from app.models import Message

    rows = session.query(Message).filter_by(conversation_id=cid).all()
    assert [m.content for m in rows] == ["durable?", "remembered"]


def test_send_message_without_llm_returns_503(
    client: TestClient, workspace: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.llm.base import LLMNotConfigured

    def boom():
        raise LLMNotConfigured("Set LLM_BASE_URL and LLM_MODEL in .env")

    monkeypatch.setattr("app.api.routes_chat.get_provider", boom)
    base = f"/api/workspaces/{workspace['id']}/conversations"
    cid = client.post(base, json={}).json()["id"]
    response = client.post(f"{base}/{cid}/messages", json={"content": "hello"})
    assert response.status_code == 503
    assert "LLM_BASE_URL" in response.json()["detail"]


def test_archived_conversation_rejects_messages(
    client: TestClient, workspace: dict, mock_llm
) -> None:
    mock_llm([LLMResponse(content="ok")])
    base = f"/api/workspaces/{workspace['id']}/conversations"
    cid = client.post(base, json={}).json()["id"]
    client.patch(f"{base}/{cid}", json={"archived": True})
    assert client.post(f"{base}/{cid}/messages", json={"content": "hi"}).status_code == 409
    # Archived conversations are hidden from the default listing.
    assert client.get(base).json() == []
    assert len(client.get(base, params={"include_archived": True}).json()) == 1


def test_workspace_without_repositories_cannot_chat(client: TestClient) -> None:
    ws = client.post("/api/workspaces", json={"name": "Empty"}).json()
    base = f"/api/workspaces/{ws['id']}/conversations"
    cid = client.post(base, json={}).json()["id"]
    response = client.post(f"{base}/{cid}/messages", json={"content": "hi"})
    assert response.status_code == 409
    assert "repositories" in response.json()["detail"]
