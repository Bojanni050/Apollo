"""A group that names a folder, and the move that follows from it.

The service tests elsewhere pin the arrangement: placing, dragging, deleting a
group, none of which touches a file. These pin the other half -- the point where
a group *does* have a consequence on disk, and where the three things that could
go wrong are checked:

* **A drop proposes, it does not move.** The reader has to say yes. A test that
  only asserted "the file ended up in the folder" would pass just as happily for
  an implementation that moved it immediately, which is precisely the behaviour
  this product does not have.
* **A move re-points what named the old path.** A placement left pointing at a
  moved file is a card that opens to an error, and that is indistinguishable
  from a lost document. The test asserts the follow, not the move.
* **Nothing is derived.** A group's folder is written down by the reader, so
  refusing a bad one matters more than accepting a good one.
"""
from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.models import DocSignal
from app.services.filing import (
    ARCHIVE_FOLDER,
    repoint_after_move,
    set_group_folder,
    validate_folder,
)
from app.services.placement import PlacementError, get_or_create_archive

pytestmark = pytest.mark.filterwarnings("ignore::DeprecationWarning")


def _base(workspace: dict) -> str:
    return f"/api/workspaces/{workspace['id']}/groups"


def _doc_repo_id(workspace: dict) -> int:
    return next(r["id"] for r in workspace["repositories"] if r["name"] == "gaia-docs")


def _source_repo_id(workspace: dict) -> int:
    return next(r["id"] for r in workspace["repositories"] if r["name"] == "gaia-service")


def _everything(root: Path) -> dict[str, bytes]:
    return {
        str(p.relative_to(root)).replace("\\", "/") if p.is_file() else str(
            p.relative_to(root)
        ).replace("\\", "/") + "/": p.read_bytes() if p.is_file() else b""
        for p in sorted(root.rglob("*"))
        if ".git" not in p.parts
    }


# ---------------------------------------------------------------------------
# The name of a folder
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("bad", ["a/b", "a\\b", "..", ".", "", "   ", 'x"y', "a|b", "a?b", "a*b"])
def test_a_folder_name_that_is_not_one_plain_name_is_refused(bad: str) -> None:
    """One level, no separators, nothing Windows would refuse.

    Each of these would either create a tree the interface never shows or fail
    much later inside a move, as an OSError with no explanation.
    """
    with pytest.raises(PlacementError):
        validate_folder(bad)


def test_the_inbox_cannot_be_a_group_folder() -> None:
    """``Inbox`` is where dropped documents arrive.

    A group filed into it would make the inbox both the entrance and a filing
    destination, and a document moved there would appear to have been newly
    dropped in -- twice.
    """
    for spelling in ("Inbox", "inbox", "INBOX"):
        with pytest.raises(PlacementError):
            validate_folder(spelling)


def test_a_group_gets_no_folder_unless_it_is_asked_for(
    client: TestClient, workspace: dict
) -> None:
    base = _base(workspace)

    created = client.post(base, json={"name": "Projecten"})

    assert created.status_code == 201
    assert created.json()["folder"] is None


def test_a_group_is_created_with_the_folder_it_was_given(
    client: TestClient, workspace: dict
) -> None:
    base = _base(workspace)

    created = client.post(base, json={"name": "Notities", "folder": "Notities"})

    assert created.status_code == 201
    assert created.json()["folder"] == "Notities"


def test_a_bad_folder_is_refused_at_creation_with_the_reason(
    client: TestClient, workspace: dict
) -> None:
    base = _base(workspace)

    created = client.post(base, json={"name": "Projecten", "folder": "a/b"})

    assert created.status_code == 400
    assert "single name" in created.json()["detail"]


def test_renaming_a_group_leaves_its_folder_alone(
    client: TestClient, workspace: dict
) -> None:
    """A rename request that says nothing about the folder must not clear it.

    This is the subtle one: ``folder`` defaults to None, so reading the
    attribute instead of checking whether the field was sent would wipe the
    folder on every rename -- and the reader would find out only when the next
    document silently stopped proposing a move.
    """
    base = _base(workspace)
    gid = client.post(base, json={"name": "Oud", "folder": "Notities"}).json()["id"]

    renamed = client.patch(f"{base}/{gid}", json={"name": "Nieuw"})

    assert renamed.status_code == 200
    assert renamed.json()["name"] == "Nieuw"
    assert renamed.json()["folder"] == "Notities"


