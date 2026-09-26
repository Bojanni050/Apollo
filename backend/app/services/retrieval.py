"""Semantic and hybrid retrieval over code and documentation vectors.

Three entry points, layered on the existing systems:

* :func:`search_codebase` -- semantic search over source code (natural
  language -> code, via the code embedding model);
* :func:`search_documents_semantically` -- semantic search over documentation
  chunks (BGE-M3);
* :func:`hybrid_search` -- the combined architecture-aware retrieval: lexical
  relevance (the existing :mod:`app.services.search` / :mod:`app.services.inspection`
  searches are unchanged and still used) plus vector similarity plus AST/code
  metadata, merged with a simple deterministic ranker.

Every result carries what a citation needs (repository, file, symbol or
section, line range, score, exact content) and nothing is invented: line
numbers and content come from the chunks that were indexed from the real
files.
"""
from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.models import CodeChunk, DocumentChunk, Repository
from app.services import inspection
from app.services.embeddings import (
    EmbeddingError,
    aembed_code,
    aembed_documents,
    code_provider,
    document_provider,
    run_async,
)
from app.services.search import search_documents
from app.services.vector_store import VectorMatch, vector_store


class RetrievalError(RuntimeError):
    """Raised when retrieval cannot run (no index, incompatible vectors)."""


@dataclass(frozen=True)
class Evidence:
    """A retrieval result, ready to become a citation-backed AI answer.

    ``kind`` is ``code`` or ``document``. For code, ``symbol``/``node_type``
    and the line range identify the exact unit; for documentation, ``section``
    names the heading the chunk lives under.
    """

    kind: str
    repository_id: int
    repository: str
    file_path: str
    content: str
    score: float
    symbol: str | None = None
    node_type: str | None = None
    start_line: int | None = None
    end_line: int | None = None
    section: str | None = None
    lexical_score: float = 0.0

    @property
    def source(self) -> str:
        """A human-readable location line for AI answers and citations."""
        if self.kind == "code":
            location = self.symbol or self.node_type or "code"
            return f"{self.repository}/{self.file_path}:{self.start_line}-{self.end_line} ({location})"
        section = f" [{self.section}]" if self.section else ""
        return f"{self.repository}/{self.file_path}{section}"


def _vector_to_evidence(match: VectorMatch, kind: str, db: Session | None = None) -> Evidence:
    repository_name = match.repository_name
    if not repository_name and db is not None:
        repository_name = db.get(Repository, match.repository_id).name if db.get(Repository, match.repository_id) else ""
    return Evidence(
        kind=kind,
        repository_id=match.repository_id,
        repository=repository_name,
        file_path=match.file_path,
        content=match.content,
        score=match.score,
        symbol=match.symbol,
        node_type=match.node_type,
        start_line=match.start_line,
        end_line=match.end_line,
        section=match.section,
    )


def _has_vectors(db: Session, workspace_id: int) -> bool:
    """Whether any indexed vectors exist for the workspace at all."""
    code = db.scalar(
        select(CodeChunk.id)
        .join(CodeChunk.repository)
        .where(Repository.workspace_id == workspace_id)
        .limit(1)
    )
    if code is not None:
        return True
    documents = db.scalar(
        select(DocumentChunk.id)
        .join(DocumentChunk.repository)
        .where(Repository.workspace_id == workspace_id)
        .limit(1)
    )
    return documents is not None


def _require_vectors(db: Session, workspace_id: int) -> None:
    """Refuse semantic search against an unindexed workspace, with guidance."""
    if not _has_vectors(db, workspace_id):
        raise RetrievalError(
            "No semantic index for this workspace yet. Trigger indexing first "
            "(POST /api/workspaces/{id}/sources/index)."
        )


