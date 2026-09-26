"""Incremental semantic indexing of repository sources and documentation.

The pipeline: inspect the repository (reusing the ignore-aware file list from
services/inspection.py) -> parse code units with Tree-sitter -> embed the
enriched representation in batches -> upsert into the vector store -> remove
stale chunks whose files are gone. Content hashes make it idempotent: an
unchanged file is skipped entirely, a changed file is re-parsed, re-embedded
and upserted under the same deterministic identifiers, and a deleted file's
chunks are removed.

A malformed file never aborts the run: failures are recorded per file and the
indexer continues with the next one, and the run's status reports them.

Indexing runs in a daemon thread so the API never blocks on it; per-workspace
status (idle / indexing / completed / failed, with counters) is held in
process memory -- deliberately not a job framework, just enough state for the
UI to poll. One index run per workspace at a time.
"""
from __future__ import annotations

import hashlib
import logging
import threading
from dataclasses import dataclass, field
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.models import CodeChunk, DocumentChunk, Repository, Workspace
from app.services import inspection
from app.services.documents import list_documents, read_document
from app.services.embeddings import (
    EmbeddingError,
    document_provider,
    code_provider,
    aembed_code,
    aembed_documents,
    run_async,
)
from app.services.parsing import ParseError, content_hash, parse_file
from app.services.vector_store import SQLAlchemyVectorStore, vector_store

logger = logging.getLogger("apollo")

#: Longest a documentation section may be before it is split at paragraph
#: boundaries into sub-chunks. Sections usually fit; this is the safeguard
#: for a single enormous section, so no chunk floods an embedding request.
MAX_SECTION_CHARS = 6_000
#: Sub-chunks of an oversized section, at paragraph boundaries.
SUB_CHUNK_TARGET_CHARS = 2_000

INDEX_STATUSES = ("idle", "indexing", "completed", "failed")


class IndexingError(RuntimeError):
    """Raised when an index run cannot be started or configured."""


# ---------------------------------------------------------------------------
# Per-workspace run status
# ---------------------------------------------------------------------------


@dataclass
class IndexCounts:
    """Progress counters exposed through the status endpoint."""

    files_discovered: int = 0
    files_processed: int = 0
    code_units_indexed: int = 0
    documents_indexed: int = 0
    document_chunks_indexed: int = 0
    embeddings_generated: int = 0
    failed_files: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "files_discovered": self.files_discovered,
            "files_processed": self.files_processed,
            "code_units_indexed": self.code_units_indexed,
            "documents_indexed": self.documents_indexed,
            "document_chunks_indexed": self.document_chunks_indexed,
            "embeddings_generated": self.embeddings_generated,
            "failed_files": list(self.failed_files),
            "errors": list(self.errors),
        }


@dataclass
class IndexStatus:
    """The live state of a workspace's semantic index."""

    status: str = "idle"
    message: str | None = None
    started_at: str | None = None
    finished_at: str | None = None
    counts: IndexCounts = field(default_factory=IndexCounts)


#: workspace_id -> status. Guarded by the lock; read by the status endpoint.
_statuses: dict[int, IndexStatus] = {}
_lock = threading.Lock()
#: workspace ids with a live run; a second trigger is refused, not queued.
_running: set[int] = set()


def get_status(workspace_id: int) -> IndexStatus:
    with _lock:
        return _statuses.get(workspace_id) or IndexStatus()


def model_compatibility(db: Session, workspace_id: int) -> dict:
    """Detect vectors incompatible with the currently configured models.

    Chunks record the model and dimension they were embedded with. If the
    configuration has changed since they were written, the vectors live in a
    different space and must not be queried: the operator is told a re-index
    is required, and which model changed.
    """
    code_model = settings.code_embedding_model
    document_model = settings.document_embedding_model
    stale_code = db.scalar(
        select(CodeChunk.id)
        .join(CodeChunk.repository)
        .where(
            Repository.workspace_id == workspace_id,
            CodeChunk.embedding_model != code_model,
        )
        .limit(1)
    )
    stale_documents = db.scalar(
        select(DocumentChunk.id)
        .join(DocumentChunk.repository)
        .where(
            Repository.workspace_id == workspace_id,
            DocumentChunk.embedding_model != document_model,
        )
        .limit(1)
    )
    reasons: list[str] = []
    if stale_code is not None:
        reasons.append(
            f"Code vectors were embedded with a different model; re-index to use "
            f"{code_model!r}."
        )
    if stale_documents is not None:
        reasons.append(
            f"Document vectors were embedded with a different model; re-index to "
            f"use {document_model!r}."
        )
    return {"reindex_required": bool(reasons), "reasons": reasons}


