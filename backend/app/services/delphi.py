"""The analysis: what Delphi makes of a collection, and where those findings live.

One button, one pass, one outcome. The reader drops documents in, presses
*Analyseren*, and Apollo says -- in ordinary sentences -- which document looks
older than another, which two say incompatible things, and which one says
something nothing else does. Nothing is moved, rewritten or deleted: the
analysis only ever produces suggestions, and every one of them can be ignored.

This module holds the motor and the storage. The *vocabulary* lives in
``services/signals.py``, so the wording the model is given and the wording the
reader reads cannot drift apart into two languages for one idea.

Three rules, in order of importance:

* **A signal is never acted on.** There is no file operation anywhere in this
  module. What there is instead is a reading: a finding with no ``why`` is
  dropped at the boundary, because a claim the reader cannot check is worse than
  no claim at all.
* **A dismissed signal stays dismissed.** Re-analysing refreshes the evidence,
  never the reader's decision. Otherwise "this is a false alarm" would quietly
  expire and the finding would come back on its own.
* **The corpus is the unit.** A signal is about a document *relative to its
  peers*, so documents are analysed in batches with the full path list
  alongside. Asking about one document in isolation would produce a different and
  much weaker answer -- everything would look new and nothing would look
  superseded.
"""
from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass, field

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.llm import BackgroundSettingsView
from app.llm.base import LLMError, LLMProvider
from app.llm.context import MESSAGE_OVERHEAD_TOKENS, ContextBudget
from app.models import PULSE_SIGNALS, DocSignal, GroupPlacement
from app.services.documents import (
    DocumentError,
    list_documents,
    read_document,
)
from app.services.markdown_structure import build_representation
from app.services.paths import PathSecurityError
from app.services.signals import parse_signals, signals_prompt_instruction

#: How many documents go into one request. Batching keeps the pass working on any
#: corpus size and lets the context budget cap each request; eight is the same
#: bound the pulse scan uses, so both halves of Delphi speak the same size.
_MAX_DOCS_PER_REQUEST = 8

#: Framing charged per document when sizing a batch: its header and the
#: separators around it.
DOCUMENT_FRAMING_TOKENS = 12

#: A floor for the per-document share, so a small context window still sends
#: something rather than refusing to analyse at all.
_MIN_DOCUMENT_TOKENS = 200

#: A repository with fewer documents than this cannot produce a "relative to its
#: peers" judgement, and asking for one invites the model to invent it.
_MIN_CORPUS = 2


class DelphiError(RuntimeError):
    """A pass could not be completed. Always a message the reader can act on."""


@dataclass(frozen=True)
class SignalDraft:
    """One validated finding, before it is stored."""

    path: str
    kind: str
    reference: str | None
    why: str
    confidence: float | None = None


@dataclass
class DelphiResult:
    """What one pass produced.

    ``analysed`` counts the documents actually sent to the model, which is
    deliberately not the number of documents in the repository: a file that could
    not be read, or a batch that did not fit the window, is a document the reader
    was not told about, and a summary claiming full coverage would be a lie.
    """

    documents: int = 0
    analysed: int = 0
    signals: list[SignalDraft] = field(default_factory=list)
    #: The documents that were actually read, in collection order. Handed to the
    #: clustering pass, which may only group these: a cluster is a claim about
    #: documents somebody read, not about paths it was told exist.
    paths: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    @property
    def summary(self) -> str:
        """One plain sentence about what happened, in the reader's terms."""
        if self.analysed == 0:
            return "Nothing was analysed. " + ("; ".join(self.errors) or "")
        found = len(self.signals)
        what = (
            "Nothing stood out."
            if found == 0
            else f"{found} thing{'s' if found != 1 else ''} worth your attention."
        )
        return (
            f"Read {self.analysed} of {self.documents} document"
            f"{'' if self.documents == 1 else 's'}. {what} "
            "No document was moved, renamed, or rewritten."
        )


