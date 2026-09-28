"""Delphi's information signals: parsed, validated, and never overstated.

A signal is a claim about a document's standing that a person will act on or
dismiss. Three properties matter more than anything else here:

* **It survives being wrong about one field.** Delphi is a model reading a
  corpus; one malformed signal must not cost a document its other findings.
* **It cannot claim something the reader cannot check.** An unrecognised name, a
  reference to a document that is not in the repository, or a bare claim with no
  evidence behind it would each put an unfalsifiable statement in front of the
  reader -- which is the opposite of what a signal is for.
* **It is a suggestion.** The wording the model is given has to keep it
  reviewable, or the reader stops checking it.
"""
from __future__ import annotations

from app.models import PULSE_SIGNALS
from app.services.signals import (
    SIGNAL_LABELS,
    Signal,
    parse_signals,
    signals_prompt_instruction,
)

#: The evidence every signal in these tests carries. Any sentence will do: the
#: rule under test is that one is present, not what it says.
EVIDENCE = "The newer document gives a different start date for the migration."


def signal(kind: str, reference: str | None = None, **extra) -> dict:
    """One signal as the model is asked to return it: a name, a target, evidence."""
    entry: dict = {"kind": kind, "why": EVIDENCE}
    if reference is not None:
        entry["reference"] = reference
    entry.update(extra)
    return entry


def test_every_signal_has_a_label_to_show() -> None:
    """An unlabelled signal would render as a raw word to the reader."""
    missing = [s for s in PULSE_SIGNALS if s not in SIGNAL_LABELS]
    assert missing == []


def test_the_prompt_only_asks_for_signals_that_exist() -> None:
    """The model is only ever asked for names the app can store and show."""
    instruction = signals_prompt_instruction().lower()
    for kind in PULSE_SIGNALS:
        assert kind in instruction


def test_the_prompt_asks_for_the_evidence_behind_every_claim() -> None:
    """Without this the model answers with bare verdicts, and a bare verdict asks
    the reader to check a claim without saying what to check."""
    instruction = signals_prompt_instruction().lower()

    assert "why" in instruction
    assert "non-empty why" in instruction


class TestParsing:
    def test_a_signal_with_its_evidence_is_kept(self) -> None:
        result = parse_signals([signal("outdated", "notes.md"), signal("new")])

        assert result.kinds() == ["outdated", "new"]
        assert result.is_empty is False

    def test_a_reference_is_kept(self) -> None:
        result = parse_signals([signal("outdated", "architecture/notes.md")])

        assert result.signals == [
            Signal(
                kind="outdated",
                reference="architecture/notes.md",
                why=EVIDENCE,
            )
        ]
        assert result.refs() == {"outdated": "architecture/notes.md"}

    def test_path_is_accepted_as_well_as_reference(self) -> None:
        # Models are inconsistent about which key they use, and this is a
        # formatting difference, not a disagreement.
        result = parse_signals([{"kind": "conflict", "path": "a.md", "why": EVIDENCE}])

        assert result.refs() == {"conflict": "a.md"}

    def test_new_needs_no_reference(self) -> None:
        """It is a statement about the corpus, not about another document."""
        result = parse_signals([signal("new")])

        assert result.has("new")
        assert result.dropped == []

    def test_the_reason_is_read_under_any_of_its_names(self) -> None:
        """`reason` and `because` are the same field with other words on it."""
        for key in ("why", "reason", "because"):
            result = parse_signals([{"kind": "conflict", key: EVIDENCE}])
            assert result.get("conflict").why == EVIDENCE

    def test_a_reason_is_capped_like_a_reference(self) -> None:
        """It is a sentence in a sidebar, not an essay."""
        result = parse_signals([{"kind": "conflict", "why": "x" * 5000}])

        assert len(result.get("conflict").why) == 1000

    def test_one_signal_can_be_picked_out_by_name(self) -> None:
        """What the storage layer needs to upsert a single finding."""
        result = parse_signals([signal("duplicate", "a.md"), signal("outdated", "b.md")])

        assert result.get("duplicate").reference == "a.md"
        assert result.get("nope") is None


class TestEvidence:
    """The rule that makes a signal checkable rather than decorative."""

    def test_a_bare_name_without_a_reason_is_refused(self) -> None:
        result = parse_signals(["outdated", "new"])

        assert result.is_empty
        assert any("without saying why" in d for d in result.dropped)

    def test_a_named_signal_without_a_reason_is_refused(self) -> None:
        result = parse_signals([{"kind": "conflict", "reference": "a.md"}])

        assert result.is_empty
        assert any("without saying why" in d for d in result.dropped)

    def test_an_empty_reason_is_refused_too(self) -> None:
        """Whitespace is not a reason."""
        result = parse_signals([{"kind": "conflict", "why": "   "}])

        assert result.is_empty

    def test_a_missing_reason_costs_only_its_own_signal(self) -> None:
        result = parse_signals([{"kind": "conflict"}, signal("duplicate", "a.md")])

        assert result.kinds() == ["duplicate"]


