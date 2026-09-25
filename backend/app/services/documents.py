"""Read-oriented document access.

The documentation repository on disk is the source of truth; nothing here
caches document content in the database. This module is deliberately
read-only -- mutation lives behind an explicit approval flow (Milestone 2+).
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from app.services.paths import PathSecurityError, safe_path, to_rel_path

IGNORED_DIRS = {".git", "node_modules", ".venv", "__pycache__", ".idea", ".vscode"}
DOC_SUFFIXES = {".md", ".markdown", ".mdx"}
MAX_READ_BYTES = 512 * 1024


class DocumentError(Exception):
    """Raised for user-correctable document problems (missing file, etc.)."""


@dataclass
class DocNode:
    name: str
    path: str
    is_dir: bool
    size: int | None = None
    children: list["DocNode"] = field(default_factory=list)


def _is_ignored(name: str) -> bool:
    return name in IGNORED_DIRS or name.endswith(".tmp")


def build_tree(root: str | Path, subdir: str = ".") -> DocNode:
    """Build a directory tree of documentation files, folders first."""
    root_path = Path(root)
    base = safe_path(root_path, subdir)
    if not base.exists():
        raise DocumentError(f"Directory not found: {subdir}")
    if not base.is_dir():
        raise DocumentError(f"Not a directory: {subdir}")

    def walk(directory: Path) -> list[DocNode]:
        nodes: list[DocNode] = []
        try:
            entries = sorted(
                directory.iterdir(), key=lambda p: (p.is_file(), p.name.lower())
            )
        except PermissionError as exc:
            raise DocumentError(f"Cannot read directory: {directory}") from exc

        for entry in entries:
            if _is_ignored(entry.name):
                continue
            rel = to_rel_path(root_path, entry)
            if entry.is_dir():
                nodes.append(
                    DocNode(name=entry.name, path=rel, is_dir=True, children=walk(entry))
                )
            elif entry.suffix.lower() in DOC_SUFFIXES:
                nodes.append(
                    DocNode(
                        name=entry.name,
                        path=rel,
                        is_dir=False,
                        size=entry.stat().st_size,
                    )
                )
        return nodes

    rel_base = to_rel_path(root_path, base)
    return DocNode(
        name=Path(rel_base).name or Path(root_path).name,
        path=rel_base,
        is_dir=True,
        children=walk(base),
    )


def list_documents(root: str | Path, subdir: str = ".") -> list[str]:
    """Flat list of repo-relative Markdown paths, for retrieval and search."""
    root_path = Path(root)
    base = safe_path(root_path, subdir)
    if not base.is_dir():
        raise DocumentError(f"Directory not found: {subdir}")

    results: list[str] = []
    for dirpath, dirnames, filenames in _walk(base):
        for filename in filenames:
            if Path(filename).suffix.lower() in DOC_SUFFIXES and not _is_ignored(filename):
                results.append(to_rel_path(root_path, Path(dirpath) / filename))
    return sorted(results)


def _walk(base: Path):
    for dirpath, dirnames, filenames in os.walk(base):
        dirnames[:] = [d for d in dirnames if not _is_ignored(d)]
        yield dirpath, dirnames, filenames


def read_document(root: str | Path, rel: str) -> str:
    """Read a Markdown document as raw text."""
    target = safe_path(root, rel)
    if not target.exists():
        raise DocumentError(f"Document not found: {rel}")
    if target.is_dir():
        raise DocumentError(f"{rel} is a directory, not a document.")
    if target.suffix.lower() not in DOC_SUFFIXES:
        raise DocumentError(f"Not a document file: {rel}")
    if target.stat().st_size > MAX_READ_BYTES:
        raise DocumentError(f"Document is too large to read: {rel}")
    try:
        # utf-8-sig transparently strips a leading byte-order mark, which some
        # editors add and which would otherwise break heading detection.
        return target.read_text(encoding="utf-8-sig", errors="replace")
    except OSError as exc:
        raise DocumentError(f"Could not read document: {rel}") from exc


__all__ = [
    "DocNode",
    "DocumentError",
    "PathSecurityError",
    "build_tree",
    "list_documents",
    "read_document",
]
