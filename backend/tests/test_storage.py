"""Storing what the reader drops in: names, collisions, and what gets refused.

Two of the product's hard rules are decided in this service rather than in a
route, so both are asserted against the filesystem instead of against a return
value: *nothing is ever deleted*, and *nothing is written outside the directory
Apollo owns*. The service is also the only place in the application that creates
a document, which makes the naming rules here the rules about what can appear in
somebody's own folder.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from app.services import storage as storage_service
from app.services.storage import (
    INBOX_DIR,
    StorageError,
    list_inbox,
    sanitize_filename,
    storage_root,
    store_upload,
    workspace_inbox,
    workspace_storage,
)

WORKSPACE = 7


def _files(root: Path) -> set[str]:
    """Every file under ``root``, as POSIX paths relative to it."""
    if not root.exists():
        return set()
    return {p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_file()}


def _upload(name: str, text: str = "# Notities\n\nWat tekst.\n"):
    return store_upload(WORKSPACE, name, text.encode("utf-8"))


# ---------------------------------------------------------------------------
# Names
# ---------------------------------------------------------------------------


def test_a_plain_name_is_left_alone() -> None:
    assert sanitize_filename("verslag.pdf") == "verslag.pdf"


def test_a_path_is_reduced_to_its_last_component() -> None:
    """The traversal is discarded, not cleaned up.

    ``safe_path`` refuses a traversal; here the name never becomes a path at all,
    because the directory is chosen by the application.
    """
    assert sanitize_filename("../../etc/passwd.md") == "passwd.md"
    assert sanitize_filename("c:\\Users\\bojan\\verslag.md") == "verslag.md"


def test_characters_that_cannot_be_in_a_name_are_replaced() -> None:
    assert sanitize_filename('ra<pp>ort:"x".md') == "ra-pp-ort--x-.md"


def test_a_trailing_dot_or_space_is_removed() -> None:
    # Legal on Linux, impossible on Windows, and the storage root may be either.
    assert sanitize_filename("verslag.md ") == "verslag.md"
    assert sanitize_filename("verslag.md.") == "verslag.md"


def test_a_reserved_device_name_is_made_usable() -> None:
    """``CON.md`` cannot be created on Windows whatever the extension."""
    assert sanitize_filename("CON.md") == "CON-file.md"
    assert sanitize_filename("com1.txt") == "com1-file.txt"


def test_accents_survive() -> None:
    """A Dutch filename must not be mangled into punctuation."""
    assert sanitize_filename("café-aanvraag.md") == "café-aanvraag.md"


@pytest.mark.parametrize("name", ["", "   ", "../../", "..", ".md", "..."])
def test_a_name_with_nothing_left_in_it_is_refused(name: str) -> None:
    with pytest.raises(StorageError):
        sanitize_filename(name)


# ---------------------------------------------------------------------------
# Storing
# ---------------------------------------------------------------------------


def test_a_document_lands_in_the_inbox_with_its_bytes() -> None:
    stored = _upload("verslag.md")

    assert stored.path == f"{INBOX_DIR}/verslag.md"
    assert stored.readable is True
    assert stored.unreadable_reason is None
    assert (workspace_inbox(WORKSPACE) / "verslag.md").read_bytes() == (
        "# Notities\n\nWat tekst.\n".encode("utf-8")
    )


def test_the_second_copy_of_a_name_is_numbered_and_the_first_survives() -> None:
    first = _upload("verslag.md", "# Eerste\n")
    second = _upload("verslag.md", "# Tweede\n")

    assert first.name == "verslag.md"
    assert second.name == "verslag-2.md"
    inbox = workspace_inbox(WORKSPACE)
    # The reason numbering exists at all: the alternative to a second copy of a
    # name is a lost first document.
    assert (inbox / "verslag.md").read_text(encoding="utf-8") == "# Eerste\n"
    assert (inbox / "verslag-2.md").read_text(encoding="utf-8") == "# Tweede\n"


def test_a_refused_upload_creates_nothing_at_all() -> None:
    """A refusal must not leave a folder behind, let alone a file."""
    with pytest.raises(StorageError):
        _upload("script.py", "print('hi')\n")

    assert not storage_root().exists()


def test_a_non_document_is_refused_with_what_is_accepted() -> None:
    with pytest.raises(StorageError) as exc:
        _upload("archief.zip", "PK\x03\x04")

    # The message names the accepted formats: a refusal the reader cannot act on
    # is only a wall.
    assert ".md" in str(exc.value)
    assert ".pdf" in str(exc.value)


def test_an_empty_file_is_refused() -> None:
    with pytest.raises(StorageError, match="empty"):
        store_upload(WORKSPACE, "leeg.md", b"")


def test_a_file_above_the_ceiling_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(storage_service, "MAX_UPLOAD_BYTES", 8)

    with pytest.raises(StorageError, match="larger than"):
        store_upload(WORKSPACE, "groot.md", b"x" * 9)

    assert not storage_root().exists()


def test_an_unreadable_document_is_stored_and_reported() -> None:
    """Stored, not deleted: the bytes arrived, and a refusal after the fact would
    leave the reader with neither the file nor a reason for its absence."""
    stored = store_upload(WORKSPACE, "kapot.docx", b"not a zip file at all")

    assert stored.readable is False
    assert stored.unreadable_reason
    assert (workspace_inbox(WORKSPACE) / "kapot.docx").read_bytes() == (
        b"not a zip file at all"
    )


def test_a_traversal_in_the_name_cannot_write_outside_the_inbox() -> None:
    stored = _upload("../../../ontsnapt.md")

    assert stored.path == f"{INBOX_DIR}/ontsnapt.md"
    # Nothing anywhere outside the inbox, and in particular nothing above the
    # workspace directory.
    assert _files(workspace_storage(WORKSPACE)) == {f"{INBOX_DIR}/ontsnapt.md"}


def test_the_inbox_is_the_only_folder_created() -> None:
    """No empty category folders: those are a claim the reader did not make."""
    _upload("verslag.md")

    assert {p.name for p in workspace_storage(WORKSPACE).iterdir()} == {INBOX_DIR}


# ---------------------------------------------------------------------------
# Listing
# ---------------------------------------------------------------------------


def test_a_missing_inbox_is_empty_rather_than_an_error() -> None:
    assert list_inbox(WORKSPACE) == []


def test_only_documents_are_listed() -> None:
    _upload("verslag.md")
    inbox = workspace_inbox(WORKSPACE)
    # A stray file that arrived some other way, and a write that never finished.
    (inbox / "aantekeningen.py").write_text("x = 1\n", encoding="utf-8")
    (inbox / "half.md.tmp-apollo").write_text("half", encoding="utf-8")

    assert [e.path for e in list_inbox(WORKSPACE)] == [f"{INBOX_DIR}/verslag.md"]


def test_entries_are_listed_by_name() -> None:
    _upload("zebra.md")
    _upload("appel.md")

    assert [e.name for e in list_inbox(WORKSPACE)] == ["appel.md", "zebra.md"]