def test_clearing_a_folder_needs_an_explicit_null(
    client: TestClient, workspace: dict
) -> None:
    base = _base(workspace)
    gid = client.post(base, json={"name": "Notities", "folder": "Notities"}).json()["id"]

    cleared = client.patch(f"{base}/{gid}", json={"folder": None})

    assert cleared.status_code == 200
    assert cleared.json()["folder"] is None


def test_setting_a_folder_moves_nothing(
    client: TestClient, workspace: dict, doc_repo: Path
) -> None:
    """Naming a folder is a statement, not an instruction.

    The documents already in the group stay exactly where they are. Moving them
    would mean a single click could relocate a dozen files that nobody had
    looked at yet.
    """
    base = _base(workspace)
    before = _everything(doc_repo)
    gid = client.post(base, json={"name": "Notities"}).json()["id"]
    client.post(
        f"{base}/{gid}/documents",
        json={"repository_id": _doc_repo_id(workspace), "path": "notes.md"},
    )

    client.patch(f"{base}/{gid}", json={"folder": "Notities"})

    assert _everything(doc_repo) == before


# ---------------------------------------------------------------------------
# The drop: propose, never move
# ---------------------------------------------------------------------------


def test_a_drop_into_a_view_group_proposes_nothing(
    client: TestClient, workspace: dict, doc_repo: Path
) -> None:
    base = _base(workspace)
    gid = client.post(base, json={"name": "Projecten"}).json()["id"]
    before = _everything(doc_repo)

    dropped = client.post(
        f"{base}/{gid}/documents",
        json={"repository_id": _doc_repo_id(workspace), "path": "notes.md"},
    )

    assert dropped.status_code == 201
    assert dropped.json()["proposal_id"] is None
    assert _everything(doc_repo) == before
    assert client.get(f"{base}/{gid}/documents").json()[0]["path"] == "notes.md"


def test_a_drop_into_a_folder_group_proposes_a_move_and_moves_nothing(
    client: TestClient, workspace: dict, doc_repo: Path
) -> None:
    """The acceptance criterion of this phase, in one test.

    The drop succeeds, a proposal appears, and the file has not moved. Accepting
    is a separate, deliberate act.
    """
    base = _base(workspace)
    gid = client.post(base, json={"name": "Notities", "folder": "Notities"}).json()["id"]
    before = _everything(doc_repo)

    dropped = client.post(
        f"{base}/{gid}/documents",
        json={"repository_id": _doc_repo_id(workspace), "path": "notes.md"},
    )

    assert dropped.status_code == 201
    proposal_id = dropped.json()["proposal_id"]
    assert proposal_id is not None

    # Nothing on disk has changed, and not even the folder exists.
    assert _everything(doc_repo) == before
    assert not (doc_repo / "Notities").exists()

    # The group already has the document, even though the file has not moved.
    assert [d["path"] for d in client.get(f"{base}/{gid}/documents").json()] == [
        "notes.md"
    ]

    # And the proposal says what it would do, in a sentence.
    proposal = client.get(
        f"/api/workspaces/{workspace['id']}/proposals/{proposal_id}"
    ).json()
    assert proposal["kind"] == "move"
    assert proposal["status"] == "pending"
    assert "Notities" in proposal["reason"]
    change = proposal["changes"][0]
    assert change["source_path"] == "notes.md"
    assert change["target_path"] == "Notities/notes.md"


def test_declining_the_proposal_leaves_everything_alone(
    client: TestClient, workspace: dict, doc_repo: Path
) -> None:
    base = _base(workspace)
    gid = client.post(base, json={"name": "Notities", "folder": "Notities"}).json()["id"]
    before = _everything(doc_repo)
    proposal_id = client.post(
        f"{base}/{gid}/documents",
        json={"repository_id": _doc_repo_id(workspace), "path": "notes.md"},
    ).json()["proposal_id"]

    declined = client.post(
        f"/api/workspaces/{workspace['id']}/proposals/{proposal_id}/reject"
    )

    assert declined.status_code == 200
    assert declined.json()["status"] == "rejected"
    assert _everything(doc_repo) == before
    # The document is still a member: declining the move is not undoing the drop.
    assert len(client.get(f"{base}/{gid}/documents").json()) == 1


