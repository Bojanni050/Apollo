"""Tests for whole-document analysis and classification confidence.

The property under test throughout: a long document is classified from its
**whole** structure, not from its opening. A conclusion recorded in the last
section of a long architecture document must be visible to the classifier even
when the document is far larger than its share of the context budget.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.llm.base import LLMResponse
from app.llm.context import ContextBudget, count_tokens
from app.services.inventory import (
    LOW_CONFIDENCE_THRESHOLD,
    Classification,
    _parse_response,
    _system_prompt,
    _user_prompt,
    run_inventory,
)
from app.services.markdown_structure import (
    build_representation,
    parse_markdown,
    render_digest,
)
from tests.test_chat_agent import ScriptedProvider

#: A small share of budget, so the digest path is exercised in every test.
TIGHT = 400
LOOSE = 100_000


def long_document(sections: int = 30, filler: int = 200) -> str:
    """A document whose decisive content is in its final section."""
    body = "# Architecture Overview\n\nAn opening that says nothing decisive.\n"
    for index in range(sections):
        body += f"\n## Section {index}\n\nBackground for section {index}. "
        body += "padding text " * filler + "\n"
    body += "\n## Decision\n\nTHE DECISION WAS TO ADOPT POSTGRESQL.\n"
    return body


# ---------------------------------------------------------------------------
# Structural extraction
# ---------------------------------------------------------------------------


def test_title_and_headings_are_extracted() -> None:
    structure = parse_markdown("# Memory Component\n\nBody.\n\n## Storage\n\nMore.\n\n### Indexing\n\nDeep.\n")
    assert structure.title == "Memory Component"
    assert [(h.level, h.text) for h in structure.headings] == [
        (1, "Memory Component"),
        (2, "Storage"),
        (3, "Indexing"),
    ]


def test_section_boundaries_are_computed() -> None:
    structure = parse_markdown("# A\n\none\n\n## B\n\ntwo\n\n## C\n\nthree\n")
    labels = [s.heading.text if s.heading else "(preamble)" for s in structure.sections]
    assert labels == ["A", "B", "C"]
    for section in structure.sections:
        assert section.end_line >= section.start_line


def test_preamble_is_its_own_section() -> None:
    """Content before the first heading often states the purpose."""
    structure = parse_markdown("An opening note.\n\n# Title\n\nBody.\n")
    assert structure.sections[0].heading is None
    assert "opening note" in structure.sections[0].preview


def test_headings_inside_code_blocks_are_not_headings() -> None:
    """A '#' in a shell comment is not a heading -- a classic false positive."""
    structure = parse_markdown("# Real Title\n\n```bash\n# not a heading\ngrep x\n```\n")
    assert [h.text for h in structure.headings] == ["Real Title"]


def test_code_blocks_are_inventoried() -> None:
    document = "# T\n\n```python\nprint(1)\nprint(2)\n```\n\n```sql\nSELECT 1;\n```\n"
    structure = parse_markdown(document)
    assert [(b.language, b.line_count) for b in structure.code_blocks] == [
        ("python", 2),
        ("sql", 1),
    ]


def test_unterminated_code_fence_does_not_crash() -> None:
    assert len(parse_markdown("# T\n\n```python\nprint(1)\n").code_blocks) == 1


def test_links_are_extracted_but_images_are_not() -> None:
    structure = parse_markdown("# T\n\n[Runbook](ops/runbook.md) and ![logo](logo.png)\n")
    assert [(l.text, l.target) for l in structure.links] == [("Runbook", "ops/runbook.md")]


def test_front_matter_is_extracted_as_metadata() -> None:
    structure = parse_markdown("---\ntitle: Memory\nstatus: accepted\n---\n\n# Heading\n\nBody.\n")
    assert structure.front_matter == {"title": "Memory", "status": "accepted"}
    assert [h.text for h in structure.headings] == ["Heading"]


def test_malformed_front_matter_is_tolerated() -> None:
    structure = parse_markdown("---\n: :\nnot a mapping\n---\n\n# T\n")
    assert any(h.text == "T" for h in structure.headings)


# ---------------------------------------------------------------------------
# 1 & 2. Whole-document awareness within the budget
# ---------------------------------------------------------------------------


def test_short_document_is_sent_unchanged() -> None:
    """A document that fits must not be wrapped, annotated or altered."""
    document = "# Small\n\nA short document.\n"
    representation, mode = build_representation(document, LOOSE)
    assert mode == "full"
    assert representation == document


def test_long_document_uses_a_structural_digest() -> None:
    representation, mode = build_representation(long_document(), TIGHT)
    assert mode == "digest"
    assert "STRUCTURAL DIGEST" in representation
    assert "## Headings" in representation


def test_digest_respects_its_token_budget() -> None:
    for budget in (200, 400, 1_000, 4_000):
        representation, _ = build_representation(long_document(), budget)
        # A little framing slack is allowed, but the digest must not blow the
        # budget -- that is the whole point of budgeting it.
        assert count_tokens(representation) <= budget * 1.35, budget


def test_important_information_near_the_end_is_visible() -> None:
    """The whole point: a conclusion at the END must survive a small budget.

    A head-truncation of the same document would show none of this.
    """
    representation, _ = build_representation(long_document(), TIGHT)
    assert "ADOPT POSTGRESQL" in representation
    assert "Decision" in representation


def test_digest_covers_the_beginning_and_the_end() -> None:
    """Sampling is spread across the document, not taken in order."""
    representation, _ = build_representation(long_document(40, 200), 600)
    assert "Section 0" in representation
    assert "Section 39" in representation or "Decision" in representation


def test_document_with_many_headings_is_handled() -> None:
    document = "# Big\n" + "".join(f"\n## H{i}\n\nBody {i}.\n" for i in range(300))
    assert len(parse_markdown(document).headings) == 301

    representation, mode = build_representation(document, TIGHT)
    assert mode == "digest"
    assert count_tokens(representation) <= TIGHT * 1.35
    # A truncated outline must SAY it is truncated, never read as complete.
    assert "more headings" in representation


def test_digest_reports_how_many_sections_it_showed() -> None:
    structure = parse_markdown(long_document(60, 200))
    _, covered = render_digest(structure, 200)
    assert 0 < covered < len(structure.sections)


# ---------------------------------------------------------------------------
# 3. Empty and trivial documents
# ---------------------------------------------------------------------------


def test_empty_document_is_handled() -> None:
    structure = parse_markdown("")
    assert structure.has_content is False
    assert structure.sections == []
    assert "empty" in render_digest(structure, TIGHT)[0].lower()


def test_empty_document_sent_to_the_model() -> None:
    """An empty file must not vanish from the inventory."""
    representation, mode = build_representation("", TIGHT)
    assert mode == "full"
    assert representation == ""


def test_whitespace_only_document_is_handled() -> None:
    assert parse_markdown("   \n\n  \n").has_content is False


def test_document_with_no_headings_still_produces_a_digest() -> None:
    representation, mode = build_representation("Just prose. " * 20_000, TIGHT)
    assert mode == "digest"
    assert count_tokens(representation) <= TIGHT * 1.35


def test_document_that_is_only_code_is_handled() -> None:
    document = "# T\n\n```python\n" + "print(1)\n" * 20_000 + "```\n"
    representation, mode = build_representation(document, TIGHT)
    assert mode == "digest"
    # The code inventory survives even though there is no prose to preview.
    assert "Code blocks" in representation


# ---------------------------------------------------------------------------
# 4. Content preservation -- the source must never be modified
# ---------------------------------------------------------------------------


def test_representation_does_not_modify_the_source() -> None:
    document = long_document()
    before = document[:]
    build_representation(document, TIGHT)
    assert document == before


def test_run_inventory_does_not_modify_documents(tmp_path: Path) -> None:
    """The repository on disk is untouched by an inventory run."""
    document = tmp_path / "long.md"
    document.write_text(long_document(20, 100), encoding="utf-8")
    original = document.read_text(encoding="utf-8")
    before = document.stat().st_mtime_ns

    result = run_inventory(_provider("long.md", "foundation"), str(tmp_path))

    assert document.read_text(encoding="utf-8") == original
    assert document.stat().st_mtime_ns == before
    assert [c.path for c in result.classifications] == ["long.md"]


# ---------------------------------------------------------------------------
# 5. Classification confidence, ambiguity and alternatives
# ---------------------------------------------------------------------------


def _provider(path: str, suggested: str | None, **fields) -> ScriptedProvider:
    """A scripted provider returning one classification for one document."""
    payload = {
        "path": path,
        "purpose": "A purpose.",
        "suggested_path": suggested,
        "confidence": 0.9,
        "ambiguous": False,
        **fields,
    }
    return ScriptedProvider([
        LLMResponse(content=json.dumps({"classifications": [payload], "summary": "s"}))
    ])


def test_alternatives_are_parsed_and_validated() -> None:
    result = _parse_response(
        json.dumps({"classifications": [{
            "path": "a.md", "purpose": "p", "suggested_path": "architecture/decisions",
            "confidence": 0.5, "alternatives": ["operations", "nonsense/folder"],
        }]}),
        ["a.md"],
    )
    # Real folders are kept; an invented one is discarded as noise.
    assert result.classifications[0].alternatives == ["operations"]


def test_reason_is_parsed() -> None:
    result = _parse_response(
        json.dumps({"classifications": [{
            "path": "a.md", "purpose": "p", "suggested_path": "operations",
            "confidence": 0.8, "reason": "It lists deployment steps.",
        }]}),
        ["a.md"],
    )
    assert result.classifications[0].reason == "It lists deployment steps."


def test_low_confidence_is_flagged() -> None:
    low = Classification(path="a.md", purpose="p", suggested_path="operations",
                         confidence=0.3)
    high = Classification(path="b.md", purpose="p", suggested_path="operations",
                          confidence=0.95)
    assert low.low_confidence is True
    assert high.low_confidence is False
    assert 0.3 < LOW_CONFIDENCE_THRESHOLD <= 0.95


def test_ambiguous_document_is_marked_and_unplaced() -> None:
    result = _parse_response(
        json.dumps({"classifications": [{
            "path": "a.md", "purpose": "p", "suggested_path": None,
            "confidence": 0.2, "ambiguous": True,
        }]}),
        ["a.md"],
    )
    item = result.classifications[0]
    assert item.ambiguous is True
    assert item.suggested_path is None
    assert item.low_confidence is True


def test_empty_alternatives_is_the_default() -> None:
    """A model that says nothing about alternatives is not a failure."""
    result = _parse_response(
        json.dumps({"classifications": [{
            "path": "a.md", "purpose": "p", "suggested_path": "operations",
            "confidence": 0.9,
        }]}),
        ["a.md"],
    )
    assert result.classifications[0].alternatives == []
    assert result.classifications[0].reason is None


@pytest.fixture()
def small_context(monkeypatch: pytest.MonkeyPatch):
    """A small context window, so the digest path is actually reached.

    At the 128k default a 40-section document simply fits, which is the correct
    outcome -- so the digest has to be forced with a small window to be tested.
    """
    from app.config import settings

    monkeypatch.setattr(settings, "llm_context_tokens", 2_000)
    monkeypatch.setattr(settings, "llm_max_output_tokens", 200)


def test_digest_classification_is_marked_partial(
    tmp_path: Path, small_context
) -> None:
    """A classification from a digest is visibly different from a whole read."""
    (tmp_path / "long.md").write_text(long_document(40, 200), encoding="utf-8")
    (tmp_path / "short.md").write_text("# Short\n\nA small document.\n", encoding="utf-8")

    provider = ScriptedProvider([LLMResponse(content=json.dumps({"classifications": [
        {"path": "long.md", "purpose": "p", "suggested_path": "architecture", "confidence": 0.9},
        {"path": "short.md", "purpose": "p", "suggested_path": "operations", "confidence": 0.9},
    ]}))])
    result = run_inventory(provider, str(tmp_path))
    by_path = {c.path: c for c in result.classifications}

    assert by_path["long.md"].partial is True
    assert by_path["short.md"].partial is False


def test_digest_is_labelled_in_the_prompt(small_context) -> None:
    """The model is told which document it is seeing a digest of."""
    from app.config import settings

    documents = [
        {"path": "long.md", "content": long_document(40, 200)},
        {"path": "short.md", "content": "# Short\n\nSmall.\n"},
    ]
    rendered, digests = _user_prompt(documents, ContextBudget.from_settings(settings))

    assert digests == ["long.md"]
    assert "--- long.md (STRUCTURAL DIGEST, whole document) ---" in rendered
    # A whole document gets a plain header, with no misleading annotation.
    assert "--- short.md ---" in rendered


def test_system_prompt_explains_digests_and_confidence() -> None:
    prompt = _system_prompt()
    assert "STRUCTURAL DIGEST" in prompt
    assert "alternatives" in prompt
    assert "Be honest" in prompt
    assert "whole document" in prompt.lower()


# ---------------------------------------------------------------------------
# End-to-end through the API
# ---------------------------------------------------------------------------


def test_api_surfaces_confidence_and_alternatives(
    client, workspace, monkeypatch
) -> None:
    """A low-confidence, contested classification is visible as such."""
    from tests.test_inventory import _inventory_response

    monkeypatch.setattr(
        "app.api.routes_inventory.get_provider",
        lambda *a, **k: ScriptedProvider([
            _inventory_response([{
                "path": "notes.md", "suggested_path": "architecture/decisions",
                "confidence": 0.35,
            }])
        ]),
    )

    response = client.post(f"/api/workspaces/{workspace['id']}/inventory/runs", json={})
    item = next(i for i in response.json()["items"] if i["source_path"] == "notes.md")

    assert item["confidence"] == pytest.approx(0.35)
    assert item["low_confidence"] is True
    assert item["partial"] is False


def test_inventory_classifies_a_long_document_by_its_ending(
    client, workspace, doc_repo: Path, monkeypatch
) -> None:
    """End-to-end: an ADR whose decision is at the end still reaches the model.

    The decisive sentence is deliberately buried far past the old
    12,000-character cut, so this fails if the inventory reverts to
    head-truncation.
    """
    (doc_repo / "adr.md").write_text(
        "# Notes\n\n"
        + ("Background and deliberation that goes on. " * 1_500)
        + "\n## Decision\n\nWe will use PostgreSQL as the primary store.\n",
        encoding="utf-8",
    )
    from tests.test_inventory import _inventory_response

    seen: list[str] = []

    class Spy(ScriptedProvider):
        def chat(self, messages, tools=None, temperature=None, max_output_tokens=None):
            seen.append(messages[-1]["content"])
            return ScriptedProvider.chat(
                self, messages, tools=tools, temperature=temperature,
                max_output_tokens=max_output_tokens,
            )

    provider = Spy([
        _inventory_response([{"path": "adr.md",
                              "suggested_path": "architecture/decisions",
                              "confidence": 0.92}])
    ])
    monkeypatch.setattr("app.api.routes_inventory.get_provider", lambda *a, **k: provider)

    response = client.post(f"/api/workspaces/{workspace['id']}/inventory/runs", json={})

    assert response.status_code == 201
    item = next(i for i in response.json()["items"] if i["source_path"] == "adr.md")
    assert item["suggested_path"] == "architecture/decisions"
    # The decisive sentence, sitting at the very end, reached the model.
    assert any("use PostgreSQL as the primary store" in prompt for prompt in seen)
    # And the API reports that the classification rests on a digest.
    assert item["partial"] is True


def test_inventory_handles_an_empty_document_in_a_repository(
    client, workspace, doc_repo: Path, monkeypatch
) -> None:
    """An empty document must be reported, not silently dropped."""
    (doc_repo / "empty.md").write_text("", encoding="utf-8")
    from tests.test_inventory import _inventory_response

    monkeypatch.setattr(
        "app.api.routes_inventory.get_provider",
        lambda *a, **k: ScriptedProvider([_inventory_response([])]),
    )

    response = client.post(f"/api/workspaces/{workspace['id']}/inventory/runs", json={})

    assert response.status_code == 201
    paths = {i["source_path"] for i in response.json()["items"]}
    assert "empty.md" in paths
    # With no classification returned, it is reported as unclassified rather
    # than quietly absent from the plan.
    empty = next(i for i in response.json()["items"] if i["source_path"] == "empty.md")
    assert empty["ambiguous"] is True
    assert "not classified" in empty["purpose"]



