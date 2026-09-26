"""Repository source endpoints: management, manifest import, inspection.

These routes extend the existing repository surface rather than replacing it.
Sources are :class:`app.models.Repository` rows with ``kind='source'``; the
difference is that they can be GitHub repositories synchronized into a managed
checkout, they are always read-only, and they can be described portably by a
workspace-level ``.sources.yaml`` manifest.

Nothing here writes to any remote: synchronization is clone/pull only, and
inspection is strictly read-only.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_repository, get_workspace, resolve_repo_root
from app.db import get_db
from app.models import Repository, Workspace
from app.schemas import (
    IndexCountsOut,
    IndexStatusOut,
    ManifestEntryOut,
    ManifestImportIn,
    ManifestImportOut,
    ManifestPreviewOut,
    ManifestValidateIn,
    RepositoryOut,
    SemanticSearchHitOut,
    SemanticSearchOut,
    SourceCreate,
    SourceFileListOut,
    SourceFileOut,
    SourceReadOut,
    SourceSearchHitOut,
    SourceSearchOut,
    SourceStructureOut,
    SourceSyncOut,
)
from app.services import inspection, sources
from app.services.inspection import InspectionError
from app.services.manifest import ManifestError, render_manifest, validate_manifest
from app.services.paths import PathSecurityError
from app.services.sources import SourceError

router = APIRouter(prefix="/workspaces/{workspace_id}", tags=["sources"])


def _source_or_404(db: Session, workspace_id: int, repository_id: int) -> Repository:
    repo = get_repository(db, workspace_id, repository_id)
    if repo.kind != "source":
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "This repository is a documentation repository, not a source.",
        )
    return repo


def _source_out(repo: Repository) -> RepositoryOut:
    out = RepositoryOut.model_validate(repo)
    live_status, message = sources.effective_status(repo)
    out.status = live_status
    out.status_message = message or repo.status_message
    return out


def _checkout_root(repo: Repository) -> str:
    """The directory inspection operates on, validated on every use.

    A GitHub source that has not been synchronized yet has no checkout; that is
    reported as the source's own status rather than a raw path error, so the UI
    can point the operator at the refresh action.
    """
    try:
        return resolve_repo_root(repo)
    except HTTPException as exc:
        if repo.source_type == "github":
            _, message = sources.effective_status(repo)
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                message or "This GitHub source has not been synchronized yet. Synchronize it first.",
            ) from exc
        raise


def _existing_identities(db: Session, workspace: Workspace) -> dict[str, dict[str, str]]:
    """identity -> display name, per source type, for duplicate detection."""
    identities: dict[str, dict[str, str]] = {"local": {}, "github": {}}
    repos = db.scalars(
        select(Repository).where(
            Repository.workspace_id == workspace.id, Repository.kind == "source"
        )
    ).all()
    for repo in repos:
        stype = repo.source_type or "local"
        if stype == "github" and repo.source_url:
            try:
                identity = sources.normalize_repo_url(repo.source_url)
            except SourceError:
                continue
        else:
            identity = sources.local_identity(repo.local_path)
        identities.setdefault(stype, {})[identity] = repo.name
    return identities


# ---------------------------------------------------------------------------
# Management
# ---------------------------------------------------------------------------


@router.get("/sources", response_model=list[RepositoryOut])
def list_sources(workspace_id: int, db: Session = Depends(get_db)) -> list[RepositoryOut]:
    workspace = get_workspace(db, workspace_id)
    repos = db.scalars(
        select(Repository)
        .where(Repository.workspace_id == workspace.id, Repository.kind == "source")
        .order_by(Repository.id)
    ).all()
    return [_source_out(r) for r in repos]


@router.post("/sources", response_model=RepositoryOut, status_code=status.HTTP_201_CREATED)
def add_source(
    workspace_id: int, payload: SourceCreate, db: Session = Depends(get_db)
) -> RepositoryOut:
    """Register a repository source.

    Local paths are validated against the workspace-root allow-list; GitHub
    URLs are stored pending synchronization. Nothing is cloned here.
    """
    workspace = get_workspace(db, workspace_id)
    try:
        repo = sources.register_source(
            db,
            workspace,
            name=payload.name,
            location=payload.location,
            source_type=payload.source_type,
            branch=payload.branch,
            description=payload.description,
        )
    except SourceError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    db.commit()
    db.refresh(repo)
    return _source_out(repo)


@router.delete("/sources/{repository_id}", status_code=status.HTTP_204_NO_CONTENT)
def remove_source(
    workspace_id: int, repository_id: int, db: Session = Depends(get_db)
) -> Response:
    """Remove a source registration.

    The managed checkout of a GitHub source is left on disk: it is the
    operator's evidence, and deleting files behind their back is not this
    endpoint's decision to make.
    """
    repo = _source_or_404(db, workspace_id, repository_id)
    db.delete(repo)
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/sources/{repository_id}/sync", response_model=SourceSyncOut)
def sync_source(
    workspace_id: int, repository_id: int, db: Session = Depends(get_db)
) -> SourceSyncOut:
    """Synchronize a source.

    Local sources: refresh their status. GitHub sources: clone into the managed
    checkout on first sync, then fast-forward pull. The remote is never
    modified -- no push, no commit, no branch changes.
    """
    repo = _source_or_404(db, workspace_id, repository_id)
    try:
        result = sources.sync_source(repo)
    except SourceError as exc:
        db.commit()  # persist the error status/message on the row
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    db.commit()
    db.refresh(repo)
    return SourceSyncOut(repository=_source_out(repo), **result)


# ---------------------------------------------------------------------------
# Manifest (.sources.yaml)
# ---------------------------------------------------------------------------


@router.post("/sources/manifest/validate", response_model=ManifestPreviewOut)
def validate_sources_manifest(
    workspace_id: int, payload: ManifestValidateIn, db: Session = Depends(get_db)
) -> ManifestPreviewOut:
    """Parse and validate a manifest and return a preview. Changes nothing."""
    workspace = get_workspace(db, workspace_id)
    try:
        report = validate_manifest(payload.content, _existing_identities(db, workspace))
    except ManifestError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    return ManifestPreviewOut(
        total=len(report.entries),
        valid_count=len(report.valid_entries),
        invalid_count=len(report.invalid_entries),
        new_count=len(report.new_entries),
        duplicate_count=len(report.duplicates),
        entries=[ManifestEntryOut.model_validate(e) for e in report.entries],
    )


@router.post("/sources/manifest/import", response_model=ManifestImportOut)
def import_sources_manifest(
    workspace_id: int, payload: ManifestImportIn, db: Session = Depends(get_db)
) -> ManifestImportOut:
    """Import a validated manifest after the operator has confirmed it.

    Idempotent by identity: entries whose repository already exists are
    reported as duplicates and left alone, so importing the same file twice
    creates nothing. Entries marked invalid during validation are never
    silently skipped -- they are reported with their reasons. The manifest is
    retained at the workspace level as the portable description of the
    configured sources; local checkout paths stay machine-specific.
    """
    if not payload.confirm:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "Import is not confirmed. Review the preview from "
            "/sources/manifest/validate and re-send with confirm=true.",
        )
    workspace = get_workspace(db, workspace_id)
    try:
        report = validate_manifest(payload.content, _existing_identities(db, workspace))
    except ManifestError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc

    imported: list[Repository] = []
    duplicates: list[str] = []
    invalid: list[dict[str, str]] = [
        {"repo": e.repo, "path": e.path, "error": e.error or "Invalid entry."}
        for e in report.invalid_entries
    ]

    for entry in report.new_entries:
        try:
            repo = sources.register_source(
                db,
                workspace,
                name=entry.repo,
                location=entry.path,
                source_type=entry.source_type,
                branch=entry.branch,
                idempotent=True,
            )
        except SourceError as exc:
            invalid.append({"repo": entry.repo, "path": entry.path, "error": str(exc)})
            continue
        if repo not in imported:
            imported.append(repo)
    for dup in report.duplicates:
        duplicates.append(dup.existing_name or dup.repo)

    db.commit()
    for repo in imported:
        db.refresh(repo)

    manifest_path = sources.persist_manifest(db, workspace)
    return ManifestImportOut(
        imported=[_source_out(r) for r in imported],
        duplicates=duplicates,
        invalid=invalid,
        manifest_path=str(manifest_path) if manifest_path else None,
    )


@router.get("/sources/manifest", response_model=ManifestPreviewOut)
def get_sources_manifest(
    workspace_id: int, db: Session = Depends(get_db)
) -> ManifestPreviewOut:
    """The workspace's retained manifest, rendered as a preview.

    Describes source identity and location only -- the portable description,
    never machine-specific checkout paths.
    """
    workspace = get_workspace(db, workspace_id)
    if not sources.manifest_entries(db, workspace):
        # An empty workspace has a manifest with no entries yet; that is a
        # valid, empty preview rather than an invalid manifest.
        return ManifestPreviewOut(
            total=0, valid_count=0, invalid_count=0, new_count=0, duplicate_count=0, entries=[]
        )
    try:
        text = render_manifest(db, workspace)
        report = validate_manifest(text, _existing_identities(db, workspace))
    except ManifestError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    return ManifestPreviewOut(
        total=len(report.entries),
        valid_count=len(report.valid_entries),
        invalid_count=len(report.invalid_entries),
        new_count=len(report.new_entries),
        duplicate_count=len(report.duplicates),
        entries=[ManifestEntryOut.model_validate(e) for e in report.entries],
    )


# ---------------------------------------------------------------------------
# Inspection (read-only)
# ---------------------------------------------------------------------------


@router.get("/sources/{repository_id}/files", response_model=SourceFileListOut)
def list_source_files(
    workspace_id: int,
    repository_id: int,
    path: str = Query("."),
    limit: int = Query(200, ge=1, le=400),
    db: Session = Depends(get_db),
) -> SourceFileListOut:
    """List text source files under a path, skipping ignored directories."""
    repo = _source_or_404(db, workspace_id, repository_id)
    root = _checkout_root(repo)
    try:
        files = inspection.list_code_files(root, path, limit=limit)
    except (InspectionError, PathSecurityError) as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    return SourceFileListOut(
        repository_id=repo.id,
        repository=repo.name,
        path=path,
        files=[SourceFileOut(path=f.path, size=f.size) for f in files],
    )


@router.get("/sources/{repository_id}/file", response_model=SourceReadOut)
def read_source_file(
    workspace_id: int,
    repository_id: int,
    path: str = Query(..., description="Repository-relative source file path"),
    start_line: int = Query(1, ge=1),
    end_line: int = Query(0, ge=0),
    db: Session = Depends(get_db),
) -> SourceReadOut:
    """Read a bounded slice of a text source file. Binary files are refused."""
    repo = _source_or_404(db, workspace_id, repository_id)
    root = _checkout_root(repo)
    try:
        content = inspection.read_source_file(
            root, path, start_line=start_line, end_line=end_line
        )
    except (InspectionError, PathSecurityError) as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    total = inspection.count_lines(root, path)
    lines = content.splitlines()
    header, *body = lines
    return SourceReadOut(
        repository_id=repo.id,
        repository=repo.name,
        path=path.replace("\\", "/"),
        content=content,
        start_line=start_line,
        end_line=min(end_line or start_line + len(body) - 1, total),
        total_lines=total,
    )


@router.get("/sources/{repository_id}/search", response_model=SourceSearchOut)
def search_source_code(
    workspace_id: int,
    repository_id: int,
    q: str = Query(..., min_length=1),
    limit: int = Query(20, ge=1, le=100),
    path: str = Query("."),
    db: Session = Depends(get_db),
) -> SourceSearchOut:
    """Lexical search over a source repository's text files."""
    repo = _source_or_404(db, workspace_id, repository_id)
    root = _checkout_root(repo)
    try:
        hits = inspection.search_code(root, q, subdir=path, limit=limit)
    except (InspectionError, PathSecurityError) as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    return SourceSearchOut(
        repository_id=repo.id,
        repository=repo.name,
        query=q,
        hits=[SourceSearchHitOut(**h.__dict__) for h in hits],
    )