# ---------------------------------------------------------------------------
# Documentation chunking (section-aligned)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class DocSection:
    """A heading-delimited slice of a document."""

    section: str
    start_line: int
    end_line: int
    content: str


def split_sections(content: str) -> list[DocSection]:
    """Split a document at Markdown headings.

    The document's own structure decides the boundaries -- every chunk starts
    at a heading (or the top of the file) and runs to the next one -- so a
    retrieved chunk is a meaningful fragment (one ADR section, one component
    description) and the citation can name the section honestly. An oversized
    section is split further at paragraph boundaries rather than mid-sentence.
    """
    lines = content.splitlines()
    boundaries: list[tuple[str, int]] = [("", 0)]  # (heading, 0-based line index)
    for index, line in enumerate(lines):
        stripped = line.lstrip()
        if stripped.startswith("#") and not stripped.startswith("```"):
            heading = stripped.lstrip("#").strip()
            if heading:
                boundaries.append((heading, index))

    sections: list[DocSection] = []
    for number, (heading, start) in enumerate(boundaries):
        end = boundaries[number + 1][1] - 1 if number + 1 < len(boundaries) else len(lines) - 1
        if end < start:
            continue
        body = "\n".join(lines[start : end + 1]).strip("\n")
        if not body.strip():
            continue
        if len(body) <= MAX_SECTION_CHARS:
            sections.append(
                DocSection(
                    section=heading,
                    start_line=start + 1,
                    end_line=end + 1,
                    content=body,
                )
            )
            continue
        # Oversized section: split at blank lines, keeping the heading text.
        paragraphs: list[str] = []
        current: list[str] = []
        position = start
        for line in lines[start : end + 1]:
            position += 1
            current.append(line)
            joined = "\n".join(current)
            if not line.strip() and len(joined.strip("\n")) >= SUB_CHUNK_TARGET_CHARS:
                paragraphs.append(joined.strip("\n"))
                current = []
        if current:
            paragraphs.append("\n".join(current).strip("\n"))
        offset = start + 1
        for paragraph in paragraphs:
            if not paragraph.strip():
                continue
            line_count = len(paragraph.splitlines())
            sections.append(
                DocSection(
                    section=heading,
                    start_line=offset,
                    end_line=offset + line_count - 1,
                    content=paragraph,
                )
            )
            offset += line_count
    return sections


def document_chunk_identifier(repository_id: int, file_path: str, section: str, content: str) -> str:
    digest = hashlib.sha256(
        f"{repository_id}|{file_path}|{section}|{content}".encode("utf-8")
    ).hexdigest()
    label = section.replace("/", "-").replace(":", "-")[:64] or "root"
    return f"{file_path}::{label}:{digest[:16]}"


# ---------------------------------------------------------------------------
# Indexing
# ---------------------------------------------------------------------------


def _index_code_file(
    store: SQLAlchemyVectorStore,
    repo: Repository,
    root: Path,
    rel_path: str,
    language: str,
    file_hash: str,
    counters: "IndexCounts | None" = None,
) -> int:
    """Parse, embed and upsert one source file. Returns units indexed."""
    units = parse_file(
        root / rel_path,
        repository_id=repo.id,
        repository_name=repo.name,
        file_path=rel_path,
        language=language,
    )
    if not units:
        return 0

    existing_hashes = {
        row[0]: row[1]
        for row in store.db.execute(
            select(CodeChunk.identifier, CodeChunk.content_hash).where(
                CodeChunk.repository_id == repo.id, CodeChunk.file_path == rel_path
            )
        ).all()
    }

    pending: list = []
    for unit in units:
        if existing_hashes.get(unit.identifier) == unit.content_hash:
            continue  # unchanged: skip embedding entirely
        pending.append(unit)

    if not pending:
        # Nothing changed; still make sure the file's rows are not stale.
        return 0

    result = run_async(aembed_code([unit.enriched for unit in pending]))
    if counters is not None:
        counters.embeddings_generated += len(pending)
    entries = []
    for unit, vector in zip(pending, result.vectors):
        entries.append(
            {
                "kind": "code",
                "repository_id": repo.id,
                "repository_name": repo.name,
                "file_path": rel_path,
                "language": unit.language,
                "node_type": unit.node_type,
                "symbol": unit.symbol,
                "signature": unit.signature,
                "start_line": unit.start_line,
                "end_line": unit.end_line,
                "content": unit.source,
                "enriched_content": unit.enriched,
                "content_hash": unit.content_hash,
                "identifier": unit.identifier,
                "embedding_model": result.model_name,
                "embedding_dimension": result.dimension,
                "embedding": vector,
            }
        )
    # A changed unit has a new identifier (it embeds the content), so the row
    # it supersedes must go: remove this file's previous rows for the same
    # symbol, then upsert the fresh ones. What remains is one row per unit.
    superseded = [
        identifier
        for identifier in existing_hashes
        if identifier not in {unit.identifier for unit in pending}
    ]
    if superseded:
        run_async(store.delete(kind="code", repository_id=repo.id, identifiers=superseded))
    run_async(store.upsert(entries))
    return len(pending)


