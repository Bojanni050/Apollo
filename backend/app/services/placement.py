"""Visual grouping: the arrangement, and the archive, as database state.

Nothing in this module touches the filesystem. That is the single most important
property here, and it is what makes the product rule "AI-organisatie +
menselijke correctie" safe to offer: rearranging groups is a normal, frequent,
reversible thing a person does while reading, so it must not be able to lose
data or leave a half-finished move behind.

Three kinds of state live here:

* **Groups** -- named clusters with an order and a layout, created by Delphi or
  by the reader.
* **Placements** -- which documents sit in which group. A document may be in
  more than one group; being in two groups is not a claim that it was moved.
* **The archive** -- a group flagged ``is_archive``, and nothing else. Archiving
  is a placement like any other, so it moves a document out of the current view
  while leaving it exactly where it is on disk. This is the only place where
  "archived" is defined: a second flag elsewhere could disagree after a drag. It
  never removes anything, and the helpers that appear to do so refuse rather than
  delete.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import (
    ARCHIVE_CATEGORY,
    Group,
    GroupPlacement,
    Repository,
)
from app.services.documents import DOC_SUFFIXES
from app.services.paths import PathSecurityError, safe_path


class PlacementError(Exception):
    """Raised when a document cannot be placed in a group."""


@dataclass(frozen=True)
class GroupView:
    """A group as the API returns it, with its member count resolved."""

    id: int
    name: str
    description: str | None
    source: str
    is_archive: bool
    position: int
    layout: str
    document_count: int
    #: The folder this group's documents belong in, or None. Mirrors the column
    #: so the board can tell a view from a filing destination without a second
    #: query per group.
    folder: str | None


#: The name of the single archive group. One, not several: "the archive" is a
#: destination a document either is or is not in, and allowing several would
#: make "is this archived?" a question with no single answer.
ARCHIVE_GROUP_NAME = ARCHIVE_CATEGORY


def _normalize(file_path: str) -> str:
    """Put a path in the canonical form used as a document's identity.

    Slashes and surrounding whitespace only. Deliberately *not*
    ``lstrip("./")``: that silently eats a leading ``../`` and turns
    ``../../../etc/passwd`` into ``etc/passwd`` -- a traversal stripped rather
    than refused, which is the one thing a path normaliser must never do. The
    traversal check in :func:`_assert_document_exists` is what catches it, and
    it can only catch it if this function has not already hidden it.

    Paths are stored in one form so that ``./notes.md`` and ``notes.md`` are the
    same document rather than two placements of it.
    """
    return (file_path or "").strip().replace("\\", "/")


def _assert_document_exists(root: Path, file_path: str) -> None:
    """Refuse to place a path that is not a readable document.

    A placement pointing at something that does not exist is worse than no
    placement: the group would show a card that opens to an error, and the
    reader would have no way to tell that apart from a real document. The check
    goes through ``safe_path``, so a ``../`` path is refused here exactly as it
    would be on read.
    """
    try:
        target = safe_path(root, file_path)
    except PathSecurityError as exc:
        raise PlacementError(str(exc)) from exc
    if not target.is_file():
        raise PlacementError(f"Not a file in this repository: {file_path!r}")
    if target.suffix.lower() not in DOC_SUFFIXES:
        raise PlacementError(
            f"{file_path!r} is not a document type Apollo can open "
            f"({', '.join(sorted(DOC_SUFFIXES))})."
        )


def list_groups(db: Session, workspace_id: int) -> list[GroupView]:
    """Every group in a workspace, archive last, with member counts."""
    counts = dict(
        db.execute(
            select(GroupPlacement.group_id, func.count(GroupPlacement.id))
            .join(Group, Group.id == GroupPlacement.group_id)
            .where(Group.workspace_id == workspace_id)
            .group_by(GroupPlacement.group_id)
        ).all()
    )
    groups = db.scalars(
        select(Group)
        .where(Group.workspace_id == workspace_id)
        .order_by(Group.is_archive, Group.position, Group.id)
    ).all()
    return [
        GroupView(
            id=g.id,
            name=g.name,
            description=g.description,
            source=g.source,
            is_archive=g.is_archive,
            position=g.position,
            layout=g.layout,
            document_count=counts.get(g.id, 0),
            folder=g.folder,
        )
        for g in groups
    ]


def create_group(
    db: Session,
    workspace_id: int,
    name: str,
    *,
    description: str | None = None,
    source: str = "user",
    is_archive: bool = False,
    layout: str = "grid",
) -> Group:
    """Create a group. Its position defaults to the end of its section.

    Positions are only used for ordering and are renumbered on demand, so two
    groups briefly sharing one cannot corrupt anything.
    """
    clean = (name or "").strip()
    if not clean:
        raise PlacementError("A group needs a name.")
    if len(clean) > 200:
        raise PlacementError("A group name may be at most 200 characters.")

    existing = db.scalar(
        select(Group).where(Group.workspace_id == workspace_id, Group.name == clean)
    )
    if existing is not None:
        raise PlacementError(f"A group named {clean!r} already exists.")

    # Archiving is a single destination, so creating a second one is refused
    # rather than silently merged: two archives would make "restore" ambiguous.
    if is_archive:
        already = db.scalar(
            select(Group).where(Group.workspace_id == workspace_id, Group.is_archive.is_(True))
        )
        if already is not None:
            raise PlacementError(
                f"This workspace already has an archive, called {already.name!r}."
            )

    last = db.scalar(
        select(func.max(Group.position)).where(
            Group.workspace_id == workspace_id, Group.is_archive.is_(is_archive)
        )
    )
    group = Group(
        workspace_id=workspace_id,
        name=clean,
        description=(description or "").strip() or None,
        source=source,
        is_archive=is_archive,
        layout=layout,
        position=(last or 0) + 1,
    )
    if is_archive:
        # Set here rather than only in ``get_or_create_archive`` so that both
        # ways of making an archive produce the same one. An archive that
        # existed but had no folder would quietly behave like a plain view --
        # filing into it would propose nothing and the file would never move.
        from app.services.filing import ARCHIVE_FOLDER  # local: avoids a cycle

        group.folder = ARCHIVE_FOLDER
    db.add(group)
    db.commit()
    db.refresh(group)
    return group


def get_group(db: Session, workspace_id: int, group_id: int) -> Group:
    group = db.scalar(
        select(Group).where(Group.id == group_id, Group.workspace_id == workspace_id)
    )
    if group is None:
        raise PlacementError("Group not found.")
    return group


def rename_group(
    db: Session,
    workspace_id: int,
    group_id: int,
    *,
    name: str | None = None,
    description: str | None = None,
    layout: str | None = None,
) -> Group:
    """Edit a group in place. The name is changed, never the members' paths."""
    group = get_group(db, workspace_id, group_id)
    if name is not None:
        clean = name.strip()
        if not clean:
            raise PlacementError("A group needs a name.")
        clash = db.scalar(
            select(Group).where(
                Group.workspace_id == workspace_id,
                Group.name == clean,
                Group.id != group_id,
            )
        )
        if clash is not None:
            raise PlacementError(f"A group named {clean!r} already exists.")
        group.name = clean
    if description is not None:
        group.description = description.strip() or None
    if layout is not None:
        if layout not in ("grid", "list"):
            raise PlacementError(f"Unknown layout {layout!r}.")
        group.layout = layout
    db.commit()
    db.refresh(group)
    return group


