"""Visual groups: the arrangement, and the archive.

The endpoints behind the drag-and-drop surface. Two rules hold throughout:

* **Nothing here writes a file.** There is no file-writing call in this module
  and no code path that reaches one. A group is a view; the documents stay where
  they are; the only way a file moves is the proposal flow, which is a separate
  route with its own approval.
* **Archiving is a move, not a deletion.** The archive is a group, and putting
  a document in it is a placement like any other. There is no delete-document
  endpoint, by design rather than by omission.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.api.deps import get_workspace
from app.db import get_db
from app.schemas import (
    GroupCreate,
    GroupDocumentOut,
    GroupMoveOut,
    GroupMoveRequest,
    GroupOut,
    GroupPlacementRequest,
    GroupUpdate,
)
from app.services.filing import plan_filing, set_group_folder
from app.services.placement import (
    PlacementError,
    assert_can_place,
    create_group,
    delete_group,
    get_group,
    get_or_create_archive,
    group_documents,
    groups_of_document,
    list_groups,
    move_document,
    place_document,
    remove_document,
    rename_group,
)

router = APIRouter(prefix="/workspaces/{workspace_id}", tags=["groups"])


def _fail(exc: PlacementError) -> HTTPException:
    """A placement problem is the caller's to fix, so 400 with the reason.

    The reason is passed through verbatim because every one of them is written
    for the reader: "A group named 'Gaia' already exists" is something to act on,
    where "invalid request" is not.
    """
    return HTTPException(status.HTTP_400_BAD_REQUEST, str(exc))


def _group_out(group, document_count: int) -> GroupOut:
    return GroupOut(
        id=group.id,
        name=group.name,
        description=group.description,
        source=group.source,
        is_archive=group.is_archive,
        position=group.position,
        layout=group.layout,
        document_count=document_count,
        folder=group.folder,
    )


@router.get("/groups", response_model=list[GroupOut])
def list_all_groups(workspace_id: int, db: Session = Depends(get_db)) -> list[GroupOut]:
    """Every group, archive last, with each one's document count."""
    get_workspace(db, workspace_id)
    return [_group_out(v, v.document_count) for v in list_groups(db, workspace_id)]


@router.post("/groups", response_model=GroupOut, status_code=status.HTTP_201_CREATED)
def create(
    workspace_id: int, payload: GroupCreate, db: Session = Depends(get_db)
) -> GroupOut:
    """Create an empty group.

    It starts empty on purpose. A group Delphi proposes is created from what it
    actually found; a group the reader makes is created empty and filled by
    dragging. Neither is pre-populated from a guess.
    """
    get_workspace(db, workspace_id)
    try:
        group = create_group(
            db,
            workspace_id,
            payload.name,
            description=payload.description,
            source=payload.source,
            is_archive=payload.is_archive,
            layout=payload.layout,
        )
        if payload.folder is not None:
            # Through the one function that validates it, so a group created with
            # a folder is checked exactly like one given one later.
            group = set_group_folder(db, workspace_id, group.id, payload.folder)
    except PlacementError as exc:
        raise _fail(exc) from exc
    return _group_out(group, 0)


@router.get("/groups/{group_id}/documents", response_model=list[GroupDocumentOut])
def documents_in(
    workspace_id: int, group_id: int, db: Session = Depends(get_db)
) -> list[GroupDocumentOut]:
    """The documents in a group, in manual order."""
    get_workspace(db, workspace_id)
    try:
        placements = group_documents(db, workspace_id, group_id)
    except PlacementError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    return [
        GroupDocumentOut(
            repository_id=p.repository_id,
            path=p.file_path,
            position=p.position,
            placed_by=p.placed_by,
        )
        for p in placements
    ]


@router.patch("/groups/{group_id}", response_model=GroupOut)
def update(
    workspace_id: int,
    group_id: int,
    payload: GroupUpdate,
    db: Session = Depends(get_db),
) -> GroupOut:
    """Rename, re-describe or re-lay-out a group. Its documents do not move."""
    get_workspace(db, workspace_id)
    try:
        group = rename_group(
            db,
            workspace_id,
            group_id,
            name=payload.name,
            description=payload.description,
            layout=payload.layout,
        )
        # Only when the request actually mentioned the field. Omitting it means
        # "I am not touching the folder"; sending null means "remove it". Reading
        # the attribute would collapse those two, and a rename would silently
        # undo a folder the reader had set up.
        if "folder" in payload.model_fields_set:
            group = set_group_folder(db, workspace_id, group_id, payload.folder)
    except PlacementError as exc:
        raise _fail(exc) from exc
    return _group_out(group, len(group_documents(db, workspace_id, group_id)))


@router.delete("/groups/{group_id}", status_code=status.HTTP_204_NO_CONTENT)
def remove(workspace_id: int, group_id: int, db: Session = Depends(get_db)) -> None:
    """Remove a group. Every document it held is left completely untouched.

    Deleting a group means "I do not want to look at these together". It is not a
    way to delete documents, and there is no parameter that makes it one.
    """
    get_workspace(db, workspace_id)
    try:
        delete_group(db, workspace_id, group_id)
    except PlacementError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc


