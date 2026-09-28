"""Real Markdown links, both directions, for one document.

The context sidebar used to show only what Delphi Pulse had proposed. Those are
inferred connections: the model *decided* two documents are related, which is
useful but is not what the author wrote. A hand-written `[link](../adr.md)` in
the text was invisible, so the panel claimed to describe a document while
omitting the relationships the author had actually recorded. This module reads
the real thing: the links in the file, and the files that link back to it.

Parsing only. Nothing here writes, and no link target is ever followed off the
repository: an external URL is reported as external rather than fetched, and a
path that resolves outside the root is dropped rather than reported.
"""
from __future__ import annotations

import posixpath
import re
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from app.services.documents import (
    TEXT_DOC_SUFFIXES,
    DocumentError,
    read_document,
)
from app.services.markdown_structure import Link, parse_markdown
from app.services.paths import PathSecurityError, safe_path

#: Only these are followed inside the repository. A link to a PDF, a diagram or
#: a spreadsheet is a real reference but not one this panel can open as a
#: document, and pretending otherwise would produce a dead row.
LINKABLE_SUFFIXES = frozenset({".md", ".markdown", ".mdx", ".txt"})

#: Schemes that leave the repository. Reported, never followed: this module has
#: no network access and must not gain any.
EXTERNAL_SCHEME_RE = re.compile(r"^[a-z][a-z0-9+.-]*:", re.IGNORECASE)

#: Ceiling on how many files a single inbound scan will read. A documentation
#: repository is small, but the scan is proportional to the corpus and this is a
#: read endpoint behind a UI click, so it is bounded rather than assumed.
MAX_INBOUND_SCAN_FILES = 2000

#: Files larger than this are skipped by the inbound scan. A 20MB file almost
#: certainly links less deliberately than a 4KB one, and scanning it would make
#: the slowest document in the repository the slowest to open.
MAX_INBOUND_FILE_BYTES = 2 * 1024 * 1024

#: Directories never scanned, matched on any path part.
SKIPPED_DIR_PARTS = frozenset({"node_modules", "__pycache__", "site-packages"})


@dataclass(frozen=True)
class DocumentLink:
    """One resolved link, as the author wrote it."""

    #: Repo-relative POSIX path of the other document.
    path: str
    #: The link text, which is often more informative than the target.
    text: str


@dataclass(frozen=True)
class ExternalReference:
    """A link that leaves the repository, kept so it is not silently lost."""

    target: str
    text: str


@dataclass(frozen=True)
class DocumentLinks:
    """Everything one document says about its neighbours."""

    path: str
    outbound: list[DocumentLink]
    inbound: list[DocumentLink]
    external: list[ExternalReference]
def _strip_fragment(target: str) -> str:
    """Drop ``#anchor`` and ``?query``; they address within a file, not a file."""
    return target.split("#", 1)[0].split("?", 1)[0].strip()


def _is_external(target: str) -> bool:
    return bool(EXTERNAL_SCHEME_RE.match(target)) or target.startswith("//")


def _resolve(root: Path, source_rel: str, target: str) -> str | None:
    """Resolve a link target against the linking document, or None if unusable.

    Relative links are the normal case in Markdown and are resolved against the
    *linking file's directory*, which is what every Markdown renderer does and
    what the author meant. A root-relative target (``/architecture/x.md``) is
    resolved from the repository root instead, which is the other convention in
    use.

    None covers every case that is not an openable document inside the
    repository: a missing file, a directory, a non-document suffix, and -- via
    :func:`safe_path` -- anything that resolves outside the root.
    """
    cleaned = _strip_fragment(target)
    if not cleaned or _is_external(cleaned):
        return None

    cleaned = cleaned.replace("\\", "/")
    if cleaned.startswith("/"):
        candidate_rel = cleaned.lstrip("/")
    else:
        base = PurePosixPath(source_rel).parent
        candidate_rel = cleaned if str(base) == "." else str(base / cleaned)

    # Collapsed here rather than left to safe_path. safe_path rejects ANY ".."
    # segment, which is right for a path typed into an API box but wrong here:
    # `../decisions/adr.md` is an ordinary Markdown link and the whole point of
    # this function is to follow it. Lexical normalisation folds the ".." away
    # where the link actually points, and what is left over -- a path still
    # starting with "..", meaning it climbed out of the repository -- is refused
    # here, before safe_path ever sees it.
    candidate_rel = posixpath.normpath(candidate_rel)
    if not candidate_rel or candidate_rel.startswith("../") or candidate_rel == "..":
        return None

    try:
        # With no ".." left, safe_path is doing one job here: catching a symlink
        # that resolves out of the root.
        absolute = safe_path(root, candidate_rel)
    except PathSecurityError:
        # A symlink pointing out of the repository. Dropped: a link that leaves
        # the repository is not a document in this workspace.
        return None

    if not absolute.is_file() or absolute.suffix.lower() not in LINKABLE_SUFFIXES:
        return None
    return absolute.relative_to(root.resolve()).as_posix()