def test_accepting_moves_the_file_and_creates_the_folder(
    client: TestClient, workspace: dict, doc_repo: Path
) -> None:
    base = _base(workspace)
    gid = client.post(base, json={"name": "Notities", "folder": "Notities"}).json()["id"]
    proposal_id = client.post(
        f"{base}/{gid}/documents",
        json={"repository_id": _doc_repo_id(workspace), "path": "notes.md"},
    ).json()["proposal_id"]

    accepted = client.post(
        f"/api/workspaces/{workspace['id']}/proposals/{proposal_id}/accept"
    )

    assert accepted.status_code == 200
    assert accepted.json()["applied_paths"] == ["Notities/notes.md"]
    assert (doc_repo / "Notities" / "notes.md").exists()
    assert not (doc_repo / "notes.md").exists()
    # The content is untouched: a filing move is not a rewrite.
    assert (doc_repo / "Notities" / "notes.md").read_text(encoding="utf-8").startswith(
        "# Scratch"
    )


def test_the_group_follows_the_document_to_its_new_path(
    client: TestClient, workspace: dict, doc_repo: Path
) -> None:
    """A placement left on the old path is a card that opens to an error.

    The reader could not tell that apart from a document having gone missing, so
    the arrangement is re-pointed as part of the move rather than afterwards.
    """
    base = _base(workspace)
    gid = client.post(base, json={"name": "Notities", "folder": "Notities"}).json()["id"]
    proposal_id = client.post(
        f"{base}/{gid}/documents",
        json={"repository_id": _doc_repo_id(workspace), "path": "notes.md"},
    ).json()["proposal_id"]

    client.post(f"/api/workspaces/{workspace['id']}/proposals/{proposal_id}/accept")

    assert [d["path"] for d in client.get(f"{base}/{gid}/documents").json()] == [
        "Notities/notes.md"
    ]
    # And the document still knows which groups it is in, under its new path.
    groups = client.get(
        f"/api/workspaces/{workspace['id']}/documents/groups",
        params={"repository_id": _doc_repo_id(workspace), "path": "Notities/notes.md"},
    ).json()
    assert [g["id"] for g in groups] == [gid]


def test_a_document_in_two_groups_follows_the_move_in_both(
    client: TestClient, workspace: dict, doc_repo: Path
) -> None:
    """Membership is a join table, so a move has to update every row.

    A document can be in more than one group; re-pointing only the group whose
    folder it moved into would leave the other one holding a dead path.
    """
    base = _base(workspace)
    notes = client.post(base, json={"name": "Los"}).json()["id"]
    filed = client.post(
        base, json={"name": "Notities", "folder": "Notities"}
    ).json()["id"]
    repo = _doc_repo_id(workspace)
    client.post(
        f"{base}/{notes}/documents", json={"repository_id": repo, "path": "notes.md"}
    )
    proposal_id = client.post(
        f"{base}/{filed}/documents", json={"repository_id": repo, "path": "notes.md"}
    ).json()["proposal_id"]

    client.post(f"/api/workspaces/{workspace['id']}/proposals/{proposal_id}/accept")

    for gid in (notes, filed):
        paths = [d["path"] for d in client.get(f"{base}/{gid}/documents").json()]
        assert paths == ["Notities/notes.md"], f"groep {gid} hield een dode path"


def test_a_second_drop_while_a_proposal_waits_does_not_double_it(
    client: TestClient, workspace: dict
) -> None:
    """One waiting card per document and target.

    Two cards proposing the same move would leave the reader choosing between
    two identical questions, which reads as a bug in the app rather than a
    choice.
    """
    base = _base(workspace)
    gid = client.post(base, json={"name": "Notities", "folder": "Notities"}).json()["id"]
    repo = _doc_repo_id(workspace)

    first = client.post(
        f"{base}/{gid}/documents", json={"repository_id": repo, "path": "notes.md"}
    ).json()["proposal_id"]
    second = client.post(
        f"{base}/{gid}/documents", json={"repository_id": repo, "path": "notes.md"}
    ).json()["proposal_id"]

    assert first is not None
    assert second is None
    pending = client.get(
        f"/api/workspaces/{workspace['id']}/proposals", params={"status_filter": "pending"}
    ).json()
    assert len([p for p in pending if p["kind"] == "move"]) == 1