def search_codebase(
    db: Session,
    query: str,
    n_results: int = 5,
    repository_id: int | None = None,
    workspace_id: int | None = None,
) -> list[Evidence]:
    """Semantic code search: natural language in, code units out.

    ``repository_id`` restricts the search to one source repository; without
    it, every source repository of the workspace (when ``workspace_id`` is
    given) is searched.
    """
    n = max(1, min(int(n_results or settings.vector_search_limit), 50))
    if workspace_id is not None:
        _require_vectors(db, workspace_id)

    provider = code_provider()
    result = run_async(aembed_code([query]))
    query_vector = result.vectors[0]
    store = vector_store(db)
    if repository_id is not None:
        matches = run_async(
            store.search(
                query_vector,
                kind="code",
                model=result.model_name,
                dimension=result.dimension,
                repository_id=repository_id,
                limit=n,
            )
        )
        return [_vector_to_evidence(m, "code", db) for m in matches]

    repos = db.scalars(
        select(Repository).where(
            Repository.kind == "source",
            *( [Repository.workspace_id == workspace_id] if workspace_id is not None else [] ),
        )
    ).all()
    out: list[Evidence] = []
    for repo in repos:
        matches = run_async(
            store.search(
                query_vector,
                kind="code",
                model=result.model_name,
                dimension=result.dimension,
                repository_id=repo.id,
                limit=n,
            )
        )
        out.extend(_vector_to_evidence(m, "code", db) for m in matches)
    out.sort(key=lambda e: e.score, reverse=True)
    return out[:n]


def search_documents_semantically(
    db: Session,
    query: str,
    n_results: int = 5,
    workspace_id: int | None = None,
) -> list[Evidence]:
    """Semantic documentation search over section-aligned chunks.

    With a ``workspace_id`` the search is scoped to that workspace's
    documentation repositories, exactly like :func:`search_codebase` scopes to
    its source repositories -- chunks of another workspace are never returned.
    """
    n = max(1, min(int(n_results or settings.vector_search_limit), 50))
    if workspace_id is not None:
        _require_vectors(db, workspace_id)

    result = run_async(aembed_documents([query]))
    query_vector = result.vectors[0]
    store = vector_store(db)

    if workspace_id is None:
        matches = run_async(
            store.search(
                query_vector,
                kind="document",
                model=result.model_name,
                dimension=result.dimension,
                limit=n,
            )
        )
        return [_vector_to_evidence(m, "document", db) for m in matches]

    repos = db.scalars(
        select(Repository).where(
            Repository.kind == "documentation",
            Repository.workspace_id == workspace_id,
        )
    ).all()
    out: list[Evidence] = []
    for repo in repos:
        matches = run_async(
            store.search(
                query_vector,
                kind="document",
                model=result.model_name,
                dimension=result.dimension,
                repository_id=repo.id,
                limit=n,
            )
        )
        out.extend(_vector_to_evidence(m, "document", db) for m in matches)
    out.sort(key=lambda e: e.score, reverse=True)
    return out[:n]


@dataclass(frozen=True)
class HybridResult:
    """Lexical + semantic + metadata, merged deterministically."""

    evidence: list[Evidence]
    lexical_hits: int
    semantic_hits: int