def _index_document_file(
    store: SQLAlchemyVectorStore,
    repo: Repository,
    root: Path,
    rel_path: str,
    counters: "IndexCounts | None" = None,
) -> int:
    """Section-split, embed and upsert one documentation file."""
    content = read_document(root, rel_path)
    file_hash = content_hash(content)
    existing = {
        row[0]: row[1]
        for row in store.db.execute(
            select(DocumentChunk.identifier, DocumentChunk.content_hash).where(
                DocumentChunk.repository_id == repo.id,
                DocumentChunk.file_path == rel_path,
            )
        ).all()
    }
    sections = split_sections(content)
    pending: list[DocSection] = []
    for section in sections:
        identifier = document_chunk_identifier(repo.id, rel_path, section.section, section.content)
        if existing.get(identifier) == content_hash(section.content):
            continue
        pending.append(section)
    if not pending:
        return 0

    texts = [f"[{repo.name}] {s.content}" if s.section else s.content for s in pending]
    result = run_async(aembed_documents(texts))
    if counters is not None:
        counters.embeddings_generated += len(pending)
    entries = []
    for section, vector in zip(pending, result.vectors):
        identifier = document_chunk_identifier(repo.id, rel_path, section.section, section.content)
        entries.append(
            {
                "kind": "document",
                "repository_id": repo.id,
                "document_id": f"{repo.id}:{rel_path}",
                "file_path": rel_path,
                "section": section.section,
                "start_line": section.start_line,
                "end_line": section.end_line,
                "content": section.content,
                "content_hash": content_hash(section.content),
                "identifier": identifier,
                "embedding_model": result.model_name,
                "embedding_dimension": result.dimension,
                "embedding": vector,
            }
        )
    run_async(store.upsert(entries))
    return len(pending)


def _prune_stale(store: SQLAlchemyVectorStore, repo: Repository, live_paths: set[str]) -> int:
    """Delete code chunks for files that no longer exist on disk."""
    stored = {
        row[0]
        for row in store.db.execute(
            select(CodeChunk.file_path).where(CodeChunk.repository_id == repo.id)
        ).all()
    }
    dead = stored - live_paths
    removed = 0
    for path in dead:
        identifiers = store.db.scalars(
            select(CodeChunk.identifier).where(
                CodeChunk.repository_id == repo.id, CodeChunk.file_path == path
            )
        ).all()
        if identifiers:
            run_async(store.delete(kind="code", repository_id=repo.id, identifiers=identifiers))
        removed += len(identifiers)
    return removed


def _prune_stale_documents(store: SQLAlchemyVectorStore, repo: Repository, live_paths: set[str]) -> int:
    stored = {
        row[0]
        for row in store.db.execute(
            select(DocumentChunk.file_path).where(DocumentChunk.repository_id == repo.id)
        ).all()
    }
    dead = stored - live_paths
    removed = 0
    for path in dead:
        rows = store.db.scalars(
            select(DocumentChunk.identifier).where(
                DocumentChunk.repository_id == repo.id, DocumentChunk.file_path == path
            )
        ).all()
        if rows:
            run_async(store.delete(kind="document", repository_id=repo.id, identifiers=rows))
        removed += len(rows)
    return removed


def index_workspace_now(db: Session, workspace_id: int) -> IndexStatus:
    """Run a full indexing pass synchronously (used by tests and the worker)."""
    import datetime as dt

    status = IndexStatus(status="indexing", started_at=dt.datetime.now(dt.timezone.utc).isoformat())
    with _lock:
        _statuses[workspace_id] = status
    try:
        _run_indexing(db, workspace_id, status)
        status.status = "completed"
    except EmbeddingError as exc:
        status.status = "failed"
        status.message = str(exc)
    except Exception as exc:  # noqa: BLE001 - reported, never crashes the worker
        status.status = "failed"
        status.message = f"{type(exc).__name__}: {exc}"
        logger.exception("Semantic indexing of workspace %s failed", workspace_id)
    finally:
        status.finished_at = dt.datetime.now(dt.timezone.utc).isoformat()
        with _lock:
            _running.discard(workspace_id)
    return status


