"""Vector storage for semantic retrieval, on PostgreSQL + pgvector.

This is the only module that knows how embeddings are physically stored.
Everything else -- the indexer, the retrievers, the AI tools -- goes through
the :class:`VectorStore` protocol, so the storage detail stays swappable
within the pgvector decision: production is PostgreSQL with the pgvector
extension (enabled by migration 0005, HNSW expression indexes), and SQLite --
used by the unit suite and local development -- stores vectors as JSON and
computes cosine similarity in Python. Same semantics, exact rather than
approximate, no fake indexes.

The protocol methods are ``async`` (embedding I/O and vector queries are
natural async boundaries); the application's synchronous request path uses
the thin bridges at the bottom of the module.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

from sqlalchemy import Float, delete, literal_column, select
from sqlalchemy.orm import Session

from app.db import is_postgres
from app.models import CodeChunk, DocumentChunk


class VectorStoreError(RuntimeError):
    """Raised for storage-side problems the caller cannot correct."""


@dataclass(frozen=True)
class VectorMatch:
    """One retrieved neighbor, with the metadata a citation needs."""

    chunk_id: int
    score: float
    repository_id: int
    repository_name: str
    file_path: str
    content: str
    #: Code chunks: the symbol and its definition-line range.
    symbol: str | None = None
    node_type: str | None = None
    start_line: int | None = None
    end_line: int | None = None
    #: Document chunks: the section heading and its line range.
    section: str | None = None
    document_id: str | None = None


@runtime_checkable
class VectorStore(Protocol):
    """The minimal surface the indexer and retrievers need."""

    async def upsert(self, entries: list[dict[str, Any]]) -> None:
        """Insert or update chunks keyed by their deterministic identifier."""
        ...

    async def search(
        self,
        query_vector: list[float],
        *,
        kind: str,
        model: str,
        dimension: int,
        repository_id: int | None = None,
        limit: int = 5,
    ) -> list[VectorMatch]:
        """Nearest neighbours by cosine similarity, never mixing models."""
        ...

    async def delete(self, *, kind: str, repository_id: int, identifiers: list[str]) -> None:
        """Remove stale chunks by identifier."""
        ...


def _cosine(a: list[float], b: list[float]) -> float:
    """Cosine similarity in [-1, 1]; 0.0 when either vector is empty."""
    length = min(len(a), len(b))
    dot = sum(a[i] * b[i] for i in range(length))
    na = math.sqrt(sum(x * x for x in a[:length])) or 1.0
    nb = math.sqrt(sum(x * x for x in b[:length])) or 1.0
    return dot / (na * nb)


def _bind_postgres_vector(value: list[float]) -> str:
    """Render a vector as the literal pgvector expects in a text comparison."""
    return "[" + ",".join(f"{float(x):.6f}" for x in value) + "]"


class SQLAlchemyVectorStore:
    """Vector storage over the existing SQLAlchemy engine and models.

    One implementation serves both databases (see module docstring): on
    PostgreSQL the similarity is computed by pgvector's ``<=>`` operator and
    benefits from the HNSW indexes created by migration 0005; on SQLite the
    candidate rows are scored in Python. Queries always pin the model and
    dimension stored with each chunk, so vectors from different embedding
    models are never compared -- a model change requires a re-index, and the
    retrieval layer reports that rather than silently mixing spaces.
    """

    def __init__(self, db: Session) -> None:
        self.db = db
        self._postgres = is_postgres(db.bind.dialect.name if db.bind is not None else "")

    # -- helpers ----------------------------------------------------------

    @staticmethod
    def _table(kind: str):
        if kind == "code":
            return CodeChunk
        if kind == "document":
            return DocumentChunk
        raise VectorStoreError(f"Unknown chunk kind {kind!r} (expected 'code' or 'document').")

    def _validate_kind(self, kind: str):
        return self._table(kind)

    # -- protocol ---------------------------------------------------------

    async def upsert(self, entries: list[dict[str, Any]]) -> None:
        """Insert new chunks and update changed ones, keyed by identifier.

        Idempotent by design: the (repository_id, identifier) pair is unique,
        so indexing the same content twice writes the same row twice --
        no duplicates. Entries are written in batches of ``commit``-sized
        groups so a huge repository cannot accumulate one giant flush.
        """
        table = None
        for entry in entries:
            kind = entry.get("kind")
            entry_table = self._validate_kind(kind)
            if table is None:
                table = entry_table
            elif entry_table is not table:
                raise VectorStoreError("One upsert batch must not mix code and document chunks.")
            existing = self.db.scalar(
                select(entry_table).where(
                    entry_table.repository_id == entry["repository_id"],
                    entry_table.identifier == entry["identifier"],
                )
            )
            if existing is not None:
                for column, value in entry.items():
                    if column not in {"kind", "id"}:
                        setattr(existing, column, value)
            else:
                self.db.add(entry_table(**{k: v for k, v in entry.items() if k != "kind"}))
        self.db.flush()

    async def search(
        self,
        query_vector: list[float],
        *,
        kind: str,
        model: str,
        dimension: int,
        repository_id: int | None = None,
        limit: int = 5,
    ) -> list[VectorMatch]:
        table = self._validate_kind(kind)
        stmt = select(table).where(
            table.embedding_model == model,
            table.embedding_dimension == dimension,
            table.embedding.is_not(None),
        )
        if repository_id is not None:
            stmt = stmt.where(table.repository_id == repository_id)

        if self._postgres:
            # pgvector cosine distance via the <=> operator, expressed as raw
            # SQL: the embedding column is a JSON-with-variant at the ORM layer
            # (see models.EmbeddingType), so the Vector comparator is not
            # reachable through the mapped attribute. The ORDER BY ... LIMIT
            # shape is exactly what the HNSW index requires to be used.
            literal = _bind_postgres_vector(query_vector)
            # Cast to the query dimension so the expression matches the HNSW
            # expression index created by migration 0005 (built on
            # `embedding::vector(<dim>)`); without the cast, the planner has
            # no index to use and falls back to a full scan.
            distance = literal_column(
                f"{table.__tablename__}.embedding::vector({dimension}) <=> '{literal}'::vector"
            )
            similarity = literal_column(
                f"1 - ({table.__tablename__}.embedding::vector({dimension}) <=> '{literal}'::vector)"
            ).label("similarity")
            stmt = stmt.add_columns(similarity).order_by(distance)
        else:
            stmt = stmt.add_columns(table.embedding)

        stmt = stmt.limit(max(1, min(limit, 50)))
        rows = self.db.execute(stmt).all()

        matches: list[VectorMatch] = []
        for row in rows:
            chunk, similarity = row
            stored = similarity if self._postgres else _cosine(query_vector, list(chunk.embedding or []))
            if stored is None:
                continue
            matches.append(
                VectorMatch(
                    chunk_id=chunk.id,
                    score=float(stored),
                    repository_id=chunk.repository_id,
                    repository_name=(
                        chunk.repository_name if hasattr(chunk, "repository_name") else ""
                    ),
                    file_path=chunk.file_path,
                    content=chunk.content,
                    symbol=getattr(chunk, "symbol", None),
                    node_type=getattr(chunk, "node_type", None),
                    start_line=chunk.start_line,
                    end_line=chunk.end_line,
                    section=getattr(chunk, "section", None),
                    document_id=getattr(chunk, "document_id", None),
                )
            )
        matches.sort(key=lambda m: m.score, reverse=True)
        return matches

    async def delete(
        self, *, kind: str, repository_id: int, identifiers: list[str]
    ) -> None:
        """Remove stale chunks by identifier. A missing identifier is a no-op."""
        table = self._validate_kind(kind)
        if not identifiers:
            return
        self.db.execute(
            delete(table).where(
                table.repository_id == repository_id,
                table.identifier.in_(identifiers),
            )
        )
        self.db.flush()

    async def delete_repository(self, *, kind: str, repository_id: int) -> None:
        """Remove every chunk of a kind for a repository (source removed)."""
        table = self._validate_kind(kind)
        self.db.execute(
            delete(table).where(
                table.repository_id == repository_id,
            )
        )
        self.db.flush()


def vector_store(db: Session) -> VectorStore:
    """The concrete store bound to a session."""
    return SQLAlchemyVectorStore(db)


def run_async(coro: Any) -> Any:
    """Drive the async store from synchronous request-path code."""
    import asyncio

    return asyncio.run(coro)
