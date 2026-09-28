"""The arrangement, the archive, and the guarantee that nothing is deleted.

The product rule these tests exist to pin: a visual group is a *view*, and
arranging documents is a normal thing a person does while reading. If
arranging could move, rename or lose a file, the feature would be unusable --
you would not drag a document freely if every drag rewrote the repository.

So each test here checks the same thing from a different side: the arrangement
lives in the database, and the files on disk are untouched.
"""
from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy.orm import Session

from app.models import Group, GroupPlacement
from app.services.placement import (
    PlacementError,
    create_group,
    delete_group,
    get_or_create_archive,
    group_documents,
    groups_of_document,
    list_groups,
    move_document,
    place_document,
    remove_document,
    rename_group,
)


def _doc_repo_id(workspace: dict) -> int:
    for repo in workspace["repositories"]:
        if repo["name"] == "gaia-docs":
            return repo["id"]
    raise AssertionError("the documentation repository is not registered")


def _before(root: Path) -> dict[str, bytes]:
    """Every file in the repository, by path. The witness of 'nothing moved'."""
    return {
        str(p.relative_to(root)).replace("\\", "/"): p.read_bytes()
        for p in sorted(root.rglob("*"))
        if p.is_file() and ".git" not in p.parts
    }


# ---------------------------------------------------------------------------
# Creating and editing groups
# ---------------------------------------------------------------------------


def test_a_group_is_created_with_the_readers_name(session: Session, workspace: dict) -> None:
    group = create_group(session, workspace["id"], "Apollo", description="Our own work")

    assert group.name == "Apollo"
    assert group.source == "user"
    assert group.is_archive is False


def test_a_group_needs_a_name(session: Session, workspace: dict) -> None:
    with pytest.raises(PlacementError):
        create_group(session, workspace["id"], "   ")


def test_two_groups_cannot_share_a_name(session: Session, workspace: dict) -> None:
    create_group(session, workspace["id"], "Gaia")

    # Not an error about the name being reserved -- a clear conflict the reader
    # can act on by choosing a different one.
    with pytest.raises(PlacementError, match="already exists"):
        create_group(session, workspace["id"], "Gaia")


def test_groups_are_listed_with_their_document_count(
    session: Session, workspace: dict
) -> None:
    group = create_group(session, workspace["id"], "Gaia")

    place_document(session, workspace["id"], group.id, _doc_repo_id(workspace), "notes.md")
    place_document(
        session, workspace["id"], group.id, _doc_repo_id(workspace), "README.md"
    )

    views = list_groups(session, workspace["id"])
    assert [v.name for v in views] == ["Gaia"]
    assert views[0].document_count == 2


def test_renaming_a_group_does_not_move_its_documents(
    session: Session, workspace: dict
) -> None:
    group = create_group(session, workspace["id"], "Old name")
    place_document(session, workspace["id"], group.id, _doc_repo_id(workspace), "notes.md")

    rename_group(session, workspace["id"], group.id, name="New name")

    assert [d.file_path for d in group_documents(session, workspace["id"], group.id)] == [
        "notes.md"
    ]


# ---------------------------------------------------------------------------
# The core guarantee: the filesystem is not involved
# ---------------------------------------------------------------------------


def test_creating_a_group_writes_no_file(
    session: Session, workspace: dict, doc_repo: Path
) -> None:
    before = _before(doc_repo)

    create_group(session, workspace["id"], "Gaia")

    assert _before(doc_repo) == before


def test_placing_a_document_moves_nothing_on_disk(
    session: Session, workspace: dict, doc_repo: Path
) -> None:
    before = _before(doc_repo)
    group = create_group(session, workspace["id"], "Gaia")

    place_document(session, workspace["id"], group.id, _doc_repo_id(workspace), "notes.md")

    assert _before(doc_repo) == before
    assert (doc_repo / "notes.md").is_file()


def test_moving_a_document_between_groups_moves_no_file(
    session: Session, workspace: dict, doc_repo: Path
) -> None:
    """The whole product rests on this one.

    Dragging is rearranging the view. If the file moved too, the folders on disk
    would churn with every correction, and the stable physical structure the
    user relies on would be a mirage.
    """
    before = _before(doc_repo)
    repo_id = _doc_repo_id(workspace)
    left = create_group(session, workspace["id"], "Left")
    right = create_group(session, workspace["id"], "Right")
    place_document(session, workspace["id"], left.id, repo_id, "notes.md")

    move_document(
        session, workspace["id"], repo_id, "notes.md",
        from_group_id=left.id, to_group_id=right.id,
    )

    assert _before(doc_repo) == before
    assert group_documents(session, workspace["id"], left.id) == []
    assert [d.file_path for d in group_documents(session, workspace["id"], right.id)] == [
        "notes.md"
    ]


