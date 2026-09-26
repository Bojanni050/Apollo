"""AST-aware source parsing for semantic code indexing.

Tree-sitter extracts *complete* syntax nodes -- a ``function_definition`` or
``class_definition`` is indexed with its exact node boundaries, never an
arbitrary line-count split. A unit's source is preserved verbatim; only the
text handed to the embedder is enriched (see :func:`enriched_code_unit`).

Languages are registered in :data:`PARSERS`; adding one means adding a
Tree-sitter grammar and an entry mapping it to the node types that hold
architecture evidence. Python is first; the abstraction exists so later
languages change one table, not the pipeline.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

from tree_sitter import Language, Parser

import tree_sitter_python

from app.services.inspection import _looks_binary


class ParseError(Exception):
    """Raised when a file cannot be read or decoded for parsing."""


@dataclass(frozen=True)
class CodeUnit:
    """One complete syntax node extracted from a source file.

    ``source`` is the exact bytes of the node, verbatim. ``identifier`` is
    deterministic: the same node in the same file of the same repository
    always yields the same identifier, so re-indexing upserts rather than
    duplicates.
    """

    repository_id: int
    repository_name: str
    file_path: str
    language: str
    node_type: str
    symbol: str
    signature: str
    start_line: int
    end_line: int
    source: str
    content_hash: str
    identifier: str

    @property
    def enriched(self) -> str:
        """The representation handed to the embedder (source stays intact)."""
        return enriched_code_unit(self)


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def content_hash(text: str) -> str:
    """The hash used for incremental indexing: change detection by content."""
    return _sha256(text)


def enriched_code_unit(unit: CodeUnit) -> str:
    """Build the enriched representation that gets embedded.

    A code model retrieves better when the fragment announces what it is; the
    original source is stored separately and is never modified.
    """
    header = (
        f"# Repository: {unit.repository_name}\n"
        f"# File: {unit.file_path}\n"
        f"# Type: {unit.node_type.removesuffix('_definition')}\n"
        f"# Symbol: {unit.symbol}\n"
        f"# Lines: {unit.start_line}-{unit.end_line}\n\n"
    )
    return header + unit.source


@dataclass(frozen=True)
class _LanguageParser:
    """A registered language: its grammar and its evidence-bearing node types."""

    language: Language
    node_types: tuple[str, ...]
    suffixes: tuple[str, ...]


def _python() -> _LanguageParser:
    return _LanguageParser(
        language=Language(tree_sitter_python.language()),
        # Complete function and class definitions only: a method is a
        # function_definition inside a class_definition and is indexed as part
        # of the class, and separately -- both are legitimate evidence.
        node_types=("function_definition", "class_definition"),
        suffixes=(".py",),
    )


#: Registered languages, keyed by name. A new grammar registers here.
PARSERS: dict[str, _LanguageParser] = {"python": _python()}

#: file suffix -> language name, derived from the registry.
_SUFFIX_TO_LANGUAGE: dict[str, str] = {
    suffix: name for name, spec in PARSERS.items() for suffix in spec.suffixes
}

_MAX_SOURCE_BYTES = 2 * 1024 * 1024  # per file, same ceiling as lexical search

_parser_cache: dict[str, Parser] = {}


def language_for(path: str | Path) -> str | None:
    """The registered language for a file path, or ``None`` when unsupported."""
    return _SUFFIX_TO_LANGUAGE.get(Path(path).suffix.lower())


def _parser(language: str) -> Parser:
    if language not in _parser_cache:
        _parser_cache[language] = Parser(PARSERS[language].language)
    return _parser_cache[language]


def _signature(node, source_bytes: bytes) -> str:
    """The definition line(s) up to and including the ``:``.

    For a function this is ``def name(args) -> ret:``; for a class,
    ``class Name(Base):``. Sourced from the node's own bytes, so it is always
    exact for the file being parsed.
    """
    first_line = node.start_point[0]
    for offset in range(node.start_byte, min(node.child_by_field_name("body").start_byte, node.end_byte)):
        if source_bytes[offset : offset + 1] == b":":
            return source_bytes[node.start_byte : offset + 1].decode("utf-8", errors="replace").strip()
    # Fall back on the first line (never reached for well-formed Python, but
    # a malformed file must not abort the run).
    del first_line
    return source_bytes[node.start_byte : node.end_byte].decode("utf-8", errors="replace").splitlines()[0]


def _symbol_of(node) -> str:
    name = node.child_by_field_name("name")
    return name.text.decode("utf-8", errors="replace") if name else "<anonymous>"


def parse_file(
    path: str | Path,
    *,
    repository_id: int,
    repository_name: str,
    file_path: str,
    language: str | None = None,
) -> list[CodeUnit]:
    """Parse one source file into complete code units.

    A malformed file never aborts the run: Tree-sitter recovers and returns
    the nodes it *could* parse, and a file with no extractable definitions
    simply yields no units. Read/decode failures raise :class:`ParseError`;
    the caller records them and continues with the next file.
    """
    lang = language or language_for(path)
    if lang is None or lang not in PARSERS:
        return []
    target = Path(path)
    try:
        size = target.stat().st_size
    except OSError as exc:
        raise ParseError(f"Could not stat {file_path}: {exc}") from exc
    if size > _MAX_SOURCE_BYTES:
        raise ParseError(f"{file_path} is too large to parse ({size} bytes).")
    try:
        raw = target.read_bytes()
    except OSError as exc:
        raise ParseError(f"Could not read {file_path}: {exc}") from exc
    if _looks_binary(raw):
        raise ParseError(f"{file_path} looks like a binary file; refusing to parse it.")
    source_bytes = raw
    tree = _parser(lang).parse(source_bytes)
    spec = PARSERS[lang]
    units: list[CodeUnit] = []

    def visit(node) -> None:
        if node.type in spec.node_types:
            body = node.child_by_field_name("body")
            # Without a body (a malformed fragment Tree-sitter recovered
            # from) there is no complete unit to index.
            if body is not None:
                start_line = node.start_point[0] + 1  # 0-based -> 1-based
                end_line = node.end_point[0] + 1
                source = source_bytes[node.start_byte : node.end_byte].decode(
                    "utf-8", errors="replace"
                )
                units.append(
                    CodeUnit(
                        repository_id=repository_id,
                        repository_name=repository_name,
                        file_path=file_path,
                        language=lang,
                        node_type=node.type,
                        symbol=_symbol_of(node),
                        signature=_signature(node, source_bytes),
                        start_line=start_line,
                        end_line=end_line,
                        source=source,
                        content_hash=content_hash(source),
                        identifier=code_unit_identifier(
                            repository_id, file_path, node.type, start_line, source
                        ),
                    )
                )
        for child in node.children:
            visit(child)

    visit(tree.root_node)
    return units


def code_unit_identifier(
    repository_id: int,
    file_path: str,
    node_type: str,
    start_line: int,
    source: str,
) -> str:
    """A deterministic identifier for one code unit.

    Stable across runs for identical content, and different as soon as any
    part of what makes the unit unique changes. Used as the natural key for
    idempotent upserts: re-indexing an unchanged file matches existing rows
    and rewrites nothing.
    """
    digest = hashlib.sha256(
        f"{repository_id}|{file_path}|{node_type}|{start_line}|{source}".encode("utf-8")
    ).hexdigest()
    return f"{file_path}:{node_type}:{digest[:16]}"
