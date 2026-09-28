"""Adding a folder as a source: a copy, never a touch.

The reader points at a folder they already have. Everything below is about the
one property that makes that safe enough to offer: **the source folder is not
written to.** Not one byte, not one name, not one timestamp. It is asserted
against the filesystem, before and after, byte for byte -- because a feature that
reorganises somebody's own project while claiming not to would be the worst thing
this application could do.

The rest is what a copy has to get right: the structure is kept, nothing is
overwritten, and anything that did not arrive says why.
"""
from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.services import storage as storage_service
from app.services.storage import (
    INBOX_DIR,
    StorageError,
    import_folder,
    list_inbox,
    storage_root,
    workspace_inbox,
    workspace_storage,
)

WORKSPACE = 11


@pytest.fixture()
def source(tmp_path: Path) -> Path:
    """A folder that looks like something a reader already has."""
    root = tmp_path / "mijn-project"
    (root / "notities").mkdir(parents=True)
    (root / "verslag.md").write_text("# Verslag\n\nDe eerste.\n", encoding="utf-8")
    (root / "notities" / "januari.md").write_text("# Januari\n", encoding="utf-8")
    (root / "notities" / "februari.md").write_text("# Februari\n", encoding="utf-8")
    return root


def _fingerprint(root: Path) -> dict[str, tuple[bytes, int]]:
    """Every file under ``root``: its bytes and its modification time.

    The time as well as the bytes, because a copy that preserved the content while
    touching the modification date would still be a change to the reader's files.
    """
    out: dict[str, tuple[bytes, int]] = {}
    for path in sorted(root.rglob("*")):
        if path.is_file():
            stat = path.stat()
            out[path.relative_to(root).as_posix()] = (path.read_bytes(), stat.st_mtime_ns)
    return out


# ---------------------------------------------------------------------------
# The property the whole feature rests on
# ---------------------------------------------------------------------------


def test_the_source_folder_is_not_written_to(source: Path) -> None:
    """Read, copied out, and otherwise untouched.

    Checked against the filesystem rather than against a promise in the code: the
    reader's own project is the thing at risk here, and only its bytes and
    timestamps are evidence.
    """
    before = _fingerprint(source)

    import_folder(WORKSPACE, source)

    assert _fingerprint(source) == before
    # The folder itself is still a folder with its own contents, not emptied and
    # not replaced by a copy of itself.
    assert (source / "verslag.md").is_file()
    assert sorted(p.name for p in source.iterdir()) == ["notities", "verslag.md"]


def test_the_documents_land_under_the_folders_name_with_their_structure(source: Path) -> None:
    result = import_folder(WORKSPACE, source)

    assert result.folder_name == "mijn-project"
    assert result.found == 3
    inbox = workspace_inbox(WORKSPACE)
    assert (inbox / "mijn-project" / "verslag.md").read_text(encoding="utf-8") == (
        "# Verslag\n\nDe eerste.\n"
    )
    # The subfolder is kept: where a document came from is the one piece of
    # context the reader already had and Apollo cannot work out again.
    assert (inbox / "mijn-project" / "notities" / "januari.md").is_file()
    assert sorted(f.path for f in result.copied) == [
        f"{INBOX_DIR}/mijn-project/notities/februari.md",
        f"{INBOX_DIR}/mijn-project/notities/januari.md",
        f"{INBOX_DIR}/mijn-project/verslag.md",
    ]


def test_every_copied_document_is_readable_back(source: Path) -> None:
    result = import_folder(WORKSPACE, source)

    assert [f.readable for f in result.copied] == [True, True, True]
    assert result.refused == []


def test_nothing_is_overwritten_when_the_same_folder_is_added_twice(source: Path) -> None:
    """Adding a folder again is a plausible thing to do deliberately, and the
    second copy must not be the first copy with different content."""
    import_folder(WORKSPACE, source)
    import_folder(WORKSPACE, source)

    inbox = workspace_inbox(WORKSPACE)
    assert (inbox / "mijn-project" / "verslag.md").read_text(encoding="utf-8") == (
        "# Verslag\n\nDe eerste.\n"
    )
    assert (inbox / "mijn-project" / "verslag-2.md").is_file()
    # And the source is still the single original it was.
    assert sorted(p.name for p in source.iterdir()) == ["notities", "verslag.md"]


def test_a_name_already_dropped_in_the_inbox_is_not_replaced(source: Path) -> None:
    """A document dropped in by hand and a document copied out of a folder are
    both the reader's; neither may replace the other."""
    from app.services.storage import store_upload

    store_upload(WORKSPACE, "verslag.md", b"# Iets anders\n")
    import_folder(WORKSPACE, source)

    inbox = workspace_inbox(WORKSPACE)
    assert (inbox / "verslag.md").read_text(encoding="utf-8") == "# Iets anders\n"
    assert (inbox / "mijn-project" / "verslag.md").read_text(encoding="utf-8") == (
        "# Verslag\n\nDe eerste.\n"
    )


# ---------------------------------------------------------------------------
# What is refused, and why
# ---------------------------------------------------------------------------


def test_apollos_own_storage_is_refused_as_a_source() -> None:
    """Pointing at the working folder would copy Apollo's output into itself, and
    it would look exactly like a successful import while doing it."""
    with pytest.raises(StorageError) as caught:
        import_folder(WORKSPACE, storage_root())

    assert "own storage" in str(caught.value)


def test_a_folder_containing_apollos_storage_is_refused(tmp_path: Path) -> None:
    """The other direction: the walk would step into Apollo's own tree and copy
    it back out."""
    with pytest.raises(StorageError) as caught:
        import_folder(WORKSPACE, tmp_path)

    assert "contains Apollo" in str(caught.value)