def _document_budget(budget: ContextBudget) -> int:
    """Tokens each document may occupy in one request.

    What is left after the reply is reserved, divided between the documents of a
    batch. A document allowed to fill the window on its own is a document nothing
    can be compared with, and comparison is the entire point of a signal.
    """
    available = budget.input_budget - MESSAGE_OVERHEAD_TOKENS * (
        _MAX_DOCS_PER_REQUEST + 1
    )
    return max(_MIN_DOCUMENT_TOKENS, available // _MAX_DOCS_PER_REQUEST)


def _prepare(root: str, paths: list[str], per_document: int) -> list[dict[str, str]]:
    """Read each document and reduce it to a structural representation.

    Whole-document coverage matters more here than verbatim text: a signal is a
    judgement about one document relative to another, and the dates, versions and
    headings that decide "this supersedes that" are spread across the file rather
    than sitting in its opening. A document too large for its share is sent as a
    digest, labelled as one so the model can weigh the finding accordingly.
    """
    prepared: list[dict[str, str]] = []
    for path in paths:
        try:
            content = read_document(root, path)
        except (DocumentError, OSError, PathSecurityError):
            # An unreadable document is skipped rather than failing the pass: the
            # reader dropped a mixture of formats, and one bad file should not
            # cost them the other nine.
            continue
        if not content.strip():
            continue
        body, mode = build_representation(content, per_document)
        text = (
            f"(STRUCTURAL DIGEST of the whole document)\n{body}"
            if mode == "digest"
            else body
        )
        prepared.append({"path": path, "text": text})
    return prepared


def _system_prompt() -> str:
    return (
        "You read a documentation collection and report what is worth the "
        "reader's attention. You are given a few documents at a time plus the "
        "full list of paths in the collection, because every judgement you make "
        "is about a document relative to the others.\n"
        "For each document you receive, report zero or more signals about its "
        "standing, and nothing else: no summary, no tags, no rewriting, no "
        "recommendation about what to do. A person decides what happens next.\n"
        f"{signals_prompt_instruction()}"
        'Answer with JSON only, shaped as {"documents": [{"path": "...", '
        '"signals": [{"kind": "...", "reference": "..." , "why": "..."}], '
        '"confidence": 0.0}]}. No markdown fences, no commentary.'
    )


def _user_prompt(docs: list[dict[str, str]], all_paths: list[str]) -> str:
    return "\n".join(
        [
            "Documents to read:",
            "",
            json.dumps(docs, ensure_ascii=False),
            "",
            "Every path in this collection (a reference must be one of these, "
            "spelled exactly as written here):",
            json.dumps(all_paths, ensure_ascii=False),
            "",
            "A document with nothing worth reporting is a normal answer: include "
            "it with an empty signal list.",
        ]
    )


def _parse_response(text: str, known_paths: set[str]) -> list[SignalDraft]:
    """Pull the findings out of one response.

    A document the model invented is ignored rather than stored, and a document
    whose signals are all malformed loses only those signals: the rest of the
    response is still true.
    """
    cleaned = (text or "").strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.strip("`")
        if cleaned.startswith("json"):
            cleaned = cleaned[4:]
    start, end = cleaned.find("{"), cleaned.rfind("}")
    if start == -1 or end == -1:
        raise DelphiError("The model response contained no JSON object.")
    try:
        data = json.loads(cleaned[start : end + 1])
    except ValueError as exc:
        raise DelphiError("The model response was not valid JSON.") from exc

    drafts: list[SignalDraft] = []
    for entry in data.get("documents", []) or []:
        if not isinstance(entry, dict):
            continue
        path = entry.get("path")
        if not isinstance(path, str) or path not in known_paths:
            # Hallucinated paths are dropped: a signal about a document that is
            # not there cannot be shown in a panel the reader could act on.
            continue
        try:
            confidence: float | None = max(
                0.0, min(1.0, float(entry.get("confidence")))
            )
        except (TypeError, ValueError):
            confidence = None
        for signal in parse_signals(entry.get("signals"), known_paths).signals:
            drafts.append(
                SignalDraft(
                    path=path,
                    kind=signal.kind,
                    reference=signal.reference,
                    why=signal.why,
                    confidence=confidence,
                )
            )
    return drafts


def analyse(provider: LLMProvider, root: str) -> DelphiResult:
    """Read the collection and report what stands out.

    Nothing is written to disk and nothing is decided. A pass over a collection
    that cannot answer the question -- one document, or a folder that cannot be
    listed -- reports the reason in ``errors`` rather than inventing a
    comparison, because the reader pressed a button and deserves an answer rather
    than an exception.
    """
    result = DelphiResult()
    try:
        paths = list_documents(root, ".")
    except (DocumentError, OSError, PathSecurityError) as exc:
        result.errors.append(f"Could not read the documents: {exc}")
        return result

    result.documents = len(paths)
    if len(paths) < _MIN_CORPUS:
        result.errors.append(
            f"There is {len(paths)} document here. A signal is a judgement about "
            "a document relative to its peers, so add a few more and press "
            "Analyseren again."
        )
        return result

    budget = ContextBudget.from_settings(BackgroundSettingsView())
    prepared = _prepare(root, paths, _document_budget(budget))
    if not prepared:
        result.errors.append("No document in this collection could be read.")
        return result

    result.paths = [doc["path"] for doc in prepared]

    system = _system_prompt()
    known = {doc["path"] for doc in prepared}
    for start in range(0, len(prepared), _MAX_DOCS_PER_REQUEST):
        batch = prepared[start : start + _MAX_DOCS_PER_REQUEST]
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": _user_prompt(batch, paths)},
        ]
        # Each document was already fitted to its share of the window, so this is
        # a backstop rather than the mechanism. Without it an unexpectedly large
        # request would fail inside the provider with a worse message.
        if not budget.fits(messages):
            result.errors.append(
                "A batch of documents did not fit the context window. Raise "
                "LLM_CONTEXT_TOKENS to analyse more of them at once."
            )
            continue
        try:
            response = provider.chat(messages)
        except LLMError as exc:
            result.errors.append(str(exc))
            continue
        result.analysed += len(batch)
        try:
            result.signals.extend(_parse_response(response.content, known))
        except DelphiError as exc:
            result.errors.append(str(exc))
    return result


