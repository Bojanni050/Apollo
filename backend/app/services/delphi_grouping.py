"""From findings to groups: what belongs together, and the reader's veto over it.

A *signal* says something about one document relative to another. A *group* says
several documents belong side by side. The second is a bigger claim than the
first, and it deserves the same treatment: proposed, evidenced, and refused the
moment the reader disagrees.

Why the clustering is its own pass. The reading pass sees eight documents at a
time, because that is what fits the context window, and a cluster spanning two
batches would be invisible to it. The clustering pass is handed the *findings*
instead -- a few hundred tokens for the whole collection -- so it can see every
document at once and name a group after what the findings actually connect. It
never re-reads a document, so it cannot invent structure the evidence does not
contain.

Four rules, and they are the point of the module:

* **A group is a view.** Writing one adds a row to ``document_groups`` and rows
  to ``group_placements``. No file is touched, and no path in this module names
  one.
* **The reader's decision wins.** A document the reader dragged somewhere is left
  where they put it, whatever this pass concludes. An arrangement a person has
  corrected is not a guess to be re-guessed.
* **A group is named, not numbered.** ``Planning 2025 en 2026`` tells the reader
  something; ``Cluster 3`` does not, and a name is also the only handle there is
  to recognise the same group on a later pass.
* **A cluster the reader emptied stays.** It is left standing rather than
  deleted, because a proposal that quietly disappeared would be
  indistinguishable from one that was never made.
"""
from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.llm.base import LLMError, LLMProvider
from app.models import ARCHIVE_CATEGORY, Group, GroupPlacement
from app.services.delphi import SignalDraft
from app.services.placement import PlacementError, create_group, place_document

#: A group of one is not a grouping. Two is the smallest claim worth making, and
#: the threshold keeps a model from returning every document as its own group.
_MIN_MEMBERS = 2

#: Group names are bounded by the column, and a name is a label rather than a
#: document, so the useful range sits far below the limit.
_MAX_NAME = 200
_MAX_WHY = 1000

#: Refused outright, whatever the surrounding text says. A name is shown in a card
#: and compared for equality, so a name that is not plain text is not a name.
_NAME_SUSPECTS = ("/", "\\", "\n", "\r", "\t")


@dataclass(frozen=True)
class GroupDraft:
    """One proposed cluster, before it is stored."""

    name: str
    paths: list[str]
    #: The evidence, in one or two sentences, pointing at the findings it came
    #: from. Required, for the same reason a signal needs one.
    why: str


@dataclass
class GroupProposal:
    """What a proposal actually did, as opposed to what it asked for."""

    group_id: int
    name: str
    placed: list[str] = field(default_factory=list)
    #: Documents in the proposal that were left where the reader put them.
    left_alone: list[str] = field(default_factory=list)
    #: Documents in the proposal that are no longer readable at that path.
    unavailable: list[str] = field(default_factory=list)

    @property
    def changed(self) -> bool:
        return bool(self.placed)


# ---------------------------------------------------------------------------
# Validating what the model proposed
# ---------------------------------------------------------------------------


def _clean_name(raw: object) -> str:
    """A group name the reader could have typed themselves."""
    name = " ".join(str(raw or "").split())
    if not name or len(name) > _MAX_NAME:
        return ""
    if any(suspect in name for suspect in _NAME_SUSPECTS):
        return ""
    return name


def _clean_paths(raw: object, known_paths: set[str]) -> list[str]:
    """The members that are real documents, without duplicates, in one order."""
    if not isinstance(raw, list):
        return []
    kept: list[str] = []
    for entry in raw:
        if isinstance(entry, str) and entry in known_paths and entry not in kept:
            kept.append(entry)
    return kept


def parse_group_drafts(raw: object, known_paths: set[str]) -> list[GroupDraft]:
    """Validate what the model proposed as groups.

    Strict in the same places the signals are: a cluster naming a document that
    is not there is not a slightly-wrong cluster, it is a card the reader cannot
    open. A cluster that survives has a name, at least two real members, and a
    reason.
    """
    if not isinstance(raw, list):
        return []

    drafts: list[GroupDraft] = []
    seen: set[tuple[str, tuple[str, ...]]] = set()
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        name = _clean_name(entry.get("name"))
        if not name:
            continue
        paths = _clean_paths(entry.get("paths") or entry.get("documents"), known_paths)
        if len(paths) < _MIN_MEMBERS:
            # One document is not a group, and a cluster whose members are all
            # fictional is worse than no cluster at all.
            continue
        why = str(entry.get("why") or entry.get("reason") or "").strip()[:_MAX_WHY]
        if not why:
            continue
        fingerprint = (name, tuple(sorted(paths)))
        if fingerprint in seen:
            continue
        seen.add(fingerprint)
        drafts.append(GroupDraft(name=name, paths=paths, why=why))
    return drafts


# ---------------------------------------------------------------------------
# Asking the model
# ---------------------------------------------------------------------------


def _cluster_system_prompt() -> str:
    return (
        "You group the documents of a collection that a reader would want to look "
        "at together. You are given the findings from a reading of that "
        "collection -- what one document seems to be relative to another -- and "
        "the full list of paths. You do not read the documents themselves, so you "
        "may only group what the findings support.\n"
        "Rules:\n"
        "- A group needs at least two documents, and every member must be a path "
        "from the list, spelled exactly as written there.\n"
        "- A group needs a name the reader would recognise from the documents "
        "themselves: a subject, a project, a period. Not Group 1, not a "
        "restatement of the members, and not a category so general it could hold "
        "anything.\n"
        "- A group needs a why of one or two sentences pointing at the findings it "
        "came from. A group nobody can check is worse than no group.\n"
        "- Do not force a document into a group. A document the findings do not "
        "connect to anything belongs in no group, and that is a normal answer.\n"
        'Answer with JSON only, shaped as {"groups": [{"name": "...", '
        '"paths": ["..."], "why": "..."}]}. No markdown fences, no commentary.'
    )