def test_a_document_already_in_the_folder_proposes_nothing(
    client: TestClient, workspace: dict, doc_repo: Path
) -> None:
    """Putting it in the group it already belongs to is leaving it there.

    Without this, every visit to the board would manufacture a move card for a
    document that is already where the group wants it.
    """
    base = _base(workspace)
    gid = client.post(base, json={"name": "Notities", "folder": "Notities"}).json()["id"]
    repo = _doc_repo_id(workspace)
    (doc_repo / "Notities").mkdir()
    (doc_repo / "Notities" / "notes.md").write_text("# Scratch\n", encoding="utf-8")

    dropped = client.post(
        f"{base}/{gid}/documents",
        json={"repository_id": repo, "path": "Notities/notes.md"},
    )

    assert dropped.status_code == 201
    assert dropped.json()["proposal_id"] is None


def test_a_read_only_repository_proposes_nothing(
    client: TestClient, workspace: dict, session: Session
) -> None:
    """A read-only repository cannot be moved into, so it is not offered.

    Filing a proposal that fails at acceptance teaches the reader that accepting
    is unreliable, which is worse than not offering. The placement is refused
    even earlier -- a source file is not a document this board arranges -- so
    this asserts the deeper guarantee directly, on a document that *is* one.
    """
    from app.services.filing import plan_filing
    from app.services.placement import get_group

    gid = client.post(_base(workspace), json={"name": "Code", "folder": "Code"}).json()["id"]
    group = get_group(session, workspace["id"], gid)

    # plan_move would refuse a .py anyway; what is being pinned here is that the
    # writability check happens first, so no proposal is even attempted.
    proposal = plan_filing(
        session, workspace["id"], group, _source_repo_id(workspace), "src/memory.py"
    )

    assert proposal is None


# ---------------------------------------------------------------------------
# The archive
# ---------------------------------------------------------------------------


def test_the_archive_stands_for_one_folder(
    client: TestClient, workspace: dict, session: Session
) -> None:
    archive = get_or_create_archive(session, workspace["id"])

    assert archive.folder == ARCHIVE_FOLDER


def test_an_archive_older_than_the_folder_column_gets_one(
    session: Session, workspace: dict
) -> None:
    """A workspace that archived before this phase keeps working.

    The column is nullable, so an existing archive has none; it is filled in on
    the next use rather than by a migration, because until something is actually
    archived the folder is not a fact about anything.
    """
    archive = get_or_create_archive(session, workspace["id"])
    archive.folder = None
    session.commit()

    again = get_or_create_archive(session, workspace["id"])

    assert again.folder == ARCHIVE_FOLDER


def test_the_archive_cannot_be_pointed_somewhere_else(
    client: TestClient, workspace: dict, session: Session
) -> None:
    archive = get_or_create_archive(session, workspace["id"])
    base = _base(workspace)

    refused = client.patch(
        f"{base}/{archive.id}", json={"folder": "Verleden"}
    )

    assert refused.status_code == 400
    assert ARCHIVE_FOLDER in refused.json()["detail"]


def test_archiving_a_document_proposes_a_move_into_the_archive(
    client: TestClient, workspace: dict, session: Session, doc_repo: Path
) -> None:
    """Archiving is a move, and it goes through the same approval as any other.

    There is no delete-document route in this product, and archiving must not
    become the way around it: the document is kept, in a folder, and can be read
    there.
    """
    base = _base(workspace)
    archive_id = get_or_create_archive(session, workspace["id"]).id

    dropped = client.post(
        f"{base}/{archive_id}/documents",
        json={"repository_id": _doc_repo_id(workspace), "path": "notes.md"},
    )

    assert dropped.status_code == 201
    proposal_id = dropped.json()["proposal_id"]
    assert proposal_id is not None
    client.post(f"/api/workspaces/{workspace['id']}/proposals/{proposal_id}/accept")
    assert (doc_repo / ARCHIVE_FOLDER / "notes.md").exists()
    assert not (doc_repo / "notes.md").exists()