# ---------------------------------------------------------------------------
# Keeping the findings
# ---------------------------------------------------------------------------


def _order_key(signal: DocSignal) -> tuple[int, int]:
    """Findings in the order a reader acts on them, not in the order stored.

    "This is out of date" invites a decision; "this is new" mostly informs. One
    fixed order for every document, so the panel does not reshuffle between
    reads of the same corpus.
    """
    position = (
        PULSE_SIGNALS.index(signal.kind)
        if signal.kind in PULSE_SIGNALS
        else len(PULSE_SIGNALS)
    )
    return (position, signal.id)


def record_signals(
    db: Session,
    workspace_id: int,
    repository_id: int,
    drafts: Sequence[SignalDraft],
) -> list[DocSignal]:
    """Store the findings, one row per finding, without stacking duplicates.

    A finding is (document, kind, reference). A pass that finds the same thing
    again refreshes the evidence on the existing row instead of adding a second
    copy -- and it never touches ``status``, so a finding the reader dismissed
    stays dismissed however often Delphi arrives at it.

    Findings that are no longer raised are left in place rather than removed. A
    suggestion silently disappearing between two passes would be indistinguishable
    from one that was never made, and the reader has no way to ask which happened.
    """
    stored: list[DocSignal] = []
    for draft in drafts:
        existing = db.scalar(
            select(DocSignal).where(
                DocSignal.workspace_id == workspace_id,
                DocSignal.repository_id == repository_id,
                DocSignal.file_path == draft.path,
                DocSignal.kind == draft.kind,
                # Comparing a column to None is written as IS NULL, which is what
                # a signal that names no other document needs.
                DocSignal.reference == draft.reference,
            )
        )
        if existing is not None:
            existing.why = draft.why
            existing.confidence = draft.confidence
            stored.append(existing)
            continue
        row = DocSignal(
            workspace_id=workspace_id,
            repository_id=repository_id,
            file_path=draft.path,
            kind=draft.kind,
            reference=draft.reference,
            why=draft.why,
            confidence=draft.confidence,
        )
        db.add(row)
        stored.append(row)
    db.commit()
    return stored


def signals_for_document(
    db: Session,
    workspace_id: int,
    repository_id: int,
    path: str,
    *,
    include_dismissed: bool = False,
) -> list[DocSignal]:
    """The findings about one document, most actionable first."""
    query = select(DocSignal).where(
        DocSignal.workspace_id == workspace_id,
        DocSignal.repository_id == repository_id,
        DocSignal.file_path == path,
    )
    if not include_dismissed:
        query = query.where(DocSignal.status != "dismissed")
    return sorted(db.scalars(query).all(), key=_order_key)


def signals_for_group(
    db: Session,
    workspace_id: int,
    group_id: int,
    *,
    include_dismissed: bool = False,
) -> list[DocSignal]:
    """The findings about every document in one group.

    Joined against the placements rather than looked up per member, because the
    panel behind a group asks for all of them at once. The distinct is belt and
    braces: one document can only sit in a group once.
    """
    query = (
        select(DocSignal)
        .join(
            GroupPlacement,
            (GroupPlacement.repository_id == DocSignal.repository_id)
            & (GroupPlacement.file_path == DocSignal.file_path)
            & (GroupPlacement.workspace_id == DocSignal.workspace_id),
        )
        .where(
            GroupPlacement.group_id == group_id,
            GroupPlacement.workspace_id == workspace_id,
            DocSignal.workspace_id == workspace_id,
        )
    )
    if not include_dismissed:
        query = query.where(DocSignal.status != "dismissed")
    return sorted(db.scalars(query.distinct()).all(), key=_order_key)


def open_signal_count(db: Session, workspace_id: int) -> int:
    """How many findings are still waiting for a decision."""
    return int(
        db.scalar(
            select(func.count())
            .select_from(DocSignal)
            .where(
                DocSignal.workspace_id == workspace_id,
                DocSignal.status != "dismissed",
            )
        )
        or 0
    )


def dismiss_signal(db: Session, workspace_id: int, signal_id: int) -> DocSignal:
    """Hide one finding. The document is not touched and the decision sticks.

    Dismissal is not deletion and not a suggestion for the next pass to undo:
    the reader said "not this one", so a later analysis refreshes the evidence
    and leaves this alone.
    """
    signal = db.scalar(
        select(DocSignal).where(
            DocSignal.id == signal_id,
            DocSignal.workspace_id == workspace_id,
        )
    )
    if signal is None:
        raise DelphiError("That finding is not in this workspace.")
    if signal.status == "dismissed":
        return signal
    signal.status = "dismissed"
    db.commit()
    db.refresh(signal)
    return signal


__all__ = [
    "DelphiError",
    "DelphiResult",
    "SignalDraft",
    "analyse",
    "dismiss_signal",
    "open_signal_count",
    "record_signals",
    "signals_for_document",
    "signals_for_group",
]

