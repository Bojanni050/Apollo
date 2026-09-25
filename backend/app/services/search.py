"""Simple lexical document search.

Deliberately straightforward: scan Markdown files and score them by term
matches in path and content. No vector store, no embeddings -- that is
explicitly out of scope for the MVP.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from app.services.documents import list_documents, read_document

STOPWORDS = {
    "the", "a", "an", "and", "or", "of", "to", "in", "is", "are", "for",
    "on", "with", "as", "by", "at", "from", "that", "this", "it", "be",
}
MAX_FILE_BYTES = 256 * 1024


@dataclass
class SearchHit:
    path: str
    score: float
    title: str
    snippet: str
    line: int | None = None


def _tokenize(query: str) -> list[str]:
    return [
        t
        for t in re.findall(r"[A-Za-z0-9_]+", query.lower())
        if t not in STOPWORDS and len(t) > 1
    ]


def _title_of(content: str, fallback: str) -> str:
    for line in content.splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            return stripped.lstrip("#").strip() or fallback
        if stripped:
            break
    return fallback


def search_documents(
    root: str | Path, query: str, limit: int = 20, subdir: str = "."
) -> list[SearchHit]:
    """Rank documents by term frequency in content plus a path bonus."""
    tokens = _tokenize(query)
    if not tokens:
        return []

    hits: list[SearchHit] = []
    for rel in list_documents(root, subdir):
        try:
            path = Path(root) / rel
            if path.stat().st_size > MAX_FILE_BYTES:
                continue
            content = read_document(root, rel)
        except Exception:  # noqa: BLE001 - skip unreadable/oversized files
            continue

        lowered_path = rel.lower()
        score = 0.0
        for token in tokens:
            if token in lowered_path:
                score += 5.0
            count = content.lower().count(token)
            score += min(count, 10) * 1.0

        if score <= 0:
            continue

        snippet, line_no = _make_snippet(content, tokens)
        hits.append(
            SearchHit(
                path=rel,
                score=score,
                title=_title_of(content, Path(rel).stem),
                snippet=snippet,
                line=line_no,
            )
        )

    hits.sort(key=lambda h: h.score, reverse=True)
    return hits[:limit]


def _make_snippet(content: str, tokens: list[str], width: int = 220) -> tuple[str, int | None]:
    lines = content.splitlines()
    for index, line in enumerate(lines, start=1):
        lowered = line.lower()
        if any(token in lowered for token in tokens):
            text = line.strip()
            if len(text) > width:
                text = text[: width - 1] + "\u2026"
            return text, index
    return (lines[0].strip()[:width] if lines else ""), None
