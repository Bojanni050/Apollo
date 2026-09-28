"""Document explorer endpoints (read-only).

Content always comes from the documentation repository on disk. Document
mutation (move/rename/edit) is intentionally absent from this milestone: it
belongs to the approval-gated proposal flow, not to a direct write endpoint.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.api.deps import get_repository, get_workspace, resolve_repo_root
from app.db import get_db
from app.schemas import (
    DocumentLinkOut,
    DocumentLinksOut,
    DocumentOut,
    DocumentTreeOut,
    ExternalReferenceOut,
    GitChangesOut,
    GitStatusEntryOut,
    SearchHitOut,
    SearchOut,
)
from app.services import git
from app.services.documents import DocumentError, build_tree, read_document
from app.services.links import collect_links
from app.services.paths import PathSecurityError, safe_path
from app.services.search import search_documents

router = APIRouter(prefix="/workspaces/{workspace_id}", tags=["documents"])


def _title_of(content: str, fallback: str) -> str:
    for line in content.splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            return stripped.lstrip("#").strip() or fallback
        if stripped:
            if len(stripped) <= 80:
                return stripped
            break
    return fallback


@router.get("/repositories/{repository_id}/tree", response_model=DocumentTreeOut)
def document_tree(
    workspace_id: int,
    repository_id: int,
    path: str = Query("."),
    db: Session = Depends(get_db),
) -> DocumentTreeOut:
    get_workspace(db, workspace_id)
    repo = get_repository(db, workspace_id, repository_id)
    root = resolve_repo_root(repo)
    try:
        node = build_tree(root, path)
    except PathSecurityError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    except DocumentError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc

    return DocumentTreeOut(
        repository_id=repo.id,
        repository=repo.name,
        revision=git.head_revision(root) if git.is_repo(root) else None,
        root=node,
    )


@router.get("/repositories/{repository_id}/document", response_model=DocumentOut)
def read_doc(
    workspace_id: int,
    repository_id: int,
    path: str = Query(..., description="Repository-relative Markdown path"),
    db: Session = Depends(get_db),
) -> DocumentOut:
    get_workspace(db, workspace_id)
    repo = get_repository(db, workspace_id, repository_id)
    root = resolve_repo_root(repo)
    try:
        content = read_document(root, path)
    except PathSecurityError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    except DocumentError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc

    return DocumentOut(
        repository_id=repo.id,
        repository=repo.name,
        path=path.replace("\\", "/"),
        title=_title_of(content, path),
        raw_markdown=content,
        revision=git.head_revision(root) if git.is_repo(root) else None,
        size=len(content),
    )


@router.get(
    "/repositories/{repository_id}/document/links", response_model=DocumentLinksOut
)
def document_links(
    workspace_id: int,
    repository_id: int,
    path: str = Query(..., description="Repository-relative path of the document"),
    db: Session = Depends(get_db),
) -> DocumentLinksOut:
    """The links into and out of one document, as written in the Markdown.

    Read-only and derived from the repository on disk on every call: these are
    the author's own links, so a cached answer could contradict the file the
    reader is looking at.

    The inbound half scans the rest of the corpus, because Markdown records only
    the forward direction. That makes this more expensive than reading one
    file, and it is a separate endpoint for that reason: the reading pane wants
    the document, the context panel wants the graph, and neither should pay for
    the other.
    """
    get_workspace(db, workspace_id)
    repo = get_repository(db, workspace_id, repository_id)
    root = resolve_repo_root(repo)
    # Validated here even though collect_links tolerates anything. The service is
    # written to answer "no links" for a path it cannot read, which is right when
    # it is parsing links and wrong at the door: a traversal attempt must be
    # refused the same way every other document route refuses one, not answered
    # with an empty graph that looks like a document with no relationships.
    try:
        safe_path(root, path)
    except PathSecurityError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc

    try:
        result = collect_links(root, path)
    except PathSecurityError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    except DocumentError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc

    return DocumentLinksOut(
        repository_id=repo.id,
        path=result.path,
        outbound=[DocumentLinkOut(path=l.path, text=l.text) for l in result.outbound],
        inbound=[DocumentLinkOut(path=l.path, text=l.text) for l in result.inbound],
        external=[ExternalReferenceOut(target=e.target, text=e.text) for e in result.external],
    )


@router.get("/repositories/{repository_id}/search", response_model=SearchOut)
def search_docs(
    workspace_id: int,
    repository_id: int,
    q: str = Query(..., min_length=1),
    limit: int = Query(20, ge=1, le=100),
    path: str = Query("."),
    db: Session = Depends(get_db),
) -> SearchOut:
    get_workspace(db, workspace_id)
    repo = get_repository(db, workspace_id, repository_id)
    root = resolve_repo_root(repo)
    try:
        hits = search_documents(root, q, limit=limit, subdir=path)
    except PathSecurityError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    except DocumentError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc

    return SearchOut(
        repository_id=repo.id, query=q, hits=[SearchHitOut(**h.__dict__) for h in hits]
    )


@router.get("/repositories/{repository_id}/git", response_model=GitChangesOut)
def git_changes(
    workspace_id: int,
    repository_id: int,
    diff: bool = Query(True, description="Include the working-tree diff text"),
    db: Session = Depends(get_db),
) -> GitChangesOut:
    get_workspace(db, workspace_id)
    repo = get_repository(db, workspace_id, repository_id)
    root = resolve_repo_root(repo)

    if not git.is_repo(root):
        return GitChangesOut(
            repository_id=repo.id, repository=repo.name, entries=[], diff=""
        )

    try:
        entries = git.status(root)
        diff_text = git.diff(root) if diff else ""
    except git.GitError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"Git error: {exc}") from exc

    return GitChangesOut(
        repository_id=repo.id,
        repository=repo.name,
        branch=git.current_branch(root),
        revision=git.head_revision(root),
        entries=[GitStatusEntryOut(path=e.path, status=e.status) for e in entries],
        diff=diff_text,
    )