@router.get("/sources/{repository_id}/structure", response_model=SourceStructureOut)
def source_structure(
    workspace_id: int,
    repository_id: int,
    depth: int = Query(3, ge=1, le=6),
    db: Session = Depends(get_db),
) -> SourceStructureOut:
    """A compact summary of the repository's project structure."""
    repo = _source_or_404(db, workspace_id, repository_id)
    root = _checkout_root(repo)
    return SourceStructureOut(
        repository_id=repo.id,
        repository=repo.name,
        structure=inspection.project_structure(root, max_depth=depth),
    )


# ---------------------------------------------------------------------------
# Semantic indexing (embeddings + pgvector): status, trigger, re-index, search
# ---------------------------------------------------------------------------


def _index_status_out(workspace_id: int, db: Session) -> IndexStatusOut:
    """The live status plus model-compatibility information.

    ``reindex_required`` is the honest signal the UI needs: it says the stored
    vectors were embedded by a different model than the one now configured,
    so semantic search would compare incompatible spaces until the operator
    re-indexes.
    """
    from app.config import settings
    from app.services import indexing

    status = indexing.get_status(workspace_id)
    compatibility = indexing.model_compatibility(db, workspace_id)
    return IndexStatusOut(
        status=status.status,
        message=status.message,
        started_at=status.started_at,
        finished_at=status.finished_at,
        counts=IndexCountsOut(**status.counts.as_dict()),
        code_embedding_model=settings.code_embedding_model,
        document_embedding_model=settings.document_embedding_model,
        reindex_required=compatibility["reindex_required"],
        reindex_reasons=compatibility["reasons"],
    )


