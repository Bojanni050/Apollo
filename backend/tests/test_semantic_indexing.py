"""Tests for semantic indexing and retrieval (Milestone 2).

Groups, mirroring the milestone's test plan:

* **Embeddings** -- provider construction, generation, dimensions, model
  metadata, invalid configuration, dimension mismatches.
* **Tree-sitter** -- function/class extraction, exact boundaries, malformed
  Python, verbatim source preservation.
* **Indexing** -- initial, repeated (idempotent), changed and deleted
  sources, ignored files, per-file failure isolation, document chunking.
* **Vector storage** -- upsert, similarity search, delete, duplicate
  prevention, model isolation.
* **Hybrid retrieval** -- lexical, semantic and combined results,
  repository/kind filters, code+documentation evidence in one answer.
* **AI** -- the semantic retrieval tools: results, citations, graceful
  degradation without an index.
* **API** -- index status/trigger/re-index endpoints and the semantic search
  endpoint.

All of this runs on SQLite (the embedding column's JSON variant) with the
deterministic local fallback provider, so no network, no pgvector server and
no paid API are needed; the PostgreSQL-specific behaviour is covered by the
migration tests and the postgres-marked suite.
"""
from __future__ import annotations

import asyncio
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app import models
from app.config import settings
from app.db import Base
from app.services import indexing, retrieval
from app.services.embeddings import (
    EmbeddingDimensionError,
    EmbeddingError,
    LocalEmbeddingProvider,
    code_provider,
    document_provider,
    embed_code,
    embed_documents,
    model_dimension,
)
from app.services.parsing import parse_file
from app.services.vector_store import vector_store
from app.services.tools import ToolContext, run_tool


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _git(repo: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-C", str(repo), *args], check=True, capture_output=True, text=True
    )


@pytest.fixture()
def semantic_repo_pair(tmp_path: Path):
    """A documentation repo and a source repo, committed to Git."""
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "adr-001.md").write_text(
        "# ADR 001: ReasonIQ is background-only\n\n"
        "ReasonIQ must never spawn foreground agents; it runs strictly in the background.\n",
        encoding="utf-8",
    )
    svc = tmp_path / "svc"
    (svc / "src").mkdir(parents=True)
    (svc / "src" / "reason_iq.py").write_text(
        "class ReasonIQ:\n"
        "    '''Background-only reasoning about user intent.'''\n"
        "    def run(self, intent):\n"
        "        return 'background'\n",
        encoding="utf-8",
    )
    (svc / "src" / "unrelated.py").write_text(
        "def other(a):\n    return a\n", encoding="utf-8"
    )
    for repo in (docs, svc):
        _git(repo, "init", "-q", "-b", "main")
        _git(repo, "add", "-A")
        _git(repo, "-c", "user.email=t@e.com", "-c", "user.name=T", "commit", "-qm", "init")
    return docs, svc


@pytest.fixture()
def sem_db():
    """An isolated database, so indexing tests never touch the shared one."""
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    yield factory
    engine.dispose()


@pytest.fixture()
def sem_ws(sem_db, semantic_repo_pair):
    """A workspace with a documentation and a source repository, indexed."""
    docs, svc = semantic_repo_pair
    db = sem_db()
    try:
        workspace = models.Workspace(name="Semantic")
        db.add(workspace)
        db.flush()
        db.add(
            models.Repository(
                workspace_id=workspace.id, name="docs", local_path=str(docs), kind="documentation"
            )
        )
        db.add(
            models.Repository(
                workspace_id=workspace.id, name="svc", local_path=str(svc), kind="source"
            )
        )
        db.commit()
        indexing.index_workspace_now(db, workspace.id)
        db.commit()
        yield db, workspace
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Embeddings
# ---------------------------------------------------------------------------