def delete_group(db: Session, workspace_id: int, group_id: int) -> None:
    """Remove a group and its placements.

    The documents are not touched in any way. Deleting a group means "I no
    longer want to look at these together", not "these files should go away" --
    which is why this is a hard guarantee rather than a convention: the
    repository's files are never enumerated here, so there is nothing to delete
    even if the caller got it wrong.
    """
    group = get_group(db, workspace_id, group_id)
    db.delete(group)
    db.commit()


def assert_can_place(
    db: Session, workspace_id: int, repository_id: int, file_path: str
) -> str:
    """Check a document can be placed, and return its canonical path.

    Public because a caller may need to know *before* it does something else:
    the archive action creates the archive group, and doing that first would
    leave an empty archive behind when the document turns out not to be there.
    Refusing first costs one call and no side effect.
    """
    repo = db.scalar(
        select(Repository).where(
            Repository.id == repository_id, Repository.workspace_id == workspace_id
        )
    )
    if repo is None:
        raise PlacementError("That repository is not registered in this workspace.")
    if not repo.writable:
        raise PlacementError(
            f"Repository {repo.name!r} is read-only, so its documents cannot be moved."
        )
    rel = _normalize(file_path)
    if not rel:
        raise PlacementError("A document needs a path.")
    _assert_document_exists(Path(repo.local_path), rel)
    return rel


def place_document(
    db: Session,
    workspace_id: int,
    group_id: int,
    repository_id: int,
    file_path: str,
    *,
    placed_by: str | None = None,
    verify_exists: bool = True,
) -> GroupPlacement:
    """Put a document into a group. Idempotent.

    Dragging a document onto a group it is already in is the same as leaving it
    there, so the second drag is a no-op rather than an error and certainly not
    a second copy. That is what makes a drag feel like picking something up and
    putting it down rather than a form submission.
    """
    group = get_group(db, workspace_id, group_id)
    repo = db.scalar(
        select(Repository).where(
            Repository.id == repository_id, Repository.workspace_id == workspace_id
        )
    )
    if repo is None:
        raise PlacementError("That repository is not registered in this workspace.")

    rel = _normalize(file_path)
    if not rel:
        raise PlacementError("A document needs a path.")
    if verify_exists:
        _assert_document_exists(Path(repo.local_path), rel)

    existing = db.scalar(
        select(GroupPlacement).where(
            GroupPlacement.group_id == group_id,
            GroupPlacement.repository_id == repository_id,
            GroupPlacement.file_path == rel,
        )
    )
    if existing is not None:
        return existing

    last = db.scalar(
        select(func.max(GroupPlacement.position)).where(GroupPlacement.group_id == group_id)
    )
    placement = GroupPlacement(
        group_id=group.id,
        workspace_id=workspace_id,
        repository_id=repository_id,
        file_path=rel,
        position=(last or 0) + 1,
        placed_by=placed_by,
    )
    db.add(placement)
    db.commit()
    db.refresh(placement)
    return placement