def hybrid_search(
    db: Session,
    query: str,
    n_results: int = 5,
    repository_id: int | None = None,
    workspace_id: int | None = None,
    kinds: tuple[str, ...] = ("code", "document"),
    include_semantic: bool = True,
) -> HybridResult:
    """Architecture-aware retrieval: lexical + AST metadata + vector.

    The existing lexical searches still run unchanged; their hits provide
    keyword evidence and (through the vector store) the code-unit metadata.
    Vector search adds semantic neighbours. The merger is deliberately simple
    and deterministic: score = lexical overlap (a presence bonus) + vector
    similarity, code and documentation ranked in one list, repository filter
    honoured, ties broken by (score, path, symbol).

    Lexical hits for files outside the index are kept with their lexical
    score, so an unindexed file is not invisible -- but semantic evidence
    outranks it once indexed.
    """
    n = max(1, min(int(n_results or settings.vector_search_limit), 50))
    combined: dict[tuple[str, int, str, int], Evidence] = {}

    # ---- Lexical (existing searches, unchanged) ----------------------------
    lexical_hits = 0
    repos = db.scalars(
        select(Repository).where(
            Repository.workspace_id == workspace_id
            if workspace_id is not None else Repository.id.is_not(None)
        )
    ).all()
    if repository_id is not None:
        repos = [r for r in repos if r.id == repository_id]

    for repo in repos:
        if "code" in kinds and repo.kind == "source":
            for hit in inspection.search_code(repo.local_path, query, limit=n):
                key = ("code", repo.id, hit.path, hit.line)
                existing = combined.get(key)
                bonus = 0.5 if existing is None else 0.0
                lexical_hits += 1
                combined[key] = Evidence(
                    kind="code",
                    repository_id=repo.id,
                    repository=repo.name,
                    file_path=hit.path,
                    content=hit.snippet,
                    score=(existing.score if existing else 0.0) + bonus + min(hit.score / 20.0, 1.0),
                    symbol=None,
                    node_type=None,
                    start_line=hit.line,
                    end_line=hit.line,
                    lexical_score=hit.score,
                )
        if "document" in kinds and repo.kind == "documentation":
            for hit in search_documents(repo.local_path, query, limit=n):
                key = ("document", repo.id, hit.path, hit.line or 1)
                existing = combined.get(key)
                bonus = 0.5 if existing is None else 0.0
                lexical_hits += 1
                combined[key] = Evidence(
                    kind="document",
                    repository_id=repo.id,
                    repository=repo.name,
                    file_path=hit.path,
                    content=hit.snippet,
                    score=(existing.score if existing else 0.0) + bonus + min(hit.score / 20.0, 1.0),
                    section=None,
                    start_line=hit.line,
                    end_line=hit.line,
                    lexical_score=hit.score,
                )

    # ---- Semantic (vectors) -----------------------------------------------
    semantic_hits = 0
    semantic: list[Evidence] = []
    if not include_semantic:
        # Lexical-only mode: the existing keyword searches, unchanged, with
        # no vector contribution to scores or hits.
        evidence = sorted(
            combined.values(),
            key=lambda e: (-e.score, e.file_path, e.symbol or e.section or ""),
        )
        return HybridResult(evidence=evidence[:n], lexical_hits=lexical_hits, semantic_hits=0)
    try:
        if "code" in kinds:
            semantic.extend(search_codebase(db, query, n_results=n, repository_id=repository_id, workspace_id=workspace_id))
        if "document" in kinds:
            semantic.extend(search_documents_semantically(db, query, n_results=n, workspace_id=workspace_id))
    except (RetrievalError, EmbeddingError):
        # No index yet (or no configured model): lexical-only hybrid. The
        # caller sees what exists; semantic is simply absent.
        semantic = []

    for ev in semantic:
        semantic_hits += 1
        line = ev.start_line or 1
        key = (ev.kind, ev.repository_id, ev.file_path, line)
        existing = combined.get(key)
        if existing is not None:
            merged = Evidence(
                kind=ev.kind,
                repository_id=ev.repository_id,
                repository=ev.repository,
                file_path=ev.file_path,
                content=ev.content,
                score=existing.score + ev.score,
                symbol=ev.symbol or existing.symbol,
                node_type=ev.node_type or existing.node_type,
                start_line=ev.start_line or existing.start_line,
                end_line=ev.end_line or existing.end_line,
                section=ev.section or existing.section,
                lexical_score=existing.lexical_score,
            )
            combined[key] = merged
        else:
            combined[key] = ev

    evidence = sorted(
        combined.values(),
        key=lambda e: (-e.score, e.file_path, e.symbol or e.section or ""),
    )
    return HybridResult(evidence=evidence[:n], lexical_hits=lexical_hits, semantic_hits=semantic_hits)