# ---------------------------------------------------------------------------
# Re-pointing, directly
# ---------------------------------------------------------------------------


def test_a_signal_follows_the_document_it_is_about(
    session: Session, workspace: dict
) -> None:
    wid = workspace["id"]
    repo = _doc_repo_id(workspace)
    signal = DocSignal(
        workspace_id=wid,
        repository_id=repo,
        file_path="notes.md",
        kind="outdated",
        reference="foundation/principles.md",
        why="It repeats a decision taken elsewhere.",
        confidence=0.7,
        status="new",
    )
    session.add(signal)
    session.commit()

    counts = repoint_after_move(session, wid, repo, "notes.md", "Notities/notes.md")

    assert counts["signals"] == 1
    session.refresh(signal)
    assert signal.file_path == "Notities/notes.md"


def test_a_reference_to_a_moved_document_follows_too(
    session: Session, workspace: dict
) -> None:
    """The easy one to forget, and it leaves a dead link in the sidebar.

    A signal *about* another document refers to it by path. If that document
    moves and the reference does not, the sidebar offers a link that opens to an
    error -- and the claim it was evidence for is now unverifiable.
    """
    wid = workspace["id"]
    repo = _doc_repo_id(workspace)
    signal = DocSignal(
        workspace_id=wid,
        repository_id=repo,
        file_path="architecture/overview.md",
        kind="outdated",
        reference="notes.md",
        why="The newer note says this is settled.",
        confidence=0.6,
        status="new",
    )
    session.add(signal)
    session.commit()

    repoint_after_move(session, wid, repo, "notes.md", "Notities/notes.md")

    session.refresh(signal)
    assert signal.reference == "Notities/notes.md"
    assert signal.file_path == "architecture/overview.md"


def test_another_repository_with_the_same_name_is_untouched(
    session: Session, workspace: dict
) -> None:
    """The pair is the identity, not the path.

    ``notes.md`` exists in more than one repository here, and a move in one of
    them must not silently repoint the other's rows.
    """
    wid = workspace["id"]
    docs = _doc_repo_id(workspace)
    source = _source_repo_id(workspace)
    signal = DocSignal(
        workspace_id=wid,
        repository_id=source,
        file_path="notes.md",
        kind="new",
        reference=None,
        why="Belongs to the other repository entirely.",
        confidence=0.5,
        status="new",
    )
    session.add(signal)
    session.commit()

    repoint_after_move(session, wid, docs, "notes.md", "Notities/notes.md")

    session.refresh(signal)
    assert signal.file_path == "notes.md"
    assert signal.repository_id == source


def test_moving_a_document_to_where_it_already_is_changes_nothing(
    session: Session, workspace: dict
) -> None:
    counts = repoint_after_move(
        session, workspace["id"], _doc_repo_id(workspace), "notes.md", "notes.md"
    )

    assert counts == {}


def test_a_search_chunk_gets_a_key_that_matches_its_new_path(
    session: Session, workspace: dict
) -> None:
    """The index key is a hash over the path, so it has to be recomputed.

    Rewriting only ``file_path`` would leave a row whose own identifier no longer
    describes it, and the next indexing run would add a second row beside it
    instead of updating it -- a duplicate that grows with every move.
    """
    from app.models import DocumentChunk
    from app.services.indexing import document_chunk_identifier

    wid = workspace["id"]
    repo = _doc_repo_id(workspace)
    chunk = DocumentChunk(
        repository_id=repo,
        document_id=f"{repo}:notes.md",
        file_path="notes.md",
        section="Scratch",
        start_line=1,
        end_line=3,
        content="# Scratch\n\nRandom thoughts.\n",
        content_hash="x" * 64,
        identifier=document_chunk_identifier(
            repo, "notes.md", "Scratch", "# Scratch\n\nRandom thoughts.\n"
        ),
        embedding_model="test",
        embedding_dimension=3,
    )
    session.add(chunk)
    session.commit()

    repoint_after_move(
        session, workspace["id"], repo, "notes.md", "Notities/notes.md"
    )

    session.refresh(chunk)
    assert chunk.file_path == "Notities/notes.md"
    assert chunk.document_id == f"{repo}:Notities/notes.md"
    # Recomputed the way the indexer computes it -- this is the whole point.
    assert chunk.identifier == document_chunk_identifier(
        repo, "Notities/notes.md", "Scratch", "# Scratch\n\nRandom thoughts.\n"
    )


