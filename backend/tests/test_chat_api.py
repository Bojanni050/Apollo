"""API tests for the chat endpoints, with the LLM provider mocked."""
from __future__ import annotations

from pathlib import Path

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


# ---------------------------------------------------------------------------
# The document in view
#
# "Ask about this document" was a button that opened a chat window and nothing
# more: the path never left the browser, so "this document" meant whatever the
# model decided it meant. These pin the part that had to change.
# ---------------------------------------------------------------------------


def _system_text(provider: ScriptedProvider) -> str:
    return " ".join(m.get("content", "") for m in provider.calls[0] if m.get("role") == "system")


def test_a_named_document_reaches_the_system_prompt(
    client: TestClient, workspace: dict, mock_llm
) -> None:
    provider = mock_llm([LLMResponse(content="ok")])
    base = f"/api/workspaces/{workspace['id']}/conversations"
    cid = client.post(base, json={}).json()["id"]

    client.post(
        f"{base}/{cid}/messages",
        json={"content": "what does this assume?", "document_path": "notes.md"},
    )
    system = _system_text(provider)
    assert "notes.md" in system
    assert "DOCUMENT IN VIEW" in system


def test_the_focus_names_the_documents_own_title(
    client: TestClient, workspace: dict, mock_llm
) -> None:
    # The path alone says where to look; the title says what the reader is
    # looking at, which is what makes the difference between "this document" and
    # whichever of the forty similar files the model would otherwise pick.
    # The fixture file is headed "# Memory component", not "memory.md".
    provider = mock_llm([LLMResponse(content="ok")])
    base = f"/api/workspaces/{workspace['id']}/conversations"
    cid = client.post(base, json={}).json()["id"]

    client.post(
        f"{base}/{cid}/messages",
        json={
            "content": "why?",
            "document_path": "architecture/components/memory.md",
        },
    )
    assert "Memory component" in _system_text(provider)


def test_no_document_means_no_focus(
    client: TestClient, workspace: dict, mock_llm
) -> None:
    provider = mock_llm([LLMResponse(content="ok")])
    base = f"/api/workspaces/{workspace['id']}/conversations"
    cid = client.post(base, json={}).json()["id"]

    client.post(f"{base}/{cid}/messages", json={"content": "general question"})
    assert "DOCUMENT IN VIEW" not in _system_text(provider)


def test_the_focus_is_a_pointer_and_not_the_documents_text(
    client: TestClient, workspace: dict, mock_llm, doc_repo
) -> None:
    # The file's contents are deliberately NOT sent. The agent has to read it
    # through read_document, so an answer is grounded in what is on disk rather
    # than in a copy the reading pane may be holding stale.
    provider = mock_llm([LLMResponse(content="ok")])
    base = f"/api/workspaces/{workspace['id']}/conversations"
    cid = client.post(base, json={}).json()["id"]

    client.post(
        f"{base}/{cid}/messages",
        json={"content": "read it", "document_path": "notes.md"},
    )
    secret_line = (doc_repo / "notes.md").read_text(encoding="utf-8").splitlines()[0]
    assert secret_line not in _system_text(provider)


def test_a_document_path_that_does_not_exist_is_refused(
    client: TestClient, workspace: dict, mock_llm
) -> None:
    # A focus the agent cannot follow is worse than none: the model would answer
    # "this document" from a guess while the reader believed it had been named.
    mock_llm([LLMResponse(content="ok")])
    base = f"/api/workspaces/{workspace['id']}/conversations"
    cid = client.post(base, json={}).json()["id"]

    response = client.post(
        f"{base}/{cid}/messages",
        json={"content": "what does this say?", "document_path": "nope/missing.md"},
    )
    assert response.status_code == 400
    assert "nope/missing.md" in response.json()["detail"]


def test_a_document_path_escaping_the_repository_is_refused(
    client: TestClient, workspace: dict, mock_llm
) -> None:
    mock_llm([LLMResponse(content="ok")])
    base = f"/api/workspaces/{workspace['id']}/conversations"
    cid = client.post(base, json={}).json()["id"]

    response = client.post(
        f"{base}/{cid}/messages",
        json={"content": "read this", "document_path": "../../../etc/passwd"},
    )
    assert response.status_code == 400


def test_a_directory_is_not_a_document(
    client: TestClient, workspace: dict, mock_llm
) -> None:
    mock_llm([LLMResponse(content="ok")])
    base = f"/api/workspaces/{workspace['id']}/conversations"
    cid = client.post(base, json={}).json()["id"]

    response = client.post(
        f"{base}/{cid}/messages",
        json={"content": "read this", "document_path": "architecture"},
    )
    assert response.status_code == 400