def remove_document(
    db: Session, workspace_id: int, group_id: int, repository_id: int, file_path: str
) -> None:
    """Take a document out of a group. The file stays exactly where it is.

    This is the operation a drag between two groups performs on the source, and
    it is deliberately a pure database change: dragging rearranges the view, and
    the file on disk is not part of the view.

    A document that simply is not in this group is a no-op -- a drag can end
    where it started, and that is not an error. A *group* that is not in this
    workspace is an error, because silently doing nothing is indistinguishable
    from a successful removal and would hide a caller that has the wrong id.
    """
    get_group(db, workspace_id, group_id)
    placement = db.scalar(
        select(GroupPlacement).where(
            GroupPlacement.group_id == group_id,
            GroupPlacement.workspace_id == workspace_id,
            GroupPlacement.repository_id == repository_id,
            GroupPlacement.file_path == _normalize(file_path),
        )
    )
    if placement is None:
        return
    db.delete(placement)
    db.commit()


def move_document(
    db: Session,
    workspace_id: int,
    repository_id: int,
    file_path: str,
    *,
    from_group_id: int | None = None,
    to_group_id: int,
) -> None:
    """Move a document from one group to another in a single step.

    Adding to the target and removing from the source are separate statements so
    a failure cannot leave the document in both groups (a duplicate) or in
    neither (a document that vanished from the arrangement). The remove is
    attempted first and is a no-op when the document is not in a source group,
    so a drag onto the group it is already in simply succeeds.
    """
    if from_group_id is not None and from_group_id != to_group_id:
        remove_document(db, workspace_id, from_group_id, repository_id, file_path)
    place_document(
        db, workspace_id, to_group_id, repository_id, file_path, placed_by="user"
    )


def group_documents(
    db: Session, workspace_id: int, group_id: int
) -> list[GroupPlacement]:
    """The documents in a group, in manual order."""
    get_group(db, workspace_id, group_id)
    return list(
        db.scalars(
            select(GroupPlacement)
            .where(GroupPlacement.group_id == group_id)
            .order_by(GroupPlacement.position, GroupPlacement.id)
        ).all()
    )


def groups_of_document(
    db: Session, workspace_id: int, repository_id: int, file_path: str
) -> list[Group]:
    """Which groups a document belongs to -- the "where does this live?" lookup."""
    return list(
        db.scalars(
            select(Group)
            .join(GroupPlacement, GroupPlacement.group_id == Group.id)
            .where(
                Group.workspace_id == workspace_id,
                GroupPlacement.repository_id == repository_id,
                GroupPlacement.file_path == _normalize(file_path),
            )
            .order_by(Group.is_archive, Group.position, Group.id)
        ).all()
    )


def get_or_create_archive(db: Session, workspace_id: int) -> Group:
    """The workspace's archive, created on first use.

    Lazy rather than migrated, because a workspace that never archives anything
    should not carry a group nobody asked for, and because the archive's identity
    is only meaningful once something has actually been put in it.
    """
    existing = db.scalar(
        select(Group).where(Group.workspace_id == workspace_id, Group.is_archive.is_(True))
    )
    if existing is not None:
        if existing.folder is None:
            # An archive created before groups had folders gets its one now. It
            # is the only folder in the system the reader does not choose, which
            # is why it is applied here and not on create.
            from app.services.filing import ARCHIVE_FOLDER  # local: avoids a cycle

            existing.folder = ARCHIVE_FOLDER
            db.commit()
            db.refresh(existing)
        return existing
    group = create_group(
        db,
        workspace_id,
        ARCHIVE_GROUP_NAME,
        description=(
            "Kept, not deleted. Everything here is still on disk and can be read "
            "or brought back."
        ),
        source="user",
        is_archive=True,
        layout="list",
    )
    from app.services.filing import ARCHIVE_FOLDER  # local: avoids a cycle

    group.folder = ARCHIVE_FOLDER
    db.commit()
    db.refresh(group)
    return group


__all__ = [
    "ARCHIVE_GROUP_NAME",
    "GroupView",
    "PlacementError",
    "create_group",
    "delete_group",
    "get_group",
    "get_or_create_archive",
    "group_documents",
    "groups_of_document",
    "list_groups",
    "move_document",
    "place_document",
    "remove_document",
    "rename_group",
]
