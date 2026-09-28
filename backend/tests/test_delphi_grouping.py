"""From findings to groups: the claims, and the reader's veto over them.

Two halves, tested for the properties that make them safe to run on somebody's
own documents without asking first.

*The claim.* A cluster is a bigger claim than a signal, so it is held to the
same standard: named rather than numbered, at least two real members, and a
reason pointing at the findings it came from. A cluster naming a document that
is not there is not a slightly-wrong cluster, it is a card the reader cannot
open.

*The veto.* Writing a proposal is where it could go wrong in ways the reader
would not notice: a document they placed themselves moved back, the archive
filled by a suggestion, a member lost in silence. Each of those is tested
against the database, not against a return value -- and the repository is
compared byte for byte before and after, because a group is a view and must stay
one.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from sqlalchemy.orm import Session

from app.llm.base import LLMError, LLMResponse
from app.models import ARCHIVE_CATEGORY
from app.services.delphi import SignalDraft
from app.services.delphi_grouping import (
    GroupDraft,
    parse_group_drafts,
    propose_clusters,
    propose_groups,
)
from app.services.placement import (
    create_group,
    get_or_create_archive,
    group_documents,
    list_groups,
    place_document,
)
from tests.test_chat_agent import ScriptedProvider

PATHS = {"notes.md", "README.md", "architecture/overview.md"}
WHY = "The findings say these two are the same subject, in different words."


def _cluster_response(groups: list[dict]) -> LLMResponse:
    return LLMResponse(content=json.dumps({"groups": groups}))


def _draft(name: str, paths: list[str], why: str = WHY) -> GroupDraft:
    return GroupDraft(name=name, paths=paths, why=why)


def _doc_repo_id(workspace: dict) -> int:
    return next(r["id"] for r in workspace["repositories"] if r["name"] == "gaia-docs")


def _tree(root: Path) -> dict[str, bytes]:
    return {
        str(p.relative_to(root)).replace("\\", "/"): p.read_bytes()
        for p in sorted(root.rglob("*"))
        if p.is_file() and ".git" not in p.parts
    }


# ---------------------------------------------------------------------------
# The claim
# ---------------------------------------------------------------------------


class TestParsing:
    def test_a_named_cluster_with_two_real_members_and_a_reason_is_kept(self) -> None:
        drafts = parse_group_drafts(
            [{"name": "Scratch notes", "paths": ["notes.md", "README.md"], "why": WHY}],
            PATHS,
        )

        assert [(d.name, d.paths) for d in drafts] == [
            ("Scratch notes", ["notes.md", "README.md"])
        ]
        assert drafts[0].why == WHY

    def test_a_cluster_of_one_document_is_refused(self) -> None:
        """One document is not a grouping; it is a document."""
        drafts = parse_group_drafts(
            [{"name": "Solo", "paths": ["notes.md"], "why": WHY}], PATHS
        )

        assert drafts == []

    def test_a_cluster_naming_a_document_that_is_not_there_keeps_the_rest(
        self,
    ) -> None:
        drafts = parse_group_drafts(
            [
                {
                    "name": "Scratch notes",
                    "paths": ["notes.md", "ghost.md", "README.md"],
                    "why": WHY,
                }
            ],
            PATHS,
        )

        # The model was right about the grouping and wrong about one path.
        # Dropping the whole cluster would throw away the part that was true.
        assert [d.paths for d in drafts] == [["notes.md", "README.md"]]

    def test_a_cluster_with_no_real_members_is_refused(self) -> None:
        drafts = parse_group_drafts(
            [{"name": "Fiction", "paths": ["a.md", "b.md"], "why": WHY}], PATHS
        )

        assert drafts == []

    def test_a_cluster_without_a_reason_is_refused(self) -> None:
        """Same rule as a signal: nobody can check a claim that shows no evidence."""
        drafts = parse_group_drafts(
            [{"name": "Scratch notes", "paths": ["notes.md", "README.md"]}], PATHS
        )

        assert drafts == []

    @pytest.mark.parametrize("name", ["", "   ", "planning/2026", "x" * 300])
    def test_a_name_that_is_not_a_name_is_refused(self, name: str) -> None:
        drafts = parse_group_drafts(
            [{"name": name, "paths": ["notes.md", "README.md"], "why": WHY}], PATHS
        )

        assert drafts == []

    def test_stray_whitespace_in_a_name_is_collapsed_rather_than_refused(self) -> None:
        """A model that wraps a name across two lines meant one name, and the
        card is a single line: normalising is the useful reading here."""
        drafts = parse_group_drafts(
            [{"name": "Scratch  notes\n", "paths": ["notes.md", "README.md"], "why": WHY}],
            PATHS,
        )

        assert [d.name for d in drafts] == ["Scratch notes"]

    def test_duplicate_members_are_counted_once(self) -> None:
        """Otherwise a model repeating a path could hold a group up on one member."""
        drafts = parse_group_drafts(
            [
                {
                    "name": "Scratch notes",
                    "paths": ["notes.md", "README.md", "notes.md"],
                    "why": WHY,
                }
            ],
            PATHS,
        )

        assert [d.paths for d in drafts] == [["notes.md", "README.md"]]

    def test_the_same_cluster_twice_is_one(self) -> None:
        entry = {"name": "Scratch notes", "paths": ["notes.md", "README.md"], "why": WHY}
        drafts = parse_group_drafts([entry, entry], PATHS)

        assert len(drafts) == 1

    def test_nonsense_is_not_an_error(self) -> None:
        for value in (None, 42, "groups", [object()], [{"paths": "notes.md"}]):
            assert isinstance(parse_group_drafts(value, PATHS), list)


# ---------------------------------------------------------------------------
# Asking the model
# ---------------------------------------------------------------------------


def test_the_model_is_given_the_findings_and_every_path() -> None:
    """Clustering is a claim about evidence, so the evidence has to be in the
    request -- and the paths, so a member can be checked against the collection."""

    findings = [
        SignalDraft(
            path="notes.md",
            kind="duplicate",
            reference="README.md",
            why=WHY,
            confidence=0.8,
        )
    ]
    provider = ScriptedProvider([_cluster_response([])])

    propose_clusters(provider, findings, ["notes.md", "README.md", "third.md"])

    sent = provider.calls[0][1]["content"]
    assert WHY in sent
    for path in ("notes.md", "README.md", "third.md"):
        assert path in sent


def test_no_findings_means_the_model_is_not_asked_to_group() -> None:
    """Nothing connected anything, so there is nothing to group on. Asking anyway
    invites a structure invented out of nothing."""
    provider = ScriptedProvider([_cluster_response([])])

    assert propose_clusters(provider, [], ["notes.md", "README.md"]) == []
    assert provider.calls == []


def test_a_provider_failure_leaves_the_findings_alone() -> None:
    """The findings are already recorded; a clustering pass that fails must not
    take them down with it."""

    class Broken(ScriptedProvider):
        def chat(self, messages, tools=None, temperature=None, max_output_tokens=None):
            self.calls.append(messages)
            raise LLMError("the provider is down")


    findings = [
        SignalDraft(path="notes.md", kind="new", reference=None, why=WHY, confidence=0.5)
    ]

    assert propose_clusters(Broken([]), findings, ["notes.md", "README.md"]) == []


def test_an_unusable_response_yields_no_clusters() -> None:
    provider = ScriptedProvider([LLMResponse(content="I could not do that.")])


    findings = [
        SignalDraft(path="notes.md", kind="new", reference=None, why=WHY, confidence=0.5)
    ]

    assert propose_clusters(provider, findings, ["notes.md", "README.md"]) == []


def test_a_proposed_cluster_comes_back_from_the_model() -> None:
    provider = ScriptedProvider(
        [
            _cluster_response(
                [
                    {
                        "name": "Scratch notes",
                        "paths": ["notes.md", "README.md"],
                        "why": WHY,
                    }
                ]
            )
        ]
    )


    findings = [
        SignalDraft(
            path="notes.md",
            kind="duplicate",
            reference="README.md",
            why=WHY,
            confidence=0.8,
        )
    ]

    drafts = propose_clusters(provider, findings, ["notes.md", "README.md"])

    assert [d.name for d in drafts] == ["Scratch notes"]


# ---------------------------------------------------------------------------
# Writing the proposals
# ---------------------------------------------------------------------------


def test_a_proposal_creates_a_group_and_places_its_members(
    session: Session, workspace: dict
) -> None:
    repo = _doc_repo_id(workspace)

    proposals = propose_groups(
        session, workspace["id"], repo, [_draft("Scratch", ["notes.md", "README.md"])]
    )

    (proposal,) = proposals
    assert proposal.name == "Scratch"
    assert sorted(proposal.placed) == ["README.md", "notes.md"]
    group = next(
        g for g in list_groups(session, workspace["id"]) if g.id == proposal.group_id
    )
    # Marked as Delphi's, so the board can say who proposed it, and carrying the
    # reason, so the card explains itself without opening anything.
    assert group.source == "ai"
    assert group.description == WHY
    members = group_documents(session, workspace["id"], group.id)
    assert sorted(p.file_path for p in members) == ["README.md", "notes.md"]


def test_proposing_a_group_writes_no_file(
    session: Session, workspace: dict, doc_repo
) -> None:
    """A group is a view. The repository is compared byte for byte, because the
    cheapest way to be wrong here is to move a file and call it organisation."""
    before = _tree(doc_repo)

    propose_groups(
        session,
        workspace["id"],
        _doc_repo_id(workspace),
        [_draft("Scratch", ["notes.md", "README.md"])],
    )

    assert _tree(doc_repo) == before


def test_a_document_the_reader_placed_is_left_alone(
    session: Session, workspace: dict
) -> None:
    """The one property that makes correcting Delphi safe: a reader who disagrees
    with a proposal is never argued out of their own arrangement."""
    repo = _doc_repo_id(workspace)
    mine = create_group(session, workspace["id"], "Mijn eigen plek")
    place_document(session, workspace["id"], mine.id, repo, "notes.md", placed_by="user")

    proposals = propose_groups(
        session,
        workspace["id"],
        repo,
        [_draft("Scratch", ["notes.md", "README.md"])],
    )

    (proposal,) = proposals
    assert proposal.placed == ["README.md"]
    assert proposal.left_alone == ["notes.md"]
    # Still in the reader's group, and still marked as theirs.
    mine_members = group_documents(session, workspace["id"], mine.id)
    assert [p.file_path for p in mine_members] == ["notes.md"]
    theirs = next(g for g in list_groups(session, workspace["id"]) if g.name == "Scratch")
    assert all(
        p.placed_by == "ai"
        for p in group_documents(session, workspace["id"], theirs.id)
    )


def test_a_second_pass_lands_in_the_same_group(session: Session, workspace: dict) -> None:
    """A second run of the same analysis must not stack a second copy of a group
    the reader can already see on the board."""
    repo = _doc_repo_id(workspace)
    draft = _draft("Scratch", ["notes.md", "README.md"])

    first = propose_groups(session, workspace["id"], repo, [draft])
    second = propose_groups(session, workspace["id"], repo, [draft])

    assert first[0].group_id == second[0].group_id
    names = [g.name for g in list_groups(session, workspace["id"])]
    assert names.count("Scratch") == 1


def test_a_group_the_reader_made_keeps_its_source(session: Session, workspace: dict) -> None:
    """Delphi joining a group the reader built does not turn it into a proposal."""
    repo = _doc_repo_id(workspace)
    mine = create_group(
        session, workspace["id"], "Scratch", description="Mijn eigen groep"
    )

    proposals = propose_groups(
        session, workspace["id"], repo, [_draft("Scratch", ["notes.md"])]
    )

    group = next(g for g in list_groups(session, workspace["id"]) if g.id == mine.id)
    assert group.source == "user"
    # The description the reader may have edited is not rewritten.
    assert group.description == "Mijn eigen groep"
    assert proposals[0].group_id == mine.id


def test_a_cluster_named_after_the_archive_is_refused(
    session: Session, workspace: dict
) -> None:
    """The archive is a destination a reader chooses. A model proposing it would
    otherwise file documents, and filing is a decision."""
    repo = _doc_repo_id(workspace)
    archive = get_or_create_archive(session, workspace["id"])

    proposals = propose_groups(
        session,
        workspace["id"],
        repo,
        [_draft(ARCHIVE_CATEGORY, ["notes.md", "README.md"])],
    )

    assert proposals == []
    assert group_documents(session, workspace["id"], archive.id) == []


def test_a_member_that_is_no_longer_there_is_reported(
    session: Session, workspace: dict
) -> None:
    """A group that quietly lost a member reads as a group of the size it shows."""
    repo = _doc_repo_id(workspace)

    proposals = propose_groups(
        session, workspace["id"], repo, [_draft("Scratch", ["notes.md", "ghost.md"])]
    )

    assert proposals[0].placed == ["notes.md"]
    assert proposals[0].unavailable == ["ghost.md"]


def test_a_document_the_reader_took_over_is_not_moved_back(
    session: Session, workspace: dict
) -> None:
    """The other direction of the veto: a member the reader moved out and back has
    become theirs, and the next pass leaves it alone."""
    repo = _doc_repo_id(workspace)
    propose_groups(
        session, workspace["id"], repo, [_draft("Scratch", ["notes.md", "README.md"])]
    )
    group = next(g for g in list_groups(session, workspace["id"]) if g.name == "Scratch")
    placement = next(
        p
        for p in group_documents(session, workspace["id"], group.id)
        if p.file_path == "notes.md"
    )

    # The reader drags it out and puts it back, which is how a person says "yes,
    # this one belongs here" without touching the rest.
    placement.placed_by = "user"
    session.commit()

    proposals = propose_groups(
        session, workspace["id"], repo, [_draft("Scratch", ["notes.md", "README.md"])]
    )

    assert proposals[0].left_alone == ["notes.md"]
    assert proposals[0].placed == ["README.md"]