@router.get("/sources/index", response_model=IndexStatusOut)
def index_status(workspace_id: int, db: Session = Depends(get_db)) -> IndexStatusOut:
    """The workspace's semantic index status (idle/indexing/completed/failed)."""
    get_workspace(db, workspace_id)
    return _index_status_out(workspace_id, db)


@router.post("/sources/index", response_model=IndexStatusOut)
def trigger_indexing(
    workspace_id: int,
    background: bool = Query(
        True, description="Run in the background (default); false runs synchronously."
    ),
    reindex: bool = Query(False, description="Discard existing vectors first (model change)."),
    db: Session = Depends(get_db),
) -> IndexStatusOut:
    """Start (or re-start) semantic indexing for every repository of the workspace.

    Never modifies any repository: the indexer only reads files and writes
    chunk rows. Background mode returns immediately with status=indexing;
    synchronous mode waits and reports the finished status (used by tests and
    small workspaces).
    """
    from app.services import indexing

    workspace = get_workspace(db, workspace_id)
    if background:
        indexing.start_indexing(db, workspace.id, force_reindex=reindex)
        db.commit()
    elif reindex:
        indexing.reindex_now(db, workspace.id)
    else:
        indexing.index_workspace_now(db, workspace.id)
    return _index_status_out(workspace_id, db)