def _links_of(content: str) -> list[Link]:
    """Parse links, tolerating a file that is not really Markdown."""
    try:
        return parse_markdown(content).links
    except Exception:
        # A document we cannot structure is a document with no known links, not
        # a failed request: the panel should still describe what it can.
        return []


def _read(root: Path, rel: str) -> str:
    try:
        return read_document(root, rel)
    except (DocumentError, PathSecurityError, OSError):
        return ""


def _markdown_files(root: Path) -> list[str]:
    """Every linkable document in the repository, sorted for a stable order."""
    results: list[str] = []
    for path in root.rglob("*"):
        if not path.is_file() or path.suffix.lower() not in LINKABLE_SUFFIXES:
            continue
        relative_parts = path.parts[len(root.parts) : -1]
        if any(
            part.startswith(".") or part in SKIPPED_DIR_PARTS for part in relative_parts
        ):
            # Pruned by inspection rather than by mutating the walk, because the
            # caller holds the returned list; the cost is one comparison per
            # ignored entry next to reading the Markdown itself.
            continue
        results.append(path.relative_to(root).as_posix())
    return sorted(results)


def collect_links(root: str | Path, rel: str) -> DocumentLinks:
    """Every link into and out of one document, as written in the Markdown.

    Outbound links come from the document itself. Inbound links require reading
    the rest of the corpus, because Markdown records only the forward direction:
    nothing states that two files reference each other without looking at both.
    That is why this is a service over the repository on disk rather than a
    column in the database -- and why it cannot be cached, since the files are
    the source of truth and change underneath the app.
    """
    root_path = Path(root).expanduser().resolve()
    subject = _strip_fragment(rel).replace("\\", "/")
    content = _read(root_path, subject)

    outbound: list[DocumentLink] = []
    external: list[ExternalReference] = []
    seen: set[tuple[str, str]] = set()

    for link in _links_of(content):
        target = link.target.strip()
        if not target or target.startswith("#"):
            # A same-document anchor. Real, but not a link to another document.
            continue
        if _is_external(target):
            entry = (target, link.text)
            if entry not in seen:
                seen.add(entry)
                external.append(ExternalReference(target=target, text=link.text))
            continue
        resolved = _resolve(root_path, subject, target)
        if resolved is None or resolved == subject:
            continue
        entry = (resolved, link.text)
        if entry in seen:
            continue
        seen.add(entry)
        outbound.append(DocumentLink(path=resolved, text=link.text))

    return DocumentLinks(
        path=subject,
        outbound=outbound,
        inbound=_inbound(root_path, subject),
        external=external,
    )


def _inbound(root: Path, subject: str) -> list[DocumentLink]:
    """Which documents link to this one.

    Matching is on the resolved path, so ``./architecture.md`` and
    ``architecture/architecture.md`` are recognised as the same file instead of
    as two unrelated targets. One row per (document, text) pair: the same file
    linked twice under the same label is one reference, not two.
    """
    found: list[DocumentLink] = []
    seen: set[tuple[str, str]] = set()
    scanned = 0

    for candidate in _markdown_files(root):
        if scanned >= MAX_INBOUND_SCAN_FILES:
            break
        if candidate == subject:
            continue
        try:
            if (root / candidate).stat().st_size > MAX_INBOUND_FILE_BYTES:
                continue
        except OSError:
            continue
        scanned += 1

        for link in _links_of(_read(root, candidate)):
            if _is_external(link.target) or _resolve(root, candidate, link.target) != subject:
                continue
            entry = (candidate, link.text)
            if entry in seen:
                continue
            seen.add(entry)
            found.append(DocumentLink(path=candidate, text=link.text))

    return found


__all__ = [
    "DocumentLink",
    "DocumentLinks",
    "ExternalReference",
    "LINKABLE_SUFFIXES",
    "collect_links",
]
