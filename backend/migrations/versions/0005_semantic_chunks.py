"""Semantic indexing: pgvector extension plus code/document chunk tables.

Enables PostgreSQL's ``pgvector`` extension and creates the two tables that
hold AST-aware code chunks (Tree-sitter nodes) and section-aligned
documentation chunks, each with an untyped ``vector`` embedding column.

Why untyped (``vector`` without a dimension) rather than ``vector(768)``:
the embedding dimension is a property of the configured *model*, not of the
schema. Code embeddings (Jina Code Embeddings 1.5B) are 768-d and document
embeddings (BAAI/bge-m3) are 1024-d, and changing a model must not require a
column rewrite -- it requires a re-index, which the application detects from
the per-row ``embedding_model``/``embedding_dimension`` columns. Because
pgvector cannot build an ANN index directly on an untyped column, the
similarity indexes here are HNSW *expression* indexes casting to the models'
dimensions as configured at migration time; the application re-ensures them at
startup for the currently configured dimensions (see services/vector_store.py).
Queries without a matching index still work -- they fall back to an exact
scan -- so a dimension change degrades gracefully instead of failing.

SQLite: this migration is guarded. SQLite has no extensions and no vector
type; the unit-test schema there is derived from the models, where the
embedding column is a JSON variant. Only the plain tables and indexes are
created, which is everything SQLite needs to run exact similarity search in
Python (see services/vector_store.py).

Revision ID: 0005_semantic_chunks
Revises: 0004_repository_sources
Create Date: 2026-10-04
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "0005_semantic_chunks"
down_revision = "0004_repository_sources"
branch_labels = None
depends_on = None

# Dimensions of the default embedding models (Jina Code Embeddings 1.5B for
# code, BAAI/bge-m3 for documentation). The application re-ensures indexes
# for whatever is actually configured at runtime.
_CODE_DIM = 768
_DOCUMENT_DIM = 1024


def _is_postgres() -> bool:
    bind = op.get_bind()
    return bind.dialect.name in {"postgres", "postgresql"}


def _code_columns() -> list[sa.Column]:
    return [
        sa.Column("repository_id", sa.Integer(), nullable=False),
        sa.Column("repository_name", sa.String(length=200), nullable=False),
        sa.Column("file_path", sa.String(length=1000), nullable=False),
        sa.Column("language", sa.String(length=50), nullable=False),
        sa.Column("node_type", sa.String(length=100), nullable=False),
        sa.Column("symbol", sa.String(length=300), nullable=False),
        sa.Column("signature", sa.Text(), server_default="", nullable=False),
        sa.Column("start_line", sa.Integer(), nullable=False),
        sa.Column("end_line", sa.Integer(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("enriched_content", sa.Text(), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("identifier", sa.String(length=1200), nullable=False),
        sa.Column("embedding_model", sa.String(length=200), nullable=False),
        sa.Column("embedding_dimension", sa.Integer(), nullable=False),
    ]


def _document_columns() -> list[sa.Column]:
    return [
        sa.Column("repository_id", sa.Integer(), nullable=False),
        sa.Column("document_id", sa.String(length=1200), nullable=False),
        sa.Column("file_path", sa.String(length=1000), nullable=False),
        sa.Column("section", sa.String(length=300), server_default="", nullable=False),
        sa.Column("start_line", sa.Integer(), nullable=False),
        sa.Column("end_line", sa.Integer(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("identifier", sa.String(length=1200), nullable=False),
        sa.Column("embedding_model", sa.String(length=200), nullable=False),
        sa.Column("embedding_dimension", sa.Integer(), nullable=False),
    ]


def _embedding_column() -> sa.Column:
    """``vector`` on PostgreSQL; JSON on SQLite (see module docstring)."""
    if _is_postgres():
        from pgvector.sqlalchemy import Vector

        return sa.Column("embedding", Vector(), nullable=True)
    return sa.Column("embedding", sa.JSON(), nullable=True)


def _timestamps() -> list[sa.Column]:
    return [
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
    ]


def upgrade() -> None:
    if _is_postgres():
        # The extension is a prerequisite for the vector type. IF NOT EXISTS
        # keeps the migration re-runnable against a database that already
        # has it (shared clusters commonly do).
        op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    op.create_table(
        "code_chunks",
        sa.Column("id", sa.Integer(), primary_key=True),
        *_code_columns(),
        _embedding_column(),
        *_timestamps(),
        sa.ForeignKeyConstraint(["repository_id"], ["repositories.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("repository_id", "identifier", name="uq_code_chunks_identifier"),
    )
    op.create_index("ix_code_chunks_repository_id", "code_chunks", ["repository_id"])
    op.create_index(
        "ix_code_chunks_repository_file", "code_chunks", ["repository_id", "file_path"]
    )
    op.create_index(
        "ix_code_chunks_content_hash", "code_chunks", ["repository_id", "content_hash"]
    )
    op.create_index(
        "ix_code_chunks_model",
        "code_chunks",
        ["repository_id", "embedding_model", "embedding_dimension"],
    )

    op.create_table(
        "document_chunks",
        sa.Column("id", sa.Integer(), primary_key=True),
        *_document_columns(),
        _embedding_column(),
        *_timestamps(),
        sa.ForeignKeyConstraint(["repository_id"], ["repositories.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("repository_id", "identifier", name="uq_document_chunks_identifier"),
    )
    op.create_index("ix_document_chunks_repository_id", "document_chunks", ["repository_id"])
    op.create_index(
        "ix_document_chunks_repository_file", "document_chunks", ["repository_id", "file_path"]
    )
    op.create_index(
        "ix_document_chunks_content_hash", "document_chunks", ["repository_id", "content_hash"]
    )
    op.create_index(
        "ix_document_chunks_model",
        "document_chunks",
        ["repository_id", "embedding_model", "embedding_dimension"],
    )

    if _is_postgres():
        # HNSW expression indexes: pgvector cannot index an untyped column
        # directly, so each index casts to the dimension its model uses.
        # (A plain index on `embedding` would fail with "column does not have
        # dimensions"; the cast both type-checks and gives the planner the
        # fixed dimension an ANN index requires.)
        op.execute(
            f"CREATE INDEX ix_code_chunks_embedding_hnsw ON code_chunks "
            f"USING hnsw ((embedding::vector({_CODE_DIM})) vector_cosine_ops)"
        )
        op.execute(
            f"CREATE INDEX ix_document_chunks_embedding_hnsw ON document_chunks "
            f"USING hnsw ((embedding::vector({_DOCUMENT_DIM})) vector_cosine_ops)"
        )


def downgrade() -> None:
    if _is_postgres():
        op.execute("DROP INDEX IF EXISTS ix_document_chunks_embedding_hnsw")
        op.execute("DROP INDEX IF EXISTS ix_code_chunks_embedding_hnsw")
    op.drop_index("ix_document_chunks_model", table_name="document_chunks")
    op.drop_index("ix_document_chunks_content_hash", table_name="document_chunks")
    op.drop_index("ix_document_chunks_repository_file", table_name="document_chunks")
    op.drop_index("ix_document_chunks_repository_id", table_name="document_chunks")
    op.drop_table("document_chunks")
    op.drop_index("ix_code_chunks_model", table_name="code_chunks")
    op.drop_index("ix_code_chunks_content_hash", table_name="code_chunks")
    op.drop_index("ix_code_chunks_repository_file", table_name="code_chunks")
    op.drop_index("ix_code_chunks_repository_id", table_name="code_chunks")
    op.drop_table("code_chunks")
    # The extension itself is left installed: other databases on the same
    # cluster may depend on it, and dropping it would take their data with it.