@router.post("/sources/reindex", response_model=IndexStatusOut)
def reindex_workspace(
    workspace_id: int,
    background: bool = Query(True),
    db: Session = Depends(get_db),
) -> IndexStatusOut:
    """Controlled re-index: discard this workspace's vectors and rebuild them.

    Required when the configured embedding model changes -- existing vectors
    are in a different space and must never be silently queried alongside the
    new model's vectors.
    """
    from app.services import indexing

    workspace = get_workspace(db, workspace_id)
    if background:
        indexing.start_indexing(db, workspace.id, force_reindex=True)
        db.commit()
    else:
        indexing.reindex_now(db, workspace.id)
    return _index_status_out(workspace_id, db)


@router.get("/sources/search", response_model=SemanticSearchOut)
def semantic_search(
    workspace_id: int,
    q: str = Query(..., min_length=1),
    mode: str = Query("hybrid", pattern="^(semantic|hybrid|lexical)$"),
    kind: str = Query("all", pattern="^(all|code|document)$"),
    repository_id: int | None = Query(None),
    limit: int = Query(5, ge=1, le=50),
    db: Session = Depends(get_db),
) -> SemanticSearchOut:
    """Search across indexed code and documentation.

    ``mode=lexical`` is the existing keyword search; ``semantic`` uses the
    vector index; ``hybrid`` (default) combines both with AST/code-unit
    metadata. Never injects the whole index: results are bounded by ``limit``.
    """
    from app.services import retrieval

    get_workspace(db, workspace_id)
    kinds = ("code", "document") if kind == "all" else (kind,)

    if mode == "hybrid":
        result = retrieval.hybrid_search(
            db,
            q,
            n_results=limit,
            repository_id=repository_id,
            workspace_id=workspace_id,
            kinds=kinds,
        )
        return SemanticSearchOut(
            query=q,
            mode="hybrid",
            repository_id=repository_id,
            lexical_hits=result.lexical_hits,
            semantic_hits=result.semantic_hits,
            hits=[SemanticSearchHitOut(**_hit_fields(e)) for e in result.evidence],
        )

    if mode == "semantic":
        evidence: list = []
        if "code" in kinds:
            evidence.extend(
                retrieval.search_codebase(
                    db, q, n_results=limit, repository_id=repository_id, workspace_id=workspace_id
                )
            )
        if "document" in kinds:
            evidence.extend(
                retrieval.search_documents_semantically(db, q, n_results=limit, workspace_id=workspace_id)
            )
        evidence.sort(key=lambda e: e.score, reverse=True)
        return SemanticSearchOut(
            query=q,
            mode="semantic",
            repository_id=repository_id,
            semantic_hits=len(evidence),
            hits=[SemanticSearchHitOut(**_hit_fields(e)) for e in evidence[:limit]],
        )

    # Lexical-only: reuse the existing searches through the hybrid machinery
    # with vectors excluded, so keyword behaviour is unchanged and no semantic
    # results leak into a keyword-only request.
    result = retrieval.hybrid_search(
        db,
        q,
        n_results=limit,
        repository_id=repository_id,
        workspace_id=workspace_id,
        kinds=kinds,
        include_semantic=False,
    )
    return SemanticSearchOut(
        query=q,
        mode="lexical",
        repository_id=repository_id,
        lexical_hits=result.lexical_hits,
        hits=[SemanticSearchHitOut(**_hit_fields(e)) for e in result.evidence],
    )


def _hit_fields(evidence) -> dict:
    return {
        "kind": evidence.kind,
        "repository_id": evidence.repository_id,
        "repository": evidence.repository,
        "file_path": evidence.file_path,
        "content": evidence.content,
        "score": evidence.score,
        "symbol": evidence.symbol,
        "node_type": evidence.node_type,
        "start_line": evidence.start_line,
        "end_line": evidence.end_line,
        "section": evidence.section,
        "source": evidence.source,
    }