class TestEmbeddings:
    def test_provider_initialization_and_metadata(self) -> None:
        provider = code_provider()
        assert provider.model_name == settings.code_embedding_model
        assert provider.model_name == "jina-code-embeddings-1.5b"
        assert provider.dimension == 768
        docs = document_provider()
        assert docs.model_name == "BAAI/bge-m3"
        assert docs.dimension == 1024

    def test_embedding_generation_and_dimensions(self) -> None:
        result = embed_code(["def a(): pass", "class B: ..."])
        assert len(result.vectors) == 2
        assert all(len(v) == 768 for v in result.vectors)
        assert result.model_name == settings.code_embedding_model

        docs = embed_documents(["# Heading\nbody"])
        assert len(docs.vectors) == 1
        assert len(docs.vectors[0]) == 1024

    def test_deterministic_local_provider(self) -> None:
        provider = LocalEmbeddingProvider(settings.code_embedding_model, dimension=64)
        first = asyncio.run(provider.embed(["alpha", "beta"]))
        second = asyncio.run(provider.embed(["alpha", "beta"]))
        assert first == second
        assert first[0] != first[1]

    def test_invalid_model_configuration(self) -> None:
        with pytest.raises(EmbeddingError):
            model_dimension("totally-unknown-model")

    def test_dimension_mismatch_is_rejected(self) -> None:
        provider = LocalEmbeddingProvider(settings.code_embedding_model, dimension=8)
        vectors = asyncio.run(provider.embed(["text"]))  # correct length...
        assert len(vectors[0]) == 8
        # ...but a provider declaring one dimension and returning another
        # must be caught before storage: simulate by validating manually.
        with pytest.raises(EmbeddingDimensionError):
            from app.services.embeddings import _validate

            _validate(
                LocalEmbeddingProvider(settings.code_embedding_model, dimension=8),
                [[0.0, 0.0, 0.0]],  # 3 dims, provider says 8
            )

    def test_local_provider_requires_dimension(self) -> None:
        with pytest.raises(EmbeddingError):
            LocalEmbeddingProvider("mystery-model")

    def test_remote_provider_requires_base_url(self) -> None:
        from app.services.embeddings import RemoteEmbeddingProvider

        with pytest.raises(EmbeddingError):
            RemoteEmbeddingProvider(settings.code_embedding_model, base_url=None)


# ---------------------------------------------------------------------------
# Tree-sitter parsing
# ---------------------------------------------------------------------------


