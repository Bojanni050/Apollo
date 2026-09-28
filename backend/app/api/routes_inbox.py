"""The inbox: the way documents get in.

Two endpoints, and the asymmetry between them is deliberate. Uploading creates
the workspace's storage repository on first use, because storing a file is a
decision to keep it. *Listing* never creates anything, because opening a
workspace is not a decision about its documents.

Nothing here deletes anything and no route could: the only write is a file
appearing in a folder Apollo owns, and the only other change is a row recording
that the folder exists.
"""
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from sqlalchemy.orm import Session

from app.api.deps import (
    get_or_create_storage_repo,
    get_storage_repository,
    get_workspace,
    workspace_working_dir,
)
from app.db import get_db
from app.schemas import (
    ImportedFileOut,
    InboxFileOut,
    InboxImportFolderOut,
    InboxImportFolderRequest,
    InboxOut,
    InboxUploadOut,
    RefusedFileOut,
    WorkingDirIn,
    WorkingDirOut,
)
from app.services import storage
from app.services.storage import (
    INBOX_DIR,
    StorageError,
    ensure_working_repo,
    import_folder,
    list_inbox,
    store_upload,
    workspace_storage,
)

router = APIRouter(prefix="/workspaces/{workspace_id}", tags=["inbox"])


@router.get("/working-dir", response_model=WorkingDirOut)
def read_working_dir(workspace_id: int, db: Session = Depends(get_db)) -> WorkingDirOut:
    """Where this workspace keeps its own documents.

    Read rather than assumed, because the answer differs per workspace and the
    interface has to be able to say which folder is in play before anything is
    dropped in it.
    """
    workspace = get_workspace(db, workspace_id)
    return _working_dir_out(workspace)


@router.put("/working-dir", response_model=WorkingDirOut)
def set_working_dir(
    workspace_id: int,
    payload: WorkingDirIn,
    db: Session = Depends(get_db),
) -> WorkingDirOut:
    """Choose the folder to work in.

    Refuses what cannot work rather than what is merely unwise: a path that is not
    a directory, and a folder inside Apollo's own storage. A non-empty folder is
    allowed and reported -- the reader may well have a folder they already keep
    their documents in, and refusing it would be Apollo deciding that their own
    folders are not allowed.

    The storage repository row is moved to the new folder rather than left behind,
    because a row pointing at a folder this workspace no longer uses is a claim
    about documents that are not there any more. The documents themselves are
    never moved, renamed or deleted by this call: an existing inbox stays exactly
    where it is, and the new folder simply starts empty.
    """
    workspace = get_workspace(db, workspace_id)
    raw = payload.path.strip()
    if not raw:
        # Back to the application's own folder, the state every workspace was in
        # before this endpoint existed. The storage row follows, and that is the
        # part that matters: it is what every read of a stored document goes
        # through, so leaving it on the folder the reader just gave up would have
        # new documents written to one place and looked for in another -- the
        # documents would exist and be invisible.
        workspace.working_dir = None
        db.commit()
        db.refresh(workspace)
        repo = get_storage_repository(db, workspace_id)
        if repo is not None:
            repo.local_path = str(workspace_storage(workspace.id))
            db.commit()
            db.refresh(repo)
        return _working_dir_out(workspace)

    target = Path(raw).expanduser()
    target = (target if target.is_absolute() else Path.cwd() / target).resolve()
    # Before the existence check, because it is a statement about the choice
    # rather than about the filesystem: pointing at Apollo's own storage is
    # refused whether or not that folder happens to exist yet, and "it does not
    # exist" would be a confusing answer to a question that was not asked.
    if storage._is_within(target, storage.storage_root()):
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "That is inside Apollo's own storage. Choose a folder of your own; "
            "Apollo's storage is where a workspace falls back to when you have not "
            "chosen one.",
        )
    if not target.exists():
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, f"That folder does not exist: {raw}"
        )
    if not target.is_dir():
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"{raw} is a file, not a folder.")

    # Recorded resolved, so the folder cannot change meaning when the application
    # is started from another directory tomorrow.
    workspace.working_dir = str(target)
    db.commit()
    db.refresh(workspace)

    repo = get_storage_repository(db, workspace_id)
    if repo is not None:
        repo.local_path = str(target)
        db.commit()
        db.refresh(repo)

    ensure_working_repo(target)
    return _working_dir_out(workspace)


