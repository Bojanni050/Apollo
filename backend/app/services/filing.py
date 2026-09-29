"""The folder a group stands for, and the proposal that fills it.

This is where the arrangement and the disk finally meet, and the meeting is
deliberately indirect. A group may name a folder; putting a document into such
a group does **not** move it. It files a *proposal* saying where the document
would go, and the document travels only when a person accepts that proposal.

The rules, and the specific failure each one prevents:

* **No folder, no proposal.** A group without a folder is a view. Most groups
  are, and a grouping you are still thinking about should not rearrange files.
* **One pending proposal per document and target.** Placing the same document
  in the same group twice is a no-op, and a second drag while a proposal waits
  does not produce a second competing card proposing the same move.
* **A move re-points everything that named the old path.** Placement, signal,
  and search chunk each identify a document by its path. A move that updates
  the file but not those rows leaves all of them naming a path that no longer
  exists, which is indistinguishable from a document having gone missing.
  :func:`repoint_after_move` is the one place that knows the list.
"""
from __future__ import annotations

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.models import (
    ChangeProposal,
    DocSignal,
    DocumentChunk,
    Group,
    GroupPlacement,
    PulseItem,
    PulseRun,
    Repository,
)
from app.services.indexing import document_chunk_identifier
from app.services.placement import PlacementError, resolved_folder
from app.services.proposals import ProposalError, plan_move

#: The folder the archive stands for. One name, fixed, because "the archive" is
#: a single destination and a second folder for it would give "where did this
#: go" two answers.
ARCHIVE_FOLDER = "Archief"

#: Refused rather than cleaned. A name that needed cleaning was not the name the
#: reader typed, and writing a different directory than the one on screen is the
#: kind of thing that is only noticed much later.
_RESERVED = {"inbox"}


def validate_folder(folder: str) -> str:
    """Return a folder name that is safe to use as one directory level.

    One level, deliberately. A group folder is a name, not a path: allowing
    ``a/b`` would let a group name decide the shape of the tree two levels down,
    where nothing in the interface shows it.
    """
    clean = (folder or "").strip()
    if not clean:
        raise PlacementError("A folder needs a name.")
    if len(clean) > 200:
        raise PlacementError("A folder name may be at most 200 characters.")
    if "/" in clean or "\\" in clean:
        raise PlacementError(
            "A folder is a single name, not a path. Use a name like 'Notities'."
        )
    if clean in (".", ".."):
        raise PlacementError("That is not a folder name.")
    # Windows refuses these outright; catching them here gives a sentence
    # instead of an OSError from deep inside a move.
    if any(ch in clean for ch in '<>:"|?*'):
        raise PlacementError('A folder name may not contain any of: < > : " | ? *')
    if clean.lower() in _RESERVED:
        raise PlacementError(
            f"{clean!r} is where documents you drop in arrive. Choose another name."
        )
    return clean


def set_group_folder(
    db: Session, workspace_id: int, group_id: int, folder: str | None
) -> Group:
    """Name the folder a group's documents belong in, or take it away again.

    Setting a folder never moves anything, and neither does clearing it. The
    folder says where documents *belong*; the moves it implies are still
    proposals, one document at a time.
    """
    from app.services.placement import get_group  # local: avoids an import cycle

    group = get_group(db, workspace_id, group_id)
    if folder is None:
        group.folder = None
    else:
        clean = validate_folder(folder)
        if group.is_archive and clean != ARCHIVE_FOLDER:
            raise PlacementError(
                f"The archive always lives in {ARCHIVE_FOLDER!r} and cannot be "
                f"pointed somewhere else."
            )
        group.folder = clean
    db.commit()
    db.refresh(group)
    return group