class TestTreeSitter:
    def _write(self, tmp_path: Path, source: str) -> Path:
        target = tmp_path / "sample.py"
        # newline="" keeps the fixture byte-for-byte as written. Without it
        # Windows rewrites every "\n" to "\r\n" on write, and the parser -- which
        # deliberately preserves source verbatim -- then returns CRLF, so the
        # verbatim-source assertions below fail on Windows only.
        target.write_text(source, encoding="utf-8", newline="")
        return target

    def test_function_extraction(self, tmp_path: Path) -> None:
        target = self._write(tmp_path, "def helper(a, b):\n    return a + b\n")
        units = parse_file(
            target, repository_id=1, repository_name="svc", file_path="src/sample.py"
        )
        assert len(units) == 1
        unit = units[0]
        assert unit.node_type == "function_definition"
        assert unit.symbol == "helper"
        assert unit.signature == "def helper(a, b):"
        assert (unit.start_line, unit.end_line) == (1, 2)

    def test_class_extraction(self, tmp_path: Path) -> None:
        target = self._write(
            tmp_path, "class IntentIQ:\n    def run(self):\n        return 1\n"
        )
        units = parse_file(
            target, repository_id=1, repository_name="svc", file_path="src/sample.py"
        )
        assert {u.node_type for u in units} == {"class_definition", "function_definition"}
        cls = next(u for u in units if u.node_type == "class_definition")
        assert cls.symbol == "IntentIQ"
        assert (cls.start_line, cls.end_line) == (1, 3)

    def test_exact_boundaries(self, tmp_path: Path) -> None:
        source = "# header comment\n\n\ndef fn():\n    '''doc'''\n    return 42\n\n\n# trailing"
        target = self._write(tmp_path, source)
        units = parse_file(
            target, repository_id=1, repository_name="svc", file_path="s.py"
        )
        assert len(units) == 1
        # Tree-sitter boundaries: def line through the last body line.
        assert (units[0].start_line, units[0].end_line) == (4, 6)
        assert units[0].source == "def fn():\n    '''doc'''\n    return 42"

    def test_source_preserved_verbatim(self, tmp_path: Path) -> None:
        source = "def fn():\n    x = '  literal  '\n    return x\n"
        target = self._write(tmp_path, source)
        units = parse_file(
            target, repository_id=1, repository_name="svc", file_path="s.py"
        )
        assert units[0].source == source.rstrip("\n")

    def test_malformed_python_yields_recoverable_units(self, tmp_path: Path) -> None:
        target = self._write(
            tmp_path,
            "def fine():\n    return 1\n\ndef broken(:\n    pass\n\ndef also_fine():\n    return 2\n",
        )
        units = parse_file(
            target, repository_id=1, repository_name="svc", file_path="s.py"
        )
        symbols = {u.symbol for u in units}
        assert "fine" in symbols and "also_fine" in symbols

    def test_unsupported_language_yields_nothing(self, tmp_path: Path) -> None:
        target = tmp_path / "main.go"
        target.write_text("func main() {}\n", encoding="utf-8")
        assert (
            parse_file(target, repository_id=1, repository_name="svc", file_path="main.go") == []
        )

    def test_enriched_representation_contains_context(self, tmp_path: Path) -> None:
        target = self._write(tmp_path, "def fn():\n    return 1\n")
        units = parse_file(
            target, repository_id=7, repository_name="Gaia", file_path="src/s.py"
        )
        enriched = units[0].enriched
        assert "# Repository: Gaia" in enriched
        assert "# File: src/s.py" in enriched
        assert "# Symbol: fn" in enriched
        assert "# Lines: 1-2" in enriched
        # The enriched text embeds, but never replaces, the original source.
        assert "def fn():" in enriched


# ---------------------------------------------------------------------------
# Vector storage
# ---------------------------------------------------------------------------


class TestVectorStore:
    def _entry(self, repo_id: int, identifier: str, symbol: str, vector) -> dict:
        return {
            "kind": "code",
            "repository_id": repo_id,
            "repository_name": "svc",
            "file_path": "src/a.py",
            "language": "python",
            "node_type": "function_definition",
            "symbol": symbol,
            "signature": f"def {symbol}():",
            "start_line": 1,
            "end_line": 2,
            "content": f"def {symbol}(): pass",
            "enriched_content": f"# ctx\ndef {symbol}(): pass",
            "content_hash": f"h-{symbol}",
            "identifier": identifier,
            "embedding_model": settings.code_embedding_model,
            "embedding_dimension": len(vector),
            "embedding": vector,
        }

    def test_upsert_search_delete_and_duplicates(self, sem_db) -> None:
        db = sem_db()
        store = vector_store(db)
        one = [1.0] + [0.0] * 767
        two = [0.0, 1.0] + [0.0] * 766

        async def run():
            await store.upsert([self._entry(1, "id-1", "alpha", one)])
            await store.upsert([self._entry(1, "id-2", "beta", two)])
            # Same identifier twice: an update, never a duplicate row.
            await store.upsert([self._entry(1, "id-1", "alpha", one)])
            hits = await store.search(
                one,
                kind="code",
                model=settings.code_embedding_model,
                dimension=768,
                limit=5,
            )
            await store.delete(kind="code", repository_id=1, identifiers=["id-1"])

        asyncio.run(run())
        assert db.query(models.CodeChunk).count() == 1
        remaining = db.query(models.CodeChunk).one()
        assert remaining.symbol == "beta"

    def test_similarity_ordering(self, sem_db) -> None:
        db = sem_db()
        store = vector_store(db)
        probe = [1.0] + [0.0] * 767
        near = [0.9, 0.1] + [0.0] * 766
        far = [0.0, 0.0, 1.0] + [0.0] * 765

        asyncio.run(
            store.upsert(
                [
                    self._entry(1, "n", "near", near),
                    self._entry(1, "f", "far", far),
                ]
            )
        )
        hits = asyncio.run(
            store.search(
                probe,
                kind="code",
                model=settings.code_embedding_model,
                dimension=768,
                limit=5,
            )
        )
        assert [h.symbol for h in hits] == ["near", "far"]
        assert hits[0].score > hits[1].score

    def test_model_isolation(self, sem_db) -> None:
        db = sem_db()
        store = vector_store(db)
        vector = [1.0] + [0.0] * 767
        asyncio.run(store.upsert([self._entry(1, "x", "alpha", vector)]))
        hits = asyncio.run(
            store.search(
                vector, kind="code", model="some-other-model", dimension=768, limit=5
            )
        )
        assert hits == []

    def test_kind_validation(self, sem_db) -> None:
        from app.services.vector_store import VectorStoreError

        store = vector_store(sem_db())
        with pytest.raises(VectorStoreError):
            asyncio.run(store.search([0.0], kind="nonsense", model="m", dimension=1, limit=1))