def reindex_now(db: Session, workspace_id: int) -> IndexStatus:
    """Synchronous controlled re-index: clear, then rebuild."""
    _drop_workspace_chunks(db, workspace_id)
    return index_workspace_now(db, workspace_id)


def _run_indexing(db: Session, workspace_id: int, status: IndexStatus) -> None:
    repositories = db.scalars(
        select(Repository).where(Repository.workspace_id == workspace_id).order_by(Repository.id)
    ).all()
    if not repositories:
        status.message = "No repositories registered; nothing to index."
        return

    store: SQLAlchemyVectorStore = vector_store(db)  # type: ignore[assignment]

    for repo in repositories:
        try:
            root = Path(repo.local_path)
            if repo.kind == "source":
                files = inspection.list_code_files(root, ".")
                status.counts.files_discovered += len(files)
                from app.services.parsing import language_for

                live: set[str] = set()
                for entry in files:
                    language = language_for(entry.path)
                    if language is None:
                        # A text file we do not parse (yet): still counts as
                        # discovered, and is not a failure.
                        continue
                    live.add(entry.path)
                    try:
                        status.counts.code_units_indexed += _index_code_file(
                            store, repo, root, entry.path, language, "", status.counts
                        )
                        status.counts.files_processed += 1
                    except (ParseError, EmbeddingError) as exc:
                        status.counts.failed_files.append(entry.path)
                        status.counts.errors.append(f"{repo.name}/{entry.path}: {exc}")
                        continue
                _prune_stale(store, repo, live)
            else:
                documents = list_documents(root, ".")
                status.counts.files_discovered += len(documents)
                for rel in documents:
                    try:
                        status.counts.document_chunks_indexed += _index_document_file(
                            store, repo, root, rel, status.counts
                        )
                        status.counts.files_processed += 1
                        status.counts.documents_indexed += 1
                    except Exception as exc:  # noqa: BLE001 - continue past failures
                        status.counts.failed_files.append(rel)
                        status.counts.errors.append(f"{repo.name}/{rel}: {exc}")
                        continue
                _prune_stale_documents(store, repo, set(documents))
        except Exception as exc:  # noqa: BLE001 - one repository must not stop the rest
            status.counts.errors.append(f"{repo.name}: {exc}")
            continue
    db.commit()


def start_indexing(db: Session, workspace_id: int, force_reindex: bool = False) -> IndexStatus:
    """Trigger a background index run for a workspace; never blocks the API.

    Returns the (immediately visible) status. A run already in progress is
    reported as such rather than queued: one index run per workspace at a
    time, no job framework.
    """
    from app.db import SessionLocal

    with _lock:
        current = _statuses.get(workspace_id)
        if current is not None and current.status == "indexing":
            return current
        _statuses[workspace_id] = IndexStatus(
            status="indexing",
            started_at=_utcnow().isoformat(),
        )
        _running.add(workspace_id)
        placeholder = _statuses[workspace_id]

    def worker() -> None:
        session = SessionLocal()
        try:
            status = placeholder
            try:
                if force_reindex:
                    _drop_workspace_chunks(session, workspace_id)
                _run_indexing(session, workspace_id, status)
                status.status = "completed"
            except EmbeddingError as exc:
                status.status = "failed"
                status.message = str(exc)
            except Exception as exc:  # noqa: BLE001 - worker must never die silently
                status.status = "failed"
                status.message = f"{type(exc).__name__}: {exc}"
                logger.exception("Semantic indexing of workspace %s failed", workspace_id)
            finally:
                status.finished_at = _utcnow().isoformat()
                with _lock:
                    _running.discard(workspace_id)
        finally:
            session.close()

    threading.Thread(target=worker, name=f"semantic-index-{workspace_id}", daemon=True).start()
    return placeholder


def _utcnow():
    import datetime as dt

    return dt.datetime.now(dt.timezone.utc)


def _drop_workspace_chunks(db: Session, workspace_id: int) -> None:
    """Controlled re-index: clear this workspace's vectors before rebuilding."""
    from sqlalchemy import delete

    repository_ids = db.scalars(
        select(Repository.id).where(Repository.workspace_id == workspace_id)
    ).all()
    if not repository_ids:
        return
    db.execute(delete(CodeChunk).where(CodeChunk.repository_id.in_(repository_ids)))
    db.execute(delete(DocumentChunk).where(DocumentChunk.repository_id.in_(repository_ids)))
    db.commit()