@router.post(
    "/groups/{group_id}/documents",
    response_model=GroupDocumentOut,
    status_code=status.HTTP_201_CREATED,
)
def add_document(
    workspace_id: int,
    group_id: int,
    payload: GroupPlacementRequest,
    db: Session = Depends(get_db),
) -> GroupDocumentOut:
    """Put a document into a group. This is the drop side of a drag.

    Two things can happen, and only one of them touches a file. The placement
    always happens. Whether the document *travels* depends on the group: a group
    with a folder proposes a move, and a group without one is a view. The
    proposal is returned so the interface can say so immediately.
    """
    get_workspace(db, workspace_id)
    try:
        placement = place_document(
            db,
            workspace_id,
            group_id,
            payload.repository_id,
            payload.path,
            # Always the reader's: this endpoint is the board being used, and a
            # placement recorded as provisional could be undone by the next
            # analysis -- which would make correcting Delphi unsafe.
            placed_by="user",
        )
        # Read the group back rather than trusting the payload: the folder is
        # what decides whether anything is proposed at all.
        group = get_group(db, workspace_id, group_id)
        proposal = plan_filing(
            db, workspace_id, group, payload.repository_id, payload.path
        )
    except PlacementError as exc:
        raise _fail(exc) from exc
    return GroupDocumentOut(
        repository_id=placement.repository_id,
        path=placement.file_path,
        position=placement.position,
        placed_by=placement.placed_by,
        proposal_id=proposal.id if proposal is not None else None,
    )


@router.post(
    "/groups/archive",
    response_model=GroupDocumentOut,
    status_code=status.HTTP_201_CREATED,
)
def archive(
    workspace_id: int, payload: GroupPlacementRequest, db: Session = Depends(get_db)
) -> GroupDocumentOut:
    """Put a document in the archive, creating the archive if there is none.

    An action rather than a place you have to find: archiving is something a
    reader does often and by name, so it gets its own verb instead of a drag
    onto a card that may not exist yet. It does exactly what the drag would --
    the archive is a group like any other -- and says which.

    The file does not move on its own. The archive stands for a folder, so this
    files the same proposal a drag into it would, and the response carries the
    id. That is what keeps "archive" from becoming a quiet way around the rule
    that nothing is ever deleted: the document is still readable at its old path
    until somebody accepts.

    The archive is created only once the document is known to be archivable, so
    a refused request leaves no empty archive behind.
    """
    get_workspace(db, workspace_id)
    try:
        # Check the document before reaching for the archive. The archive is
        # created on first use, and creating it for a document that turns out not
        # to be there would leave an empty archive behind -- a side effect of a
        # request that was refused.
        assert_can_place(db, workspace_id, payload.repository_id, payload.path)
        archive_group = get_or_create_archive(db, workspace_id)
        placement = place_document(
            db,
            workspace_id,
            archive_group.id,
            payload.repository_id,
            payload.path,
            # The reader's, same as every placement made through this surface.
            placed_by="user",
        )
        proposal = plan_filing(
            db, workspace_id, archive_group, payload.repository_id, payload.path
        )
    except PlacementError as exc:
        raise _fail(exc) from exc
    return GroupDocumentOut(
        repository_id=placement.repository_id,
        path=placement.file_path,
        position=placement.position,
        placed_by=placement.placed_by,
        proposal_id=proposal.id if proposal is not None else None,
    )


@router.delete("/groups/{group_id}/documents", status_code=status.HTTP_204_NO_CONTENT)
def take_document_out(
    workspace_id: int,
    group_id: int,
    repository_id: int,
    path: str,
    db: Session = Depends(get_db),
) -> None:
    """Take a document out of a group. The file stays where it is."""
    get_workspace(db, workspace_id)
    try:
        remove_document(db, workspace_id, group_id, repository_id, path)
    except PlacementError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc


@router.post("/groups/move", response_model=GroupMoveOut)
def move(
    workspace_id: int, payload: GroupMoveRequest, db: Session = Depends(get_db)
) -> GroupMoveOut:
    """Move a document from one group to another.

    One request rather than a remove followed by an add, so a failure cannot
    leave the document in both groups or in neither.

    The arrangement is always changed. Whether a *file* moves depends on the
    target: a group with a folder proposes it, and the id comes back here so the
    board can say so at once rather than leaving the reader to notice a card on
    another screen.
    """
    get_workspace(db, workspace_id)
    try:
        move_document(
            db,
            workspace_id,
            payload.repository_id,
            payload.path,
            from_group_id=payload.from_group_id,
            to_group_id=payload.to_group_id,
        )
        group = get_group(db, workspace_id, payload.to_group_id)
        proposal = plan_filing(
            db, workspace_id, group, payload.repository_id, payload.path
        )
    except PlacementError as exc:
        raise _fail(exc) from exc
    return GroupMoveOut(proposal_id=proposal.id if proposal is not None else None)


@router.get("/documents/groups", response_model=list[GroupOut])
def groups_containing(
    workspace_id: int,
    repository_id: int,
    path: str,
    db: Session = Depends(get_db),
) -> list[GroupOut]:
    """Which groups a document belongs to.

    This is what the sidebar shows when a document is open: "this is where it
    sits", which is the question a reader has when deciding whether the
    arrangement Delphi proposed is right.
    """
    get_workspace(db, workspace_id)
    groups = groups_of_document(db, workspace_id, repository_id, path)
    counts = {v.id: v.document_count for v in list_groups(db, workspace_id)}
    return [
        _group_out(g, counts.get(g.id, 0))
        for g in groups
    ]