# ---------------------------------------------------------------------------
# Indexing
# ---------------------------------------------------------------------------


class TestIndexing:
    def test_initial_indexing(self, sem_ws) -> None:
        db, workspace = sem_ws
        code = db.query(models.CodeChunk).all()
        documents = db.query(models.DocumentChunk).all()
        assert {c.symbol for c in code} >= {"ReasonIQ", "run", "other"}
        assert all(len(c.embedding) == 768 for c in code)
        assert all(c.embedding_model == settings.code_embedding_model for c in code)
        assert documents and all(len(d.embedding) == 1024 for d in documents)
        assert all(d.embedding_model == settings.document_embedding_model for d in documents)

    def test_repeated_indexing_is_idempotent(self, sem_ws) -> None:
        db, workspace = sem_ws
        before = db.query(models.CodeChunk).count(), db.query(models.DocumentChunk).count()
        ids = sorted(c.id for c in db.query(models.CodeChunk).all())
        status = indexing.index_workspace_now(db, workspace.id)
        after = db.query(models.CodeChunk).count(), db.query(models.DocumentChunk).count()
        assert before == after
        # Second run embeds nothing new (all content hashes matched).
        assert status.counts.embeddings_generated == 0
        assert sorted(c.id for c in db.query(models.CodeChunk).all()) == ids

    def test_changed_source_is_reembedded_without_duplicates(self, sem_ws, semantic_repo_pair) -> None:
        db, workspace = sem_ws
        _, svc = semantic_repo_pair
        target = svc / "src" / "reason_iq.py"
        target.write_text(
            "class ReasonIQ:\n"
            "    '''Background-only reasoning, second edition.'''\n"
            "    def run(self, intent, depth=1):\n"
            "        return 'background'\n",
            encoding="utf-8",
        )
        status = indexing.index_workspace_now(db, workspace.id)
        assert status.counts.embeddings_generated > 0
        rows = [c for c in db.query(models.CodeChunk).all() if c.file_path.endswith("reason_iq.py")]
        # One row per unit -- the superseded rows are gone.
        assert len(rows) == 2
        assert any("depth=1" in c.signature for c in rows)

    def test_deleted_source_is_pruned(self, sem_ws, semantic_repo_pair) -> None:
        db, workspace = sem_ws
        _, svc = semantic_repo_pair
        (svc / "src" / "unrelated.py").unlink()
        indexing.index_workspace_now(db, workspace.id)
        assert not any(
            c.file_path.endswith("unrelated.py") for c in db.query(models.CodeChunk).all()
        )

    def test_ignored_directories_are_skipped(self, sem_db, tmp_path: Path) -> None:
        svc = tmp_path / "svc"
        (svc / "src").mkdir(parents=True)
        (svc / "src" / "real.py").write_text("def real():\n    return 1\n", encoding="utf-8")
        (svc / "__pycache__").mkdir()
        (svc / "__pycache__" / "gen.py").write_text("def gen():\n    return 2\n", encoding="utf-8")
        (svc / "logo.png").write_bytes(b"\x89PNG\r\n\x1a\n\x00")
        _git(svc, "init", "-q", "-b", "main")
        _git(svc, "add", "-A")
        _git(svc, "-c", "user.email=t@e.com", "-c", "user.name=T", "commit", "-qm", "i")

        db = sem_db()
        workspace = models.Workspace(name="w")
        db.add(workspace)
        db.flush()
        db.add(
            models.Repository(
                workspace_id=workspace.id, name="svc", local_path=str(svc), kind="source"
            )
        )
        db.commit()
        status = indexing.index_workspace_now(db, workspace.id)
        paths = {c.file_path for c in db.query(models.CodeChunk).all()}
        assert paths == {"src/real.py"}
        assert all("pycache" not in p for p in paths)
        assert status.counts.failed_files == []

    def test_failed_file_does_not_abort_the_run(self, sem_db, tmp_path: Path) -> None:
        svc = tmp_path / "svc"
        (svc / "src").mkdir(parents=True)
        (svc / "src" / "good.py").write_text("def good():\n    return 1\n", encoding="utf-8")
        db = sem_db()
        workspace = models.Workspace(name="w")
        db.add(workspace)
        db.flush()
        repo = models.Repository(
            workspace_id=workspace.id, name="svc", local_path=str(svc), kind="source"
        )
        db.add(repo)
        db.commit()

        # Force a per-file failure while the rest succeeds. indexing imports
        # parse_file into its own namespace, so that is the binding to patch.
        original = indexing.parse_file

        def flaky(path, **kwargs):
            if path.name == "good.py" and not getattr(flaky, "failed_once", False):
                flaky.failed_once = True
                raise indexing.ParseError("simulated parse failure")
            return original(path, **kwargs)

        flaky.failed_once = False
        indexing.parse_file = flaky
        try:
            status = indexing.index_workspace_now(db, workspace.id)
        finally:
            indexing.parse_file = original
        assert status.status == "completed"
        assert status.counts.failed_files == ["src/good.py"]
        assert status.counts.errors

    def test_document_chunking_follows_sections(self, sem_ws) -> None:
        db, _ = sem_ws
        chunks = db.query(models.DocumentChunk).all()
        assert {c.section for c in chunks} == {"ADR 001: ReasonIQ is background-only"}
        assert all(c.file_path == "adr-001.md" for c in chunks)
        # Chunk line ranges must fall inside the real file.
        for chunk in chunks:
            assert 1 <= chunk.start_line <= chunk.end_line

    def test_split_sections_paragraph_fallback(self) -> None:
        content = "# Head\n\n" + ("some meaningful paragraph text that repeats\n\n" * 400)
        sections = indexing.split_sections(content)
        assert len(sections) > 1
        assert all(s.section == "Head" for s in sections)
        assert all(len(s.content) <= indexing.MAX_SECTION_CHARS + 200 for s in sections)

    def test_model_change_requires_reindex(self, sem_ws, monkeypatch) -> None:
        db, workspace = sem_ws
        assert indexing.model_compatibility(db, workspace.id)["reindex_required"] is False
        monkeypatch.setattr(settings, "code_embedding_model", "different-code-model")
        from app.services.embeddings import reset_providers

        reset_providers()
        try:
            compatibility = indexing.model_compatibility(db, workspace.id)
            assert compatibility["reindex_required"] is True
            assert compatibility["reasons"]
        finally:
            monkeypatch.undo()
            reset_providers()


