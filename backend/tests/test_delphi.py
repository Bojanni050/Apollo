"""The analysis: what Delphi makes of a collection, and what happens to it.

The motor is exercised through a scripted provider, so the properties checked
here are the ones that hold no matter what the model says:

* **It reports, it does not act.** No pass writes, moves or removes a file; the
  collection is compared byte for byte before and after.
* **A claim without evidence never reaches the reader**, and neither does a
  claim about a document that is not in the collection.
* **A corpus that cannot answer the question says so** instead of inventing a
  comparison -- one document is not a collection.
* **The reader's decision survives the next pass.** A dismissed finding is
  refreshed in its evidence, never in its status, and a finding found twice is
  one row rather than two.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.llm.base import LLMError, LLMResponse
from app.services.delphi import (
    DelphiError,
    SignalDraft,
    analyse,
    dismiss_signal,
    open_signal_count,
    record_signals,
    signals_for_document,
    signals_for_group,
)
from app.services.placement import create_group, place_document
from tests.test_chat_agent import ScriptedProvider

EVIDENCE = "Planning 2026 states May 2026 where planning 2025 states January 2026."


def _response(entries: list[dict]) -> LLMResponse:
    return LLMResponse(content=json.dumps({"documents": entries}))


def _tree(root: Path) -> dict[str, bytes]:
    """Every file under root with its bytes, for before/after comparisons."""
    return {
        str(p.relative_to(root)).replace("\\", "/"): p.read_bytes()
        for p in sorted(root.rglob("*"))
        if p.is_file()
    }


@pytest.fixture()
def collection(tmp_path: Path) -> Path:
    """Three documents that genuinely stand in relation to each other."""
    root = tmp_path / "verzameling"
    root.mkdir()
    (root / "planning-2025.md").write_text(
        "# Planning 2025\n\n## Migratie\n\nWe migreren in januari 2026.\n",
        encoding="utf-8",
    )
    (root / "planning-2026.md").write_text(
        "# Planning 2026\n\n## Migratie\n\nWe migreren in mei 2026, uitgesteld vanwege "
        "de release.\n\n## Budget\n\nHet budget is vastgesteld op 40.000.\n",
        encoding="utf-8",
    )
    (root / "budget-notities.md").write_text(
        "# Notities over het budget\n\nHet budget is 45.000.\n",
        encoding="utf-8",
    )
    return root


# ---------------------------------------------------------------------------
# The pass
# ---------------------------------------------------------------------------


def test_a_pass_reports_what_stands_out_with_its_evidence(collection: Path) -> None:
    provider = ScriptedProvider(
        [
            _response(
                [
                    {
                        "path": "planning-2025.md",
                        "signals": [
                            {
                                "kind": "outdated",
                                "reference": "planning-2026.md",
                                "why": EVIDENCE,
                            }
                        ],
                        "confidence": 0.9,
                    },
                    {
                        "path": "budget-notities.md",
                        "signals": [
                            {
                                "kind": "conflict",
                                "reference": "planning-2026.md",
                                "why": "The two documents give different budgets.",
                            }
                        ],
                        "confidence": 0.7,
                    },
                    {"path": "planning-2026.md", "signals": []},
                ]
            )
        ]
    )

    result = analyse(provider, str(collection))

    assert result.analysed == 3
    assert result.documents == 3
    assert result.errors == []
    assert [(s.path, s.kind) for s in result.signals] == [
        ("planning-2025.md", "outdated"),
        ("budget-notities.md", "conflict"),
    ]
    assert result.signals[0].why == EVIDENCE
    assert result.signals[0].reference == "planning-2026.md"
    assert result.signals[0].confidence == 0.9


def test_a_document_the_model_invented_is_ignored(collection: Path) -> None:
    provider = ScriptedProvider(
        [
            _response(
                [
                    {
                        "path": "not-on-disk.md",
                        "signals": [{"kind": "new", "why": "It says something new."}],
                    }
                ]
            )
        ]
    )

    result = analyse(provider, str(collection))

    assert result.signals == []


def test_a_bare_claim_without_evidence_never_reaches_the_reader(
    collection: Path,
) -> None:
    """The rule the whole phase turns on: a claim must be checkable."""
    provider = ScriptedProvider(
        [
            _response(
                [
                    {
                        "path": "planning-2025.md",
                        "signals": [
                            {"kind": "outdated", "reference": "planning-2026.md"},
                            {
                                "kind": "duplicate",
                                "reference": "planning-2026.md",
                                "why": EVIDENCE,
                            },
                        ],
                    }
                ]
            )
        ]
    )

    result = analyse(provider, str(collection))

    assert [s.kind for s in result.signals] == ["duplicate"]
    assert all(s.why for s in result.signals)


def test_the_model_is_given_every_path_so_a_reference_can_be_checked(
    collection: Path,
) -> None:
    """The whole corpus travels with the batch: a signal is relative to peers."""
    provider = ScriptedProvider([_response([])])

    analyse(provider, str(collection))

    sent = provider.calls[0][1]["content"]
    for path in ("planning-2025.md", "planning-2026.md", "budget-notities.md"):
        assert path in sent


def test_one_document_is_not_a_collection(tmp_path: Path) -> None:
    root = tmp_path / "een"
    root.mkdir()
    (root / "notities.md").write_text("# Notities\n", encoding="utf-8")
    provider = ScriptedProvider([_response([])])

    result = analyse(provider, str(root))

    assert result.signals == []
    assert result.errors
    # Refused before the model was asked: there is no comparison to be had.
    assert provider.calls == []


def test_a_pass_writes_nothing_to_disk(collection: Path) -> None:
    """The property the whole phase rests on, checked on the filesystem."""
    before = _tree(collection)
    provider = ScriptedProvider(
        [
            _response(
                [
                    {
                        "path": "planning-2025.md",
                        "signals": [
                            {
                                "kind": "outdated",
                                "reference": "planning-2026.md",
                                "why": EVIDENCE,
                            }
                        ],
                    }
                ]
            )
        ]
    )

    analyse(provider, str(collection))

    assert _tree(collection) == before


def test_a_response_that_is_not_json_is_reported_not_raised(
    collection: Path,
) -> None:
    provider = ScriptedProvider([LLMResponse(content="I could not do that.")])

    result = analyse(provider, str(collection))

    # The pass is a statement about what happened, so the failure belongs in it.
    assert result.analysed == 3
    assert result.signals == []
    assert any("no JSON" in e for e in result.errors)


def test_a_provider_failure_is_reported_and_the_pass_ends(collection: Path) -> None:
    class Broken(ScriptedProvider):
        def chat(self, messages, tools=None, temperature=None, max_output_tokens=None):
            self.calls.append(messages)
            raise LLMError("the provider is down")

    result = analyse(Broken([]), str(collection))

    assert result.signals == []
    assert result.errors
    assert "the provider is down" in result.errors[0]


# ---------------------------------------------------------------------------
# Keeping the findings
# ---------------------------------------------------------------------------


def _draft(
    path: str, kind: str, reference: str | None = None, why: str = EVIDENCE
) -> SignalDraft:
    return SignalDraft(
        path=path, kind=kind, reference=reference, why=why, confidence=0.8
    )


def _doc_repo_id(workspace: dict) -> int:
    return next(r["id"] for r in workspace["repositories"] if r["name"] == "gaia-docs")


def test_the_same_finding_twice_is_one_row_with_fresher_evidence(
    session: Session, workspace: dict
) -> None:
    repo = _doc_repo_id(workspace)
    record_signals(session, workspace["id"], repo, [_draft("notes.md", "outdated", "README.md")])

    record_signals(
        session,
        workspace["id"],
        repo,
        [_draft("notes.md", "outdated", "README.md", why="It now also states a new date.")],
    )

    found = signals_for_document(session, workspace["id"], repo, "notes.md")
    assert len(found) == 1
    assert found[0].why == "It now also states a new date."


def test_a_dismissed_finding_survives_the_next_pass(
    session: Session, workspace: dict
) -> None:
    """"Not this one" has to outlast the next analysis, or it is not a decision."""
    repo = _doc_repo_id(workspace)
    stored = record_signals(
        session, workspace["id"], repo, [_draft("notes.md", "outdated", "README.md")]
    )

    dismiss_signal(session, workspace["id"], stored[0].id)
    record_signals(
        session,
        workspace["id"],
        repo,
        [_draft("notes.md", "outdated", "README.md", why="Different evidence.")],
    )

    assert signals_for_document(session, workspace["id"], repo, "notes.md") == []
    hidden = signals_for_document(
        session, workspace["id"], repo, "notes.md", include_dismissed=True
    )
    assert [s.status for s in hidden] == ["dismissed"]
    # The evidence is refreshed even though the decision is not.
    assert hidden[0].why == "Different evidence."


def test_findings_come_back_most_actionable_first(
    session: Session, workspace: dict
) -> None:
    """Out of date invites a decision; new mostly informs. One fixed order."""
    repo = _doc_repo_id(workspace)
    record_signals(
        session,
        workspace["id"],
        repo,
        [_draft("notes.md", "new", None), _draft("notes.md", "outdated", "README.md")],
    )

    found = signals_for_document(session, workspace["id"], repo, "notes.md")

    assert [s.kind for s in found] == ["outdated", "new"]


def test_a_group_reports_the_findings_of_its_documents(
    session: Session, workspace: dict
) -> None:
    repo = _doc_repo_id(workspace)
    group = create_group(session, workspace["id"], "Planning")
    place_document(session, workspace["id"], group.id, repo, "notes.md")
    record_signals(
        session,
        workspace["id"],
        repo,
        [
            _draft("notes.md", "outdated", "README.md"),
            _draft("README.md", "new", None),
        ],
    )

    found = signals_for_group(session, workspace["id"], group.id)

    assert [s.file_path for s in found] == ["notes.md"]


def test_the_open_count_ignores_what_the_reader_dismissed(
    session: Session, workspace: dict
) -> None:
    repo = _doc_repo_id(workspace)
    stored = record_signals(
        session,
        workspace["id"],
        repo,
        [_draft("notes.md", "outdated", "README.md"), _draft("notes.md", "new", None)],
    )

    assert open_signal_count(session, workspace["id"]) == 2
    dismiss_signal(session, workspace["id"], stored[0].id)
    assert open_signal_count(session, workspace["id"]) == 1


def test_a_finding_from_another_workspace_cannot_be_dismissed(
    session: Session, workspace: dict, client: TestClient
) -> None:
    other = client.post("/api/workspaces", json={"name": "Ander"}).json()
    repo = _doc_repo_id(workspace)
    stored = record_signals(
        session, workspace["id"], repo, [_draft("notes.md", "new", None)]
    )

    with pytest.raises(DelphiError):
        dismiss_signal(session, other["id"], stored[0].id)