def test_deleting_a_group_removes_only_the_view(
    session: Session, workspace: dict, doc_repo: Path
) -> None:
    before = _before(doc_repo)
    group = create_group(session, workspace["id"], "Gaia")
    place_document(session, workspace["id"], group.id, _doc_repo_id(workspace), "notes.md")

    delete_group(session, workspace["id"], group.id)

    assert _before(doc_repo) == before
    assert (doc_repo / "notes.md").is_file()
    assert list_groups(session, workspace["id"]) == []
    # And nothing is left pointing at the group.
    assert session.query(GroupPlacement).count() == 0


def test_removing_a_document_from_a_group_leaves_the_file(
    session: Session, workspace: dict, doc_repo: Path
) -> None:
    before = _before(doc_repo)
    group = create_group(session, workspace["id"], "Gaia")
    place_document(session, workspace["id"], group.id, _doc_repo_id(workspace), "notes.md")

    remove_document(
        session, workspace["id"], group.id, _doc_repo_id(workspace), "notes.md"
    )

    assert group_documents(session, workspace["id"], group.id) == []
    assert _before(doc_repo) == before


# ---------------------------------------------------------------------------
# Placements are about documents, and documents are real
# ---------------------------------------------------------------------------


def test_a_document_can_be_in_several_groups_without_being_moved(
    session: Session, workspace: dict, doc_repo: Path
) -> None:
    """Being in two groups is not a claim that it was moved.

    A planning document belongs to its project and to its own topic. Forcing a
    single home would make the user choose, and the answer would be a lie.
    """
    before = _before(doc_repo)
    repo_id = _doc_repo_id(workspace)
    one = create_group(session, workspace["id"], "Projecten")
    two = create_group(session, workspace["id"], "Referentie")

    place_document(session, workspace["id"], one.id, repo_id, "notes.md")
    place_document(session, workspace["id"], two.id, repo_id, "notes.md")

    assert {g.name for g in groups_of_document(session, workspace["id"], repo_id, "notes.md")} == {
        "Projecten",
        "Referentie",
    }
    assert _before(doc_repo) == before


def test_placing_a_document_twice_is_one_placement(
    session: Session, workspace: dict
) -> None:
    group = create_group(session, workspace["id"], "Gaia")
    repo_id = _doc_repo_id(workspace)

    place_document(session, workspace["id"], group.id, repo_id, "notes.md")
    place_document(session, workspace["id"], group.id, repo_id, "notes.md")

    # A drag onto a group it is already in is picking something up and putting
    # it down, not a form submission that can fail.
    assert len(group_documents(session, workspace["id"], group.id)) == 1


def test_dropping_onto_the_same_group_is_not_an_error(
    session: Session, workspace: dict
) -> None:
    group = create_group(session, workspace["id"], "Gaia")
    repo_id = _doc_repo_id(workspace)
    place_document(session, workspace["id"], group.id, repo_id, "notes.md")

    move_document(
        session, workspace["id"], repo_id, "notes.md",
        from_group_id=group.id, to_group_id=group.id,
    )

    assert len(group_documents(session, workspace["id"], group.id)) == 1


def test_a_document_that_is_not_there_cannot_be_placed(
    session: Session, workspace: dict
) -> None:
    """A card that opens to an error is worse than no card.

    The reader would have no way to tell it apart from a real document.
    """
    group = create_group(session, workspace["id"], "Gaia")

    with pytest.raises(PlacementError, match="Not a file"):
        place_document(
            session, workspace["id"], group.id, _doc_repo_id(workspace), "nope.md"
        )


def test_a_path_escaping_the_repository_cannot_be_placed(
    session: Session, workspace: dict
) -> None:
    group = create_group(session, workspace["id"], "Gaia")

    with pytest.raises(PlacementError, match="traversal"):
        place_document(
            session, workspace["id"], group.id, _doc_repo_id(workspace),
            "../../../etc/passwd",
        )


def test_a_file_that_is_not_a_document_cannot_be_placed(
    session: Session, workspace: dict, doc_repo: Path
) -> None:
    # A .py is a real file in the repository and is not a document. The tree
    # does not list it, so a group that held it would be showing something the
    # reading pane cannot open.
    (doc_repo / "script.py").write_text("x = 1\n", encoding="utf-8")
    group = create_group(session, workspace["id"], "Gaia")

    with pytest.raises(PlacementError, match="not a document"):
        place_document(
            session, workspace["id"], group.id, _doc_repo_id(workspace), "script.py"
        )