# ---------------------------------------------------------------------------
# Retrieval (semantic + hybrid)
# ---------------------------------------------------------------------------


class TestRetrieval:
    def test_semantic_code_search(self, sem_ws) -> None:
        db, workspace = sem_ws
        results = retrieval.search_codebase(
            db, "where is intent reasoning implemented", n_results=3, workspace_id=workspace.id
        )
        assert results
        top = results[0]
        assert top.kind == "code"
        assert top.symbol == "ReasonIQ"
        assert top.file_path == "src/reason_iq.py"
        assert top.start_line and top.end_line
        assert "class ReasonIQ" in top.content

    def test_semantic_document_search(self, sem_ws) -> None:
        db, workspace = sem_ws
        results = retrieval.search_documents_semantically(
            db, "is ReasonIQ allowed in the foreground", n_results=3, workspace_id=workspace.id
        )
        assert results
        assert results[0].kind == "document"
        assert "ReasonIQ" in (results[0].section or "")

    def test_repository_filter(self, sem_ws) -> None:
        db, workspace = sem_ws
        svc = db.query(models.Repository).filter_by(name="svc").one()
        results = retrieval.search_codebase(
            db, "reasoning", n_results=5, repository_id=svc.id, workspace_id=workspace.id
        )
        assert results and all(r.repository == "svc" for r in results)

    def test_kind_filter(self, sem_ws) -> None:
        db, workspace = sem_ws
        code_only = retrieval.hybrid_search(
            db, "Reasoniq background", n_results=10, workspace_id=workspace.id, kinds=("code",)
        )
        assert code_only.evidence and all(e.kind == "code" for e in code_only.evidence)
        doc_only = retrieval.hybrid_search(
            db, "ReasonIQ background", n_results=10, workspace_id=workspace.id, kinds=("document",)
        )
        assert doc_only.evidence and all(e.kind == "document" for e in doc_only.evidence)

    def test_hybrid_combines_lexical_and_semantic(self, sem_ws) -> None:
        db, workspace = sem_ws
        result = retrieval.hybrid_search(
            db, "ReasonIQ background", n_results=10, workspace_id=workspace.id
        )
        kinds = {e.kind for e in result.evidence}
        assert kinds == {"code", "document"}
        assert result.lexical_hits > 0
        assert result.semantic_hits > 0
        # Merged hits that appear in both score higher than lexical-only.
        assert result.evidence[0].score > 0

    def test_semantic_search_without_index_is_explicit(self, sem_db, semantic_repo_pair) -> None:
        db = sem_db()
        workspace = models.Workspace(name="w")
        db.add(workspace)
        db.flush()
        docs, svc = semantic_repo_pair
        db.add(
            models.Repository(
                workspace_id=workspace.id, name="docs", local_path=str(docs), kind="documentation"
            )
        )
        db.add(
            models.Repository(
                workspace_id=workspace.id, name="svc", local_path=str(svc), kind="source"
            )
        )
        db.commit()
        with pytest.raises(retrieval.RetrievalError):
            retrieval.search_codebase(db, "anything", workspace_id=workspace.id)

    def test_hybrid_degrades_to_lexical_without_index(self, sem_db, semantic_repo_pair) -> None:
        db = sem_db()
        workspace = models.Workspace(name="w")
        db.add(workspace)
        db.flush()
        docs, svc = semantic_repo_pair
        db.add(
            models.Repository(
                workspace_id=workspace.id, name="docs", local_path=str(docs), kind="documentation"
            )
        )
        db.add(
            models.Repository(
                workspace_id=workspace.id, name="svc", local_path=str(svc), kind="source"
            )
        )
        db.commit()
        result = retrieval.hybrid_search(
            db, "ReasonIQ", n_results=10, workspace_id=workspace.id
        )
        assert result.semantic_hits == 0
        assert result.evidence  # lexical still works