# ---------------------------------------------------------------------------
# The service, without HTTP
# ---------------------------------------------------------------------------


def test_set_group_folder_round_trips_through_the_service(
    session: Session, workspace: dict
) -> None:
    from app.services.placement import create_group

    group = create_group(session, workspace["id"], "Referentie")

    set_group_folder(session, workspace["id"], group.id, "Referentie")
    session.refresh(group)
    assert group.folder == "Referentie"

    set_group_folder(session, workspace["id"], group.id, None)
    session.refresh(group)
    assert group.folder is None


def test_a_group_of_another_workspace_is_not_reachable(
    session: Session, workspace: dict
) -> None:
    """A group id from elsewhere must not be filable.

    Otherwise a caller with a stale id could name a folder in someone else's
    arrangement -- the same class of mistake as reading another workspace's
    documents, and refused the same way.
    """
    from app.services.placement import create_group

    group = create_group(session, workspace["id"], "Projecten")

    with pytest.raises(PlacementError):
        set_group_folder(session, workspace["id"] + 999, group.id, "Projecten")


def test_a_group_is_not_renamed_by_its_folder(
    client: TestClient, workspace: dict
) -> None:
    """The folder is the group's own name, set independently.

    Tying them would mean renaming a group silently moved its documents, and
    would make a Delphi group with a generated name into a generated directory.
    """
    base = _base(workspace)
    gid = client.post(base, json={"name": "Werk", "folder": "Notities"}).json()["id"]

    renamed = client.patch(f"{base}/{gid}", json={"name": "Bezig"})

    assert renamed.json()["name"] == "Bezig"
    assert renamed.json()["folder"] == "Notities"


def test_the_folder_survives_a_second_document(
    client: TestClient, workspace: dict, doc_repo: Path
) -> None:
    base = _base(workspace)
    gid = client.post(base, json={"name": "Notities", "folder": "Notities"}).json()["id"]
    repo = _doc_repo_id(workspace)

    for path in ("notes.md", "foundation/principles.md"):
        proposal_id = client.post(
            f"{base}/{gid}/documents", json={"repository_id": repo, "path": path}
        ).json()["proposal_id"]
        client.post(
            f"/api/workspaces/{workspace['id']}/proposals/{proposal_id}/accept"
        )

    assert (doc_repo / "Notities" / "notes.md").exists()
    assert (doc_repo / "Notities" / "principles.md").exists()
    assert sorted(
        d["path"] for d in client.get(f"{base}/{gid}/documents").json()
    ) == ["Notities/notes.md", "Notities/principles.md"]


def test_no_document_is_ever_removed_by_all_of_this(
    client: TestClient, workspace: dict, session: Session, doc_repo: Path
) -> None:
    """The product's hardest rule, checked across the whole phase.

    A move preserves bytes; it relocates them. So the multiset of contents under
    the repository must be identical before and after, whatever the reader
    accepted. Deleting a file would keep every individual assertion above passing
    while breaking the one thing that matters.
    """
    base = _base(workspace)
    repo = _doc_repo_id(workspace)
    contents_before = sorted(
        p.read_bytes() for p in doc_repo.rglob("*") if p.is_file() and ".git" not in p.parts
    )
    gid = client.post(base, json={"name": "Notities", "folder": "Notities"}).json()["id"]
    archive_id = get_or_create_archive(session, workspace["id"]).id

    for target, path in (
        (gid, "notes.md"),
        (gid, "foundation/principles.md"),
        (archive_id, "architecture/overview.md"),
    ):
        proposal_id = client.post(
            f"{base}/{target}/documents", json={"repository_id": repo, "path": path}
        ).json()["proposal_id"]
        if proposal_id is not None:
            client.post(
                f"/api/workspaces/{workspace['id']}/proposals/{proposal_id}/accept"
            )

    contents_after = sorted(
        p.read_bytes() for p in doc_repo.rglob("*") if p.is_file() and ".git" not in p.parts
    )
    assert contents_after == contents_before