def test_the_conversation_records_the_question_not_the_focus(
    client: TestClient, workspace: dict, mock_llm, session
) -> None:
    """The transcript stays the reader's own words.

    The focus is a per-turn instruction to the model, not something the reader
    said. Prepending it to the stored message would put text in their mouth in
    every later export.
    """
    from app.models import Message

    mock_llm([LLMResponse(content="ok")])
    base = f"/api/workspaces/{workspace['id']}/conversations"
    cid = client.post(base, json={}).json()["id"]

    client.post(
        f"{base}/{cid}/messages",
        json={"content": "what does this assume?", "document_path": "notes.md"},
    )
    rows = session.query(Message).filter_by(conversation_id=cid, role="user").all()
    assert [m.content for m in rows] == ["what does this assume?"]


def test_the_focus_names_the_repository_the_path_belongs_to(
    client: TestClient, workspace: dict, mock_llm
) -> None:
    """A repository-relative path is not a file until you say which repository.

    Both fixtures hold a README.md, so "README.md" alone names two different
    files. The prompt has to carry the one the reader actually had open,
    otherwise the model resolves "this document" to whichever it looks at first
    and cites that as the thing on screen.
    """
    provider = mock_llm([LLMResponse(content="ok")])
    base = f"/api/workspaces/{workspace['id']}/conversations"
    cid = client.post(base, json={}).json()["id"]

    client.post(
        f"{base}/{cid}/messages",
        json={"content": "what does this assume?", "document_path": "README.md"},
    )
    system = _system_text(provider)
    # gaia-docs is registered first and is the documentation repository, so
    # that is the one this path must resolve to -- and it must be named.
    assert "gaia-docs: README.md" in system
    assert "gaia-service" not in system


def test_a_path_that_exists_only_in_another_repository_names_that_repository(
    client: TestClient, workspace: dict, mock_llm
) -> None:
    """Resolution keeps looking, and reports the repository it landed in.

    `src/memory.py` is not a document, but the point here is the repository
    attribution: the file lives in the source repository, so that is the name
    that must accompany the path.
    """
    provider = mock_llm([LLMResponse(content="ok")])
    base = f"/api/workspaces/{workspace['id']}/conversations"
    cid = client.post(base, json={}).json()["id"]

    # README.md exists in both repositories; name one that does not, so the
    # assertion is about attribution rather than about first-match ordering.
    response = client.post(
        f"{base}/{cid}/messages",
        json={"content": "read it", "document_path": "docs/only-here.md"},
    )
    assert response.status_code == 400

    # And the documented repository does resolve, with its own name attached.
    client.post(
        f"{base}/{cid}/messages",
        json={"content": "and this", "document_path": "notes.md"},
    )
    assert "gaia-docs: notes.md" in _system_text(provider)


def test_a_file_the_agent_cannot_read_is_not_accepted_as_a_focus(
    client: TestClient, workspace: dict, mock_llm
) -> None:
    """A focus must be followable.

    `src/memory.py` exists, is inside a registered repository, and is not a
    directory -- so before the suffix check it was accepted. But `read_document`
    refuses it, so the model would be told to read a file its only tool cannot
    open: the prompt would name a document and the answer would come from a
    guess. Refusing up front is the same rule as refusing a missing path.
    """
    mock_llm([LLMResponse(content="ok")])
    base = f"/api/workspaces/{workspace['id']}/conversations"
    cid = client.post(base, json={}).json()["id"]

    response = client.post(
        f"{base}/{cid}/messages",
        json={"content": "what does this do?", "document_path": "src/memory.py"},
    )
    assert response.status_code == 400
    assert "src/memory.py" in response.json()["detail"]


def test_a_binary_document_gets_no_title_rather_than_mojibake(
    client: TestClient, workspace: dict, mock_llm, doc_repo: Path
) -> None:
    """A PDF is a valid focus, but its bytes are not a heading.

    Decoding a PDF as UTF-8 with replacement characters produces a confident,
    wrong "title" in the system prompt. The path is still enough to identify
    the file, so the title is simply omitted for non-text documents.
    """
    provider = mock_llm([LLMResponse(content="ok")])
    pdf = doc_repo / "whitepaper.pdf"
    pdf.write_bytes(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n1 0 obj\n<<>>\nendobj\ntrailer\n")

    base = f"/api/workspaces/{workspace['id']}/conversations"
    cid = client.post(base, json={}).json()["id"]

    response = client.post(
        f"{base}/{cid}/messages",
        json={"content": "summarise this", "document_path": "whitepaper.pdf"},
    )
    assert response.status_code == 201
    system = _system_text(provider)
    assert "whitepaper.pdf" in system
    assert "(untitled)" in system
    # The replacement character is what a mis-decoded binary looks like.
    assert "\ufffd" not in system