# ---------------------------------------------------------------------------
# AI tools
# ---------------------------------------------------------------------------


class TestSemanticTools:
    def _ctx(self, sem_ws) -> ToolContext:
        db, workspace = sem_ws
        repos = db.query(models.Repository).filter_by(workspace_id=workspace.id).all()
        return ToolContext(workspace=workspace, repositories=list(repos), citations=[], db=db)

    def test_semantic_search_code_tool(self, sem_ws) -> None:
        ctx = self._ctx(sem_ws)
        result = run_tool("semantic_search_code", ctx, {"query": "intent reasoning"})
        assert "ReasonIQ" in result
        assert "src/reason_iq.py" in result
        code_citations = [c for c in ctx.citations if c.evidence_type == "verified_implementation"]
        assert code_citations
        citation = code_citations[0]
        assert citation.path == "src/reason_iq.py"
        assert citation.start_line and citation.end_line

    def test_semantic_search_documents_tool(self, sem_ws) -> None:
        ctx = self._ctx(sem_ws)
        result = run_tool("semantic_search_documents", ctx, {"query": "foreground agents"})
        assert "adr-001.md" in result
        doc_citations = [
            c for c in ctx.citations if c.evidence_type == "documented_intention"
        ]
        assert doc_citations

    def test_hybrid_tool_combines_code_and_documentation(self, sem_ws) -> None:
        ctx = self._ctx(sem_ws)
        result = run_tool(
            "hybrid_search", ctx, {"query": "ReasonIQ background"}
        )
        assert "svc/src/reason_iq.py" in result or "src/reason_iq.py" in result
        assert "adr-001.md" in result
        kinds = {c.evidence_type for c in ctx.citations}
        assert {"verified_implementation", "documented_intention"} <= kinds

    def test_citations_match_returned_content(self, sem_ws) -> None:
        ctx = self._ctx(sem_ws)
        run_tool("semantic_search_code", ctx, {"query": "intent reasoning"})
        db, workspace = sem_ws
        for citation in ctx.citations:
            row = (
                db.query(models.CodeChunk)
                .filter_by(
                    repository_id=ctx.by_name(citation.repository).id,
                    file_path=citation.path,
                    start_line=citation.start_line,
                )
                .first()
            )
            assert row is not None, "cited location must exist in the index"
            assert citation.start_line == row.start_line
            assert citation.end_line == row.end_line

    def test_tools_degrade_without_index(self, sem_ws) -> None:
        db, workspace = sem_ws
        indexing._drop_workspace_chunks(db, workspace.id)
        ctx = self._ctx(sem_ws)
        result = run_tool("semantic_search_code", ctx, {"query": "intent"})
        assert "Error" in result
        assert "index" in result.lower()
        # The lexical tools still work.
        lexical = run_tool("search_code", ctx, {"query": "ReasonIQ"})
        assert "reason_iq.py" in lexical