def test_a_document_from_another_repository_cannot_be_placed(
    session: Session, workspace: dict
) -> None:
    """Groups are workspace-scoped, so a foreign repository id is refused.

    Without this, a caller could name a repository from a different workspace and
    have a group in this one point into it.
    """
    group = create_group(session, workspace["id"], "Gaia")

    with pytest.raises(PlacementError, match="not registered"):
        place_document(session, workspace["id"], group.id, 9999, "notes.md")


def test_a_group_from_another_workspace_is_not_reachable(
    session: Session, client, workspace: dict
) -> None:
    other = client.post("/api/workspaces", json={"name": "Other"}).json()
    group = create_group(session, workspace["id"], "Gaia")

    with pytest.raises(PlacementError, match="not found"):
        remove_document(session, other["id"], group.id, _doc_repo_id(workspace), "notes.md")


# ---------------------------------------------------------------------------
# The archive
# ---------------------------------------------------------------------------


def test_the_archive_is_created_once_and_kept(
    session: Session, workspace: dict
) -> None:
    first = get_or_create_archive(session, workspace["id"])
    second = get_or_create_archive(session, workspace["id"])

    # One archive, not several: "is this archived?" has to have one answer.
    assert first.id == second.id
    assert first.is_archive is True


def test_a_second_archive_cannot_be_created(session: Session, workspace: dict) -> None:
    get_or_create_archive(session, workspace["id"])

    with pytest.raises(PlacementError, match="already has an archive"):
        create_group(session, workspace["id"], "Oude dingen", is_archive=True)


def test_the_archive_is_listed_last(session: Session, workspace: dict) -> None:
    """It is a destination, not a group to be lost among the current ones."""
    create_group(session, workspace["id"], "Alpha")
    get_or_create_archive(session, workspace["id"])
    create_group(session, workspace["id"], "Beta")

    names = [v.name for v in list_groups(session, workspace["id"])]
    assert names == ["Alpha", "Beta", "Archief"]


def test_archiving_a_document_keeps_the_file(
    session: Session, workspace: dict, doc_repo: Path
) -> None:
    """The product's hard rule, asserted where it could plausibly break.

    Archiving moves a document out of the current view. It must not remove it,
    and there is no code path here that could: the placement is a database row
    and the file is never enumerated, let alone unlinked.
    """
    before = _before(doc_repo)
    archive = get_or_create_archive(session, workspace["id"])

    place_document(
        session, workspace["id"], archive.id, _doc_repo_id(workspace), "notes.md"
    )

    assert _before(doc_repo) == before
    assert (doc_repo / "notes.md").is_file()
    assert (doc_repo / "notes.md").read_text(encoding="utf-8").startswith("# Scratch")


def test_an_archived_document_can_be_brought_back(
    session: Session, workspace: dict, doc_repo: Path
) -> None:
    """Archiving is reversible, which is what makes it safe to propose."""
    repo_id = _doc_repo_id(workspace)
    archive = get_or_create_archive(session, workspace["id"])
    current = create_group(session, workspace["id"], "Current")
    place_document(session, workspace["id"], archive.id, repo_id, "notes.md")

    move_document(
        session, workspace["id"], repo_id, "notes.md",
        from_group_id=archive.id, to_group_id=current.id,
    )

    assert group_documents(session, workspace["id"], archive.id) == []
    assert len(group_documents(session, workspace["id"], current.id)) == 1
    assert (doc_repo / "notes.md").is_file()


def test_the_archive_starts_empty(session: Session, workspace: dict) -> None:
    """Creating it must not sweep anything in.

    The opposite would be a silent way to make documents disappear from view.
    """
    archive = get_or_create_archive(session, workspace["id"])
    assert group_documents(session, workspace["id"], archive.id) == []


def test_groups_are_cascaded_when_a_workspace_is_deleted(
    session: Session, client, workspace: dict
) -> None:
    """Deleting a workspace must not leave rows pointing at a workspace that is gone.

    Groups and placements are the arrangement over documents; once the workspace
    is gone the documents are unknown to the app, and the rows would be orphans.
    """
    group = create_group(session, workspace["id"], "Gaia")
    place_document(session, workspace["id"], group.id, _doc_repo_id(workspace), "notes.md")

    client.delete(f"/api/workspaces/{workspace['id']}")

    assert session.query(Group).filter_by(workspace_id=workspace["id"]).count() == 0
    assert session.query(GroupPlacement).count() == 0
