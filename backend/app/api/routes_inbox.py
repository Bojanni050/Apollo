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

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from sqlalchemy.orm import Session

from app.api.deps import get_or_create_storage_repo, get_storage_repository, get_workspace
from app.db import get_db
from app.schemas import (
    ImportedFileOut,
    InboxFileOut,
    InboxImportFolderOut,
    InboxImportFolderRequest,
    InboxOut,
    InboxUploadOut,
    RefusedFileOut,
)
from app.services import storage
from app.services.storage import (
    INBOX_DIR,
    StorageError,
    import_folder,
    list_inbox,
    store_upload,
)

router = APIRouter(prefix="/workspaces/{workspace_id}", tags=["inbox"])


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
            for entry in list_inbox(workspace_id)
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
        result = import_folder(workspace_id, payload.path)
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
        stored = store_upload(workspace_id, file.filename or "", data)
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
