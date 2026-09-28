"""Delphi's information signals: what is old, duplicated, new, or in conflict.

A connection says two documents are related. A signal says something about a
document's *standing* -- that it has been superseded, that it covers ground
another document already covers, that it says something the rest of the corpus
does not. The distinction matters because only one of them is a reason to move a
document out of the way.

Everything here is a suggestion. A signal is stored, shown, and then either acted
on or dismissed by a person. Nothing in this module writes a file, and the
"archive" it can propose is a move into a group, never a deletion.

The validation is strict on the way *out* for the same reason the archive is
strict on the way in: an unrecognised signal name or a reference to a document
that does not exist would put a claim in the sidebar that the reader cannot
check, which is the one thing a signal must never be.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import PurePosixPath

from app.models import PULSE_SIGNAL_REFS, PULSE_SIGNALS

#: The signals, described for the prompt and for the UI. Ordered by how much a
#: reader is expected to act on them: "this is out of date" invites a decision,
#: "this is new" mostly informs.
SIGNAL_LABELS: dict[str, str] = {
    "outdated": "Older information",
    "duplicate": "Possible duplicate",
    "new": "New information",
    "update": "Possible update",
    "conflict": "Conflict",
}

#: One plain sentence per signal, for the prompt. Kept in code rather than
#: inline in the prompt string so the wording the model is given and the wording
#: the user reads cannot drift apart.
SIGNAL_DESCRIPTIONS = """
- outdated:  the document has been superseded by a newer one. Say which document
  supersedes it, and why you believe so (a later date, a higher version, a
  document that explicitly replaces it). Being merely old is not enough.
- duplicate: two documents cover substantially the same ground. Name the one
  that covers it better, if one does, and say what makes them overlap.
- new:       the document says something the rest of the corpus does not. No
  reference is needed; this is a statement about the corpus, not about a file.
- update:    the document is relevant to a topic another document or group
  already covers, and appears to extend or correct it. Name the document.
- conflict:  two documents give incompatible information about the same subject.
  Name the other document and say specifically what disagrees. A difference in
  emphasis is not a conflict; a difference in what is claimed to be true is.
"""


@dataclass(frozen=True)
class Signal:
    """One validated signal about one document."""

    kind: str
    #: The document it points at, repository-relative. None for "new", and None
    #: when Delphi did not name one.
    reference: str | None = None

    @property
    def label(self) -> str:
        return SIGNAL_LABELS.get(self.kind, self.kind)


@dataclass
class SignalSet:
    """The signals raised for one document, plus what was dropped and why.

    ``dropped`` is kept rather than silently discarded: a suggestion that
    references a document which is not there is worth the reviewer knowing about,
    because it is the model's claim that the corpus is inconsistent.
    """

    signals: list[Signal] = field(default_factory=list)
    dropped: list[str] = field(default_factory=list)

    def kinds(self) -> list[str]:
        return [s.kind for s in self.signals]

    def refs(self) -> dict[str, str]:
        return {s.kind: s.reference for s in self.signals if s.reference}

    def has(self, kind: str) -> bool:
        return any(s.kind == kind for s in self.signals)

    @property
    def is_empty(self) -> bool:
        return not self.signals


def _is_document_name(target: str) -> bool:
    """Whether a reference looks like a document rather than prose.

    A model that writes "the newer planning document" instead of a path is not
    wrong about the finding, only about how to express it, so the text is kept
    out of the reference column and the signal survives without a link.
    """
    if not target or len(target) > 1000:
        return False
    if "\\" in target or target.startswith("/") or ".." in PurePosixPath(target).parts:
        return False
    return "." in PurePosixPath(target).name


def parse_signals(
    raw: object, known_paths: set[str] | None = None
) -> SignalSet:
    """Validate and normalise whatever the model returned for one document.

    Tolerant by design, because this is model output on a job the user is
    waiting for. An unrecognised signal name, a malformed reference, or a
    reference to a path that does not exist costs that one signal, not the scan.
    The alternative -- failing the item -- would throw away a document's correct
    findings because one of five was malformed.

    ``known_paths`` is the set of repository-relative paths that actually exist.
    Without it a reference is checked for shape only, which is what the tests use
    and what a caller without a file listing should get.
    """
    result = SignalSet()
    if not isinstance(raw, list):
        return result

    seen: set[str] = set()
    for entry in raw:
        if isinstance(entry, str):
            kind, reference = entry.strip(), ""
        elif isinstance(entry, dict):
            kind = str(entry.get("kind") or entry.get("signal") or "").strip()
            reference = str(entry.get("reference") or entry.get("path") or "").strip()
        else:
            result.dropped.append(f"ignored a signal that was not a name or an object: {entry!r}")
            continue

        if kind not in PULSE_SIGNALS:
            result.dropped.append(f"{kind!r} is not a signal Apollo knows")
            continue
        if kind in seen:
            # The same finding twice is one finding.
            continue
        seen.add(kind)

        # Slashes only. A leading "./" is dropped because that is the same
        # document written another way; a leading "../" is NOT stripped, because
        # hiding a traversal is not the same as refusing one, and the reference
        # ends up in a link the reader can click.
        reference = reference.replace("\\", "/")
        if reference.startswith("./"):
            reference = reference[2:]
        if not reference:
            # The signal survives without a target. "This looks out of date" is
            # worth reading even when Delphi failed to say out of date relative
            # to what; dropping the finding over a missing field would lose the
            # more useful half of it.
            result.signals.append(Signal(kind=kind, reference=None))
            if kind in PULSE_SIGNAL_REFS:
                # Recorded so the reviewer can see the finding is incomplete
                # rather than assume Delphi considered it and found nothing.
                result.dropped.append(
                    f"{kind!r} was raised without saying which document it refers to"
                )
            continue

        if not _is_document_name(reference):
            result.dropped.append(f"{kind!r} pointed at {reference!r}, which is not a document path")
            result.signals.append(Signal(kind=kind, reference=None))
            continue

        if known_paths is not None and reference not in known_paths:
            result.dropped.append(
                f"{kind!r} pointed at {reference!r}, which is not in this repository"
            )
            result.signals.append(Signal(kind=kind, reference=None))
            continue

        result.signals.append(Signal(kind=kind, reference=reference))

    # Ordered by the canonical order rather than by the model's, so two runs over
    # the same corpus produce the same sidebar.
    result.signals.sort(key=lambda s: PULSE_SIGNALS.index(s.kind))
    return result


def signals_prompt_instruction() -> str:
    """The block appended to Delphi's system prompt."""
    return (
        "In addition to tags and connections, report SIGNALS about each "
        "document's standing. A signal is a claim that something about the "
        "document has changed relative to its peers, and each one is a "
        "suggestion a person will review -- never state it as settled fact.\n"
        f"{SIGNAL_DESCRIPTIONS}\n"
        'Use "reference" to name the other document, as a path relative to the '
        "repository root. Only use these signal names; if nothing applies, "
        "report an empty list. Do not use a signal to say a document is "
        "unrelated -- that is what an empty list means.\n"
    )


__all__ = [
    "SIGNAL_LABELS",
    "Signal",
    "SignalSet",
    "parse_signals",
    "signals_prompt_instruction",
]