def _working_dir_out(workspace) -> WorkingDirOut:
    """Describe the workspace's folder, warning when it is not empty."""
    from app.services.storage import INBOX_DIR

    chosen = workspace.working_dir
    if not chosen:
        fallback = storage.workspace_storage(workspace.id)
        return WorkingDirOut(
            working_dir=None,
            empty=True,
            entries=0,
            warning=(
                "Apollo is using its own folder. Choose one of yours and everything "
                "lands where you can see it in a file manager."
            ),
            inbox_dir=str(fallback / INBOX_DIR),
        )

    target = Path(chosen)
    # Git's own directory is Apollo's bookkeeping, not the reader's content, and
    # counting it would make every folder Apollo has ever prepared look occupied --
    # including the one they just emptied on purpose.
    entries = (
        len([p for p in target.iterdir() if p.name != ".git"]) if target.is_dir() else 0
    )
    if not target.is_dir():
        # Said rather than glossed over. A folder the reader deleted on purpose
        # comes back on the next drop, because that is where their documents are
        # kept -- surprising enough that the interface owes them the heads-up
        # rather than letting the folder reappear unexplained.
        return WorkingDirOut(
            working_dir=chosen,
            empty=False,
            entries=0,
            warning=(
                "That folder is gone. Apollo will create it again the next time "
                "you drop a document in it, or you can choose another one."
            ),
            inbox_dir=str(target / INBOX_DIR),
        )
    return WorkingDirOut(
        working_dir=chosen,
        empty=entries == 0,
        entries=entries,
        warning=(
            None
            if entries == 0
            else f"That folder already holds {entries} item(s). Apollo will add its "
            "own folders beside them and never touch what is already there."
        ),
        inbox_dir=str(target / INBOX_DIR),
    )


@router.get("/inbox", response_model=InboxOut)
def read_inbox(workspace_id: int, db: Session = Depends(get_db)) -> InboxOut:
    """The documents waiting in the inbox.

    Read from disk on every call rather than from the database, because the
    folder is the truth: a file the reader tidied away with their own file
    manager must disappear from this list, not be reported as still there.

    Works before the first upload. ``repository_id`` is null then, which tells
    the interface there is nothing to open yet instead of making it ask a
    question with no answer.
    """
    get_workspace(db, workspace_id)
    repo = get_storage_repository(db, workspace_id)
    return InboxOut(
        repository_id=repo.id if repo is not None else None,
        directory=INBOX_DIR,
        files=[
            InboxFileOut(path=entry.path, name=entry.name, size=entry.size)
            for entry in list_inbox(workspace_id, workspace_working_dir(db, workspace_id))
        ],
    )


@router.post("/inbox/import-folder", response_model=InboxImportFolderOut, status_code=status.HTTP_201_CREATED)
def import_inbox_folder(
    workspace_id: int,
    payload: InboxImportFolderRequest,
    db: Session = Depends(get_db),
) -> InboxImportFolderOut:
    """Copy the documents out of a folder the reader already has.

    The folder is read and nothing else: it is not moved, not renamed and not
    emptied, so the reader's own project is exactly as it was afterwards. What
    lands here is a *copy* under the inbox, with the folder's structure kept.

    The repository row is written after the bytes are on disk, for the same reason
    the upload endpoint does it: the other order leaves a registered repository
    pointing at a folder the import never filled.

    Named ``import_inbox_folder`` rather than ``import_folder`` on purpose: the
    service is imported under that name in this module, and a route function of
    the same name shadows it -- the call below would then recurse into this
    function with the request's own defaults and fail on the dependency object.
    """
    get_workspace(db, workspace_id)
    try:
        result = import_folder(
            workspace_id, payload.path, workspace_working_dir(db, workspace_id)
        )
    except StorageError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc

    repo = get_or_create_storage_repo(db, workspace_id)
    return InboxImportFolderOut(
        repository_id=repo.id,
        folder_name=result.folder_name,
        found=result.found,
        copied=[
            ImportedFileOut(
                source_path=f.source_path,
                path=f.path,
                name=f.name,
                size=f.size,
                readable=f.readable,
                unreadable_reason=f.unreadable_reason,
            )
            for f in result.copied
        ],
        refused=[
            RefusedFileOut(source_path=f.source_path, reason=f.reason)
            for f in result.refused
        ],
        truncated=result.truncated,
    )


@router.post(
    "/inbox/upload",
    response_model=InboxUploadOut,
    status_code=status.HTTP_201_CREATED,
)
async def upload_document(
    workspace_id: int,
    file: UploadFile = File(..., description="The document to store"),
    db: Session = Depends(get_db),
) -> InboxUploadOut:
    """Store one dropped-in document.

    The body is read with one byte more than the ceiling, so a file that is too
    large is refused *before* the rest of it is buffered. Buffering a gigabyte to
    then answer "too large" would cost the reader the wait and tell them nothing.

    The repository row is written after the bytes are safely on disk, never
    before: the other order would leave a registered repository pointing at an
    empty folder whenever a write failed.
    """
    get_workspace(db, workspace_id)
    # Read with the ceiling resolved at call time rather than imported by value,
    # so the limit has exactly one home (services/storage.py) and cannot drift
    # from the one the service itself enforces.
    data = await file.read(storage.MAX_UPLOAD_BYTES + 1)
    try:
        stored = store_upload(
            workspace_id,
            file.filename or "",
            data,
            workspace_working_dir(db, workspace_id),
        )
    except StorageError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc

    repo = get_or_create_storage_repo(db, workspace_id)
    return InboxUploadOut(
        repository_id=repo.id,
        path=stored.path,
        name=stored.name,
        size=stored.size,
        readable=stored.readable,
        unreadable_reason=stored.unreadable_reason,
    )