def plan_filing(
    db: Session,
    workspace_id: int,
    group: Group,
    repository_id: int,
    file_path: str,
) -> ChangeProposal | None:
    """Propose that a document move into this group's folder. Or propose nothing.

    "This group's folder" is :func:`~app.services.placement.resolved_folder`,
    not the raw column: a topic under an area files into the area's folder
    plus its own (``Architectuur/Geheugenbeleid``), so the composition has to
    happen here too, not only where the board displays it.

    Returns None in every case where proposing would be noise or wrong: the group
    has no folder, the document already sits in it, or an identical proposal is
    still waiting. The caller reads None as "nothing to review", not as a
    failure -- the placement itself has already succeeded by then.
    """
    folder = resolved_folder(group)
    if not folder:
        return None

    source = (file_path or "").strip().replace("\\", "/")
    if not source:
        return None

    # Already filed in this folder: putting a document in the group it already
    # belongs to is the same as leaving it there.
    if source.startswith(f"{folder}/"):
        return None

    repo = db.scalar(
        select(Repository).where(
            Repository.id == repository_id, Repository.workspace_id == workspace_id
        )
    )
    if repo is None or not repo.writable:
        # A read-only repository cannot be moved into. Proposing nothing is
        # better than filing a proposal that fails at acceptance.
        return None

    # One pending proposal per (document, target), so a second drag cannot leave
    # the reader choosing between two cards that propose the same move.
    wanted_target = f"{folder}/{source.rsplit('/', 1)[-1]}"
    for proposal in db.scalars(
        select(ChangeProposal).where(
            ChangeProposal.workspace_id == workspace_id,
            ChangeProposal.status == "pending",
            ChangeProposal.kind == "move",
        )
    ).all():
        for change in proposal.changes or []:
            if (
                change.get("source_path") == source
                and change.get("target_path") == wanted_target
            ):
                return None

    from app.api.deps import resolve_repo_root  # local: avoids an import cycle

    try:
        change = plan_move(
            resolve_repo_root(repo),
            source,
            folder,
            # The folder is established by accepting, not before: a group nobody
            # has filed anything into has no directory yet, and that is correct.
            allow_missing_dir=True,
        )
    except ProposalError:
        # An unplaceable document (a vanished path, a traversal) gets no
        # proposal. The placement stands on its own; inventing a card here would
        # only hide the reason.
        return None

    proposal = ChangeProposal(
        workspace_id=workspace_id,
        kind="move",
        title=f"File into {group.name}: {source.rsplit('/', 1)[-1]}",
        reason=(
            f"You put this document in the group {group.name!r}, which lives in "
            f"the folder {folder!r}. Accepting moves the file there; "
            f"declining leaves it exactly where it is."
        ),
        changes=[
            {
                "action": change.action,
                "source_path": change.source_path,
                "target_path": change.target_path,
                "content": None,
                "repository_id": repository_id,
                # Recorded so acceptance knows to re-point the arrangement.
                # Without it the group would keep a card pointing at a path that
                # is gone, and the reader could not tell that from a lost file.
                "group_id": group.id,
            }
        ],
        expected_consequences=(
            f"'{change.source_path}' will no longer exist at its current path. "
            "The groups it belongs to follow it to the new path; any link to the "
            "old path will need updating."
        ),
        diff=change.diff,
        status="pending",
    )
    db.add(proposal)
    db.commit()
    db.refresh(proposal)
    return proposal


def repoint_after_move(
    db: Session,
    workspace_id: int,
    repository_id: int,
    old_path: str,
    new_path: str,
) -> dict[str, int]:
    """Follow a document to its new path, everywhere that path was written down.

    A document is identified by ``(repository_id, file_path)``, and that pair is
    stored in four places that survive a move. All four are corrected, and none
    is deleted: a signal keeps its claim, which is still true of the document,
    and a search chunk keeps its text, which the move did not change.

    Code chunks are deliberately absent. They belong to source repositories,
    which are never writable -- a move proposal cannot be filed against one --
    so there is no move that could strand them.
    """
    counts: dict[str, int] = {}
    if not old_path or old_path == new_path:
        return counts

    # The arrangement, in every group the document belongs to and not only the
    # one whose folder it moved into: a document may sit in several groups.
    res = db.execute(
        update(GroupPlacement)
        .where(
            GroupPlacement.workspace_id == workspace_id,
            GroupPlacement.repository_id == repository_id,
            GroupPlacement.file_path == old_path,
        )
        .values(file_path=new_path)
    )
    counts["placements"] = res.rowcount or 0

    # Signals: both the document a claim is about, and a claim that *refers* to
    # it. The second is easy to forget and leaves a dead link in the sidebar.
    res = db.execute(
        update(DocSignal)
        .where(
            DocSignal.workspace_id == workspace_id,
            DocSignal.repository_id == repository_id,
            DocSignal.file_path == old_path,
        )
        .values(file_path=new_path)
    )
    counts["signals"] = res.rowcount or 0
    res = db.execute(
        update(DocSignal)
        .where(
            DocSignal.workspace_id == workspace_id,
            DocSignal.repository_id == repository_id,
            DocSignal.reference == old_path,
        )
        .values(reference=new_path)
    )
    counts["references"] = res.rowcount or 0

    # The search index. Its key is a hash over repository, path, section and
    # content, so rewriting the path alone would leave a row whose own key no
    # longer describes it -- and the next indexing run would add a second row
    # beside it rather than update it. The key is therefore recomputed from the
    # same inputs, through the same function the indexer uses.
    moved_chunks = db.scalars(
        select(DocumentChunk).where(
            DocumentChunk.repository_id == repository_id,
            DocumentChunk.file_path == old_path,
        )
    ).all()
    for chunk in moved_chunks:
        chunk.file_path = new_path
        chunk.document_id = f"{repository_id}:{new_path}"
        chunk.identifier = document_chunk_identifier(
            repository_id, new_path, chunk.section, chunk.content
        )
    counts["document_chunks"] = len(moved_chunks)

    # Pulse findings name a document by its path, and are reached through their
    # run -- the item itself carries no workspace or repository.
    res = db.execute(
        update(PulseItem)
        .where(
            PulseItem.run_id.in_(
                select(PulseRun.id).where(PulseRun.workspace_id == workspace_id)
            ),
            PulseItem.file_path == old_path,
        )
        .values(file_path=new_path)
    )
    counts["pulse_items"] = res.rowcount or 0

    db.commit()
    return counts


__all__ = [
    "ARCHIVE_FOLDER",
    "plan_filing",
    "repoint_after_move",
    "set_group_folder",
    "validate_folder",
]