def _cluster_user_prompt(findings: list[SignalDraft], known_paths: list[str]) -> str:
    return "\n".join(
        [
            "Findings from reading the collection:",
            "",
            json.dumps(
                [
                    {
                        "path": f.path,
                        "kind": f.kind,
                        "reference": f.reference,
                        "why": f.why,
                    }
                    for f in findings
                ],
                ensure_ascii=False,
            ),
            "",
            "Every path in this collection (a member must be one of these, "
            "spelled exactly as written here):",
            json.dumps(known_paths, ensure_ascii=False),
        ]
    )


def _payload_of(response_content: str) -> dict:
    """The JSON object out of a response, tolerating fences and prose."""
    cleaned = (response_content or "").strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.strip("`")
        if cleaned.startswith("json"):
            cleaned = cleaned[4:]
    start, end = cleaned.find("{"), cleaned.rfind("}")
    if start == -1 or end == -1:
        return {}
    try:
        data = json.loads(cleaned[start : end + 1])
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


def propose_clusters(
    provider: LLMProvider,
    findings: list[SignalDraft],
    known_paths: list[str],
) -> list[GroupDraft]:
    """Group the documents the findings connect.

    Returns an empty list rather than raising when the model is unreachable or
    answers with something unusable: the findings are already recorded and are
    worth the reader's attention on their own, and a clustering pass that fails
    must not cost them that.
    """
    if len(known_paths) < _MIN_MEMBERS:
        return []
    if not findings:
        # Nothing connected anything, so there is nothing to group on. Asking
        # anyway invites the model to invent a structure out of nothing.
        return []

    try:
        response = provider.chat(
            [
                {"role": "system", "content": _cluster_system_prompt()},
                {
                    "role": "user",
                    "content": _cluster_user_prompt(findings, known_paths),
                },
            ]
        )
    except LLMError:
        return []
    return parse_group_drafts(
        _payload_of(response.content).get("groups"), set(known_paths)
    )


# ---------------------------------------------------------------------------
# Writing the proposals
# ---------------------------------------------------------------------------


def _reader_placed(db: Session, workspace_id: int, repository_id: int) -> set[str]:
    """The documents the reader has placed with their own hand.

    Keyed by path within one repository, because a document is identified by the
    pair: two repositories in the same workspace can both hold ``notes.md``.
    """
    return set(
        db.scalars(
            select(GroupPlacement.file_path).where(
                GroupPlacement.workspace_id == workspace_id,
                GroupPlacement.repository_id == repository_id,
                GroupPlacement.placed_by == "user",
            )
        ).all()
    )


def _ensure_group(db: Session, workspace_id: int, draft: GroupDraft) -> Group:
    """The group with this name, created as Delphi's if it does not exist yet.

    Reusing by name is deliberate: the name is both what the model proposed and
    what the reader reads, so a second pass proposing the same subject lands in
    the same card instead of a second copy of it.

    A group the reader made keeps its ``source`` even when Delphi adds members:
    the reader built it, and joining a group they built is still their group.
    The description is not rewritten either -- the reader may have edited it, and
    a reason that stops being theirs because a second pass ran would be worse
    than a slightly stale one.
    """
    existing = db.scalar(
        select(Group).where(
            Group.workspace_id == workspace_id, Group.name == draft.name
        )
    )
    if existing is not None:
        return existing
    return create_group(
        db,
        workspace_id,
        draft.name,
        description=draft.why,
        source="ai",
        layout="grid",
    )


def propose_groups(
    db: Session,
    workspace_id: int,
    repository_id: int,
    drafts: Sequence[GroupDraft],
) -> list[GroupProposal]:
    """Write the proposed groups and place their members.

    Three things are decided here rather than left to the caller, because each
    one is a way to be wrong in a way the reader would not notice:

    * **A document the reader placed is left alone.** Their arrangement survives
      a re-analysis untouched, which is what makes correcting Delphi safe: you
      can disagree with a proposal and never be argued out of it.
    * **The archive is never a target.** A cluster named after the archive would
      otherwise file documents there, and filing is a decision a person makes.
    * **A member that is not there is reported, not skipped quietly.** A group
      that quietly lost a member reads as a group of that size.
    """
    reader_placed = _reader_placed(db, workspace_id, repository_id)
    archive = db.scalar(
        select(Group).where(
            Group.workspace_id == workspace_id, Group.is_archive.is_(True)
        )
    )
    archive_name = (archive.name if archive is not None else ARCHIVE_CATEGORY).casefold()

    proposals: list[GroupProposal] = []
    for draft in drafts:
        if draft.name.casefold() == archive_name:
            continue
        group = _ensure_group(db, workspace_id, draft)
        proposal = GroupProposal(group_id=group.id, name=group.name)
        for path in draft.paths:
            if path in reader_placed:
                proposal.left_alone.append(path)
                continue
            try:
                place_document(
                    db, workspace_id, group.id, repository_id, path, placed_by="ai"
                )
            except PlacementError:
                # The document is not readable at that path any more. Recorded
                # rather than hidden, because a group that lost a member
                # silently reads as a group of that size.
                proposal.unavailable.append(path)
                continue
            proposal.placed.append(path)
        proposals.append(proposal)
    return proposals


__all__ = [
    "GroupDraft",
    "GroupProposal",
    "parse_group_drafts",
    "propose_clusters",
    "propose_groups",
]
