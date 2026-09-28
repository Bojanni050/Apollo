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
    GroupMoveRequest,
    GroupOut,
    GroupPlacementRequest,
    GroupUpdate,
)
from app.services.placement import (
    PlacementError,
    create_group,
    delete_group,
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
    """Put a document into a group. This is the drop side of a drag."""
    get_workspace(db, workspace_id)
    try:
        placement = place_document(
            db,
            workspace_id,
            group_id,
            payload.repository_id,
            payload.path,
            placed_by=payload.placed_by,
        )
    except PlacementError as exc:
        raise _fail(exc) from exc
    return GroupDocumentOut(
        repository_id=placement.repository_id,
        path=placement.file_path,
        position=placement.position,
        placed_by=placement.placed_by,
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


@router.post("/groups/move", status_code=status.HTTP_204_NO_CONTENT)
def move(
    workspace_id: int, payload: GroupMoveRequest, db: Session = Depends(get_db)
) -> None:
    """Move a document from one group to another.

    One request rather than a remove followed by an add, so a failure cannot
    leave the document in both groups or in neither. The file on disk is not
    touched: this rearranges the view and nothing else.
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
    except PlacementError as exc:
        raise _fail(exc) from exc


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