class TestOrdering:
    def test_signals_come_back_in_a_stable_order(self) -> None:
        """Two runs over the same corpus must produce the same sidebar."""
        first = parse_signals([signal("new"), signal("outdated"), signal("duplicate")])
        second = parse_signals([signal("duplicate"), signal("outdated"), signal("new")])

        assert first.kinds() == second.kinds()
        assert first.kinds() == [k for k in PULSE_SIGNALS if k in first.kinds()]


class TestRobustness:
    def test_a_malformed_signal_costs_only_itself(self) -> None:
        """The whole point: one bad field must not lose a document's findings."""
        result = parse_signals(
            [signal("outdated"), signal("purple-monkey"), signal("new")]
        )

        assert result.kinds() == ["outdated", "new"]
        assert any("purple-monkey" in d for d in result.dropped)

    def test_an_unknown_name_is_recorded_not_hidden(self) -> None:
        result = parse_signals([signal("tasty")])

        assert result.is_empty
        assert any("tasty" in d for d in result.dropped)

    def test_a_signal_naming_no_document_survives_without_one(self) -> None:
        """"This looks out of date" is still worth reading without the target."""
        result = parse_signals([signal("outdated")])

        assert result.has("outdated")
        assert result.refs() == {}
        # But the incompleteness is visible, so it is not read as a considered
        # finding with nothing to point at.
        assert any("without saying which document" in d for d in result.dropped)

    def test_prose_instead_of_a_path_is_dropped_as_a_reference(self) -> None:
        """The model sometimes writes "the newer planning doc" instead of a path.

        That is a correct finding expressed wrongly, so the signal stays and the
        unusable reference goes.
        """
        result = parse_signals([signal("outdated", "the newer planning document")])

        assert result.has("outdated")
        assert result.refs() == {}

    def test_a_reference_outside_the_repository_is_refused(self) -> None:
        """A link to a document that is not there cannot be checked by anyone."""
        result = parse_signals(
            [signal("outdated", "missing.md")],
            known_paths={"notes.md", "README.md"},
        )

        assert result.has("outdated")
        assert result.refs() == {}
        assert any("not in this repository" in d for d in result.dropped)

    def test_a_known_reference_is_kept(self) -> None:
        result = parse_signals(
            [signal("duplicate", "notes.md")],
            known_paths={"notes.md", "README.md"},
        )

        assert result.refs() == {"duplicate": "notes.md"}

    def test_the_same_signal_twice_is_one_finding(self) -> None:
        result = parse_signals([signal("conflict", "a.md"), signal("conflict", "a.md")])

        assert result.kinds() == ["conflict"]

    def test_a_reference_that_escapes_the_repository_is_refused(self) -> None:
        """A traversal must not become a clickable link out of the corpus."""
        result = parse_signals(
            [signal("outdated", "../../etc/passwd")],
            known_paths={"notes.md"},
        )

        assert result.refs() == {}

    def test_nonsense_input_is_not_an_error(self) -> None:
        """The scan should not fail because one document's field was odd."""
        for value in (None, 42, "outdated", {"nope": 1}, [object()]):
            result = parse_signals(value)
            assert isinstance(result.kinds(), list)

    def test_a_leading_dot_slash_is_the_same_document(self) -> None:
        result = parse_signals([signal("update", "./notes.md")])

        assert result.refs() == {"update": "notes.md"}

    def test_backslashes_are_normalised(self) -> None:
        result = parse_signals([signal("update", "a\\b.md")])

        assert result.refs() == {"update": "a/b.md"}


def test_signals_are_suggestions_and_never_state_themselves_as_fact() -> None:
    """The wording the model is given has to keep them reviewable.

    A signal that reads as a settled verdict is one the reader stops checking,
    and an unchecked "this is out of date" is worse than no signal at all.
    """
    instruction = signals_prompt_instruction()

    assert "suggestion" in instruction.lower()
    assert "never state it as settled fact" in instruction.lower()


def test_the_prompt_asks_for_an_empty_list_rather_than_an_absence_of_relations() -> None:
    """Otherwise the model invents a signal to fill the field."""
    instruction = signals_prompt_instruction().lower()

    assert "empty list" in instruction
    assert "unrelated" in instruction