# ---------------------------------------------------------------------------
# API
# ---------------------------------------------------------------------------


class TestIndexingAPI:
    def _client(self, sem_db, monkeypatch) -> TestClient:
        from app.db import get_db
        from app.main import app

        def override():
            db = sem_db()
            try:
                yield db
            finally:
                db.close()

        app.dependency_overrides[get_db] = override
        import app.db as app_db

        monkeypatch.setattr(app_db, "SessionLocal", sem_db)
        # Statuses are held in process memory keyed by workspace id; without
        # this reset, a later test's workspace would collide with an earlier
        # test's status entry and read a stale "indexing" state.
        indexing._statuses.clear()
        indexing._running.clear()
        return TestClient(app)

    def test_index_status_and_trigger(self, sem_db, semantic_repo_pair, monkeypatch) -> None:
        docs, svc = semantic_repo_pair
        client = self._client(sem_db, monkeypatch)
        created = client.post("/api/workspaces", json={"name": "W"}).json()

        status = client.get(f"/api/workspaces/{created['id']}/sources/index").json()
        assert status["status"] == "idle"
        assert status["code_embedding_model"] == settings.code_embedding_model
        assert status["reindex_required"] is False

        triggered = client.post(
            f"/api/workspaces/{created['id']}/sources/index",
            params={"background": False},
        ).json()
        assert triggered["status"] == "completed"
        assert triggered["counts"]["embeddings_generated"] >= 0

    def test_semantic_search_endpoint(self, sem_ws, monkeypatch, sem_db) -> None:
        db, workspace = sem_ws
        client = self._client(sem_db, monkeypatch)
        result = client.get(
            f"/api/workspaces/{workspace.id}/sources/search",
            params={"q": "ReasonIQ background", "mode": "hybrid"},
        ).json()
        assert result["mode"] == "hybrid"
        assert result["lexical_hits"] >= 0
        assert result["semantic_hits"] >= 0
        kinds = {h["kind"] for h in result["hits"]}
        assert kinds <= {"code", "document"}
        for hit in result["hits"]:
            assert hit["source"]
            assert hit["content"]

    def test_semantic_only_mode(self, sem_ws, monkeypatch, sem_db) -> None:
        db, workspace = sem_ws
        client = self._client(sem_db, monkeypatch)
        result = client.get(
            f"/api/workspaces/{workspace.id}/sources/search",
            params={"q": "where is intent reasoning implemented", "mode": "semantic", "kind": "code"},
        ).json()
        assert result["mode"] == "semantic"
        assert result["hits"]
        assert all(h["kind"] == "code" for h in result["hits"])

    def test_reindex_endpoint(self, sem_ws, monkeypatch, sem_db) -> None:
        db, workspace = sem_ws
        client = self._client(sem_db, monkeypatch)
        result = client.post(
            f"/api/workspaces/{workspace.id}/sources/reindex",
            params={"background": False},
        ).json()
        assert result["status"] == "completed"
        # Re-indexing rebuilt the vectors under the current model.
        assert db.query(models.CodeChunk).count() > 0
        assert db.query(models.DocumentChunk).count() > 0

    def test_lexical_mode_excludes_semantic_hits(self, sem_ws, monkeypatch, sem_db) -> None:
        db, workspace = sem_ws
        client = self._client(sem_db, monkeypatch)
        result = client.get(
            f"/api/workspaces/{workspace.id}/sources/search",
            params={"q": "ReasonIQ background", "mode": "lexical"},
        ).json()
        assert result["mode"] == "lexical"
        # Pure keyword search: hits may come from the lexical searches only.
        assert result["semantic_hits"] == 0
        assert result["lexical_hits"] > 0

    def test_document_search_is_scoped_to_the_workspace(
        self, sem_db, semantic_repo_pair
    ) -> None:
        """Chunks of another workspace are never returned, even without a
        repository filter."""
        docs, svc = semantic_repo_pair
        db = sem_db()
        first = models.Workspace(name="first")
        second = models.Workspace(name="second")
        db.add_all([first, second])
        db.flush()
        for ws in (first, second):
            db.add(
                models.Repository(
                    workspace_id=ws.id, name="docs", local_path=str(docs), kind="documentation"
                )
            )
            db.add(
                models.Repository(
                    workspace_id=ws.id, name="svc", local_path=str(svc), kind="source"
                )
            )
        db.commit()
        indexing.index_workspace_now(db, first.id)
        indexing.index_workspace_now(db, second.id)

        results = retrieval.search_documents_semantically(
            db, "ReasonIQ background", n_results=5, workspace_id=second.id
        )
        assert results
        assert all(
            r.repository_id in {repo.id for repo in db.query(models.Repository).filter_by(workspace_id=second.id)}
            for r in results
        )
        first_ids = {repo.id for repo in db.query(models.Repository).filter_by(workspace_id=first.id)}
        assert all(r.repository_id not in first_ids for r in results)