def test_a_folder_that_does_not_exist_is_refused() -> None:
    with pytest.raises(StorageError) as caught:
        import_folder(WORKSPACE, "/nee/deze/map/bestaat/niet")

    assert "does not exist" in str(caught.value)


def test_a_file_rather_than_a_folder_is_refused(source: Path) -> None:
    with pytest.raises(StorageError) as caught:
        import_folder(WORKSPACE, source / "verslag.md")

    assert "not a folder" in str(caught.value)


def test_an_empty_file_is_refused_with_a_reason(source: Path) -> None:
    (source / "leeg.md").write_bytes(b"")

    result = import_folder(WORKSPACE, source)

    assert result.refused[0].source_path.endswith("leeg.md")
    assert "empty" in result.refused[0].reason


def test_files_that_are_not_documents_are_neither_copied_nor_refused(source: Path) -> None:
    """Nobody offered a screenshot, so nobody is waiting for one. It is counted in
    ``found`` and otherwise ignored -- listing it as refused would be a long list
    of things the reader never asked about."""
    (source / "diagram.png").write_bytes(b"\x89PNG\r\n")
    (source / "script.py").write_text("x = 1\n", encoding="utf-8")

    result = import_folder(WORKSPACE, source)

    assert [f.name for f in result.copied] == [
        "februari.md",
        "januari.md",
        "verslag.md",
    ]
    assert result.refused == []
    assert result.found == 5


def test_noise_folders_are_not_walked(source: Path) -> None:
    """A nested repository and a dependency tree are not the reader's work, and
    walking them would spend the whole import budget on them."""
    (source / ".git").mkdir()
    (source / ".git" / "config.md").write_text("# geen document\n", encoding="utf-8")
    (source / "node_modules" / "pkg").mkdir(parents=True)
    (source / "node_modules" / "pkg" / "readme.md").write_text("# pkg\n", encoding="utf-8")

    result = import_folder(WORKSPACE, source)

    assert [f.name for f in result.copied] == [
        "februari.md",
        "januari.md",
        "verslag.md",
    ]


def test_a_symlink_is_not_followed(source: Path, tmp_path: Path) -> None:
    """A link is a way out of the folder the reader offered. Following one back
    into Apollo's own storage would copy the inbox into itself."""
    outside = tmp_path / "buiten"
    outside.mkdir()
    (outside / "extern.md").write_text("# buiten\n", encoding="utf-8")
    try:
        (source / "link").symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("symlinks are not available on this machine")

    result = import_folder(WORKSPACE, source)

    assert [f.name for f in result.copied] == [
        "februari.md",
        "januari.md",
        "verslag.md",
    ]


def test_a_folder_larger_than_the_import_budget_says_so(
    source: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Truncated, and telling the reader.
    
    A collection that looks complete when it is not is the failure this exists to
    prevent, so the cap is a partial result with a flag rather than a refusal.
    """
    monkeypatch.setattr(storage_service, "MAX_IMPORT_FILES", 2)

    result = import_folder(WORKSPACE, source)

    assert result.truncated is True
    assert len(result.copied) == 2

# ---------------------------------------------------------------------------
# Over HTTP
# ---------------------------------------------------------------------------


def test_the_endpoint_copies_the_folder_and_registers_the_repository(
    client: TestClient, workspace: dict, source: Path
) -> None:
    response = client.post(
        f"/api/workspaces/{workspace['id']}/inbox/import-folder",
        json={"path": str(source)},
    )

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["folder_name"] == "mijn-project"
    assert len(body["copied"]) == 3
    assert body["refused"] == []
    assert body["truncated"] is False
    # The repository is registered by this call, so the collection is openable
    # straight away.
    assert body["repository_id"] is not None
    listing = client.get(f"/api/workspaces/{workspace['id']}/inbox").json()
    assert len(listing["files"]) == 3


def test_the_endpoint_refuses_a_folder_that_is_not_there(
    client: TestClient, workspace: dict
) -> None:
    response = client.post(
        f"/api/workspaces/{workspace['id']}/inbox/import-folder",
        json={"path": "/nee/deze/map"},
    )

    assert response.status_code == 400
    assert "does not exist" in response.json()["detail"]


def test_nothing_the_endpoint_does_can_delete_from_the_source(
    client: TestClient, workspace: dict, source: Path
) -> None:
    before = _fingerprint(source)

    client.post(
        f"/api/workspaces/{workspace['id']}/inbox/import-folder",
        json={"path": str(source)},
    )

    assert _fingerprint(source) == before


# ---------------------------------------------------------------------------
# The listing, now that the inbox can be a tree
# ---------------------------------------------------------------------------


def test_the_inbox_lists_documents_from_subfolders(source: Path) -> None:
    import_folder(WORKSPACE, source)

    assert [e.path for e in list_inbox(WORKSPACE)] == [
        f"{INBOX_DIR}/mijn-project/notities/februari.md",
        f"{INBOX_DIR}/mijn-project/notities/januari.md",
        f"{INBOX_DIR}/mijn-project/verslag.md",
    ]


def test_a_document_from_a_subfolder_opens_by_its_own_path(source: Path) -> None:
    """The listing is only useful if the path it reports is the path that opens."""
    from app.services.documents import read_document

    import_folder(WORKSPACE, source)

    text = read_document(
        workspace_storage(WORKSPACE), f"{INBOX_DIR}/mijn-project/notities/januari.md"
    )

    assert "Januari" in text
