"""Repository-aware code inspection for source repositories.

Documentation repositories contain Markdown; source repositories contain code,
configuration and schemas. This module gives the backend (and through it the
AI tools) a repository-aware way to work with the second kind *without
indexing the whole repository into context*:

* :func:`list_code_files` -- targeted listing, never a whole-repo dump;
* :func:`read_source_file` -- bounded reads of text files, with binary files
  refused up front rather than read and mangled;
* :func:`search_code` -- lexical code search over text files only;
* :func:`project_structure` -- a compact directory summary for orientation.

Ignored directories (``.git``, ``node_modules``, ``venv``, ``__pycache__``,
``dist``, ``build``, ``target``, ...) are never descended into, and a
repository's own ``.gitignore`` is respected where practical: ``git ls-files``
is used when the checkout is a Git repository, with a filename-pattern
fallback for plain directories.
"""
from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

from app.services.paths import PathSecurityError, safe_path, to_rel_path

#: Directories that never hold architecture evidence worth reading.
IGNORED_DIRS = {
    ".git",
    ".hg",
    ".svn",
    "node_modules",
    "venv",
    ".venv",
    "env",
    "__pycache__",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    "dist",
    "build",
    "target",
    "out",
    ".idea",
    ".vscode",
    ".next",
    ".turbo",
    "coverage",
    ".tox",
    ".nox",
    "site-packages",
}

#: Text file extensions worth reading, searching and citing. Anything else is
#: treated as binary-or-irrelevant and refused by the read path.
TEXT_SUFFIXES = {
    ".py", ".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs", ".json", ".yaml", ".yml",
    ".toml", ".md", ".markdown", ".mdx", ".rst", ".txt", ".cfg", ".ini",
    ".sh", ".bash", ".zsh", ".ps1", ".bat", ".cmd",
    ".go", ".rs", ".java", ".kt", ".kts", ".rb", ".php", ".cs", ".c", ".h",
    ".cpp", ".hpp", ".cc", ".hh", ".swift", ".sql", ".graphql", ".proto",
    ".tf", ".tfvars", ".hcl", ".html", ".css", ".scss", ".less", ".svelte",
    ".vue", ".xml", ".csv",
}

#: Suffixes whose files are always considered binary (never read, never
#: searched), even though some are technically text-ish.
BINARY_SUFFIXES = {
    ".png", ".jpg", ".jpeg", ".gif", ".bmp", ".ico", ".webp", ".svgz",
    ".pdf", ".docx", ".xlsx", ".pptx", ".zip", ".gz", ".tar", ".rar",
    ".7z", ".exe", ".dll", ".so", ".dylib", ".bin", ".pyc", ".class",
    ".jar", ".war", ".wasm", ".mp3", ".mp4", ".mov", ".avi", ".woff",
    ".woff2", ".ttf", ".otf", ".eot", ".db", ".sqlite", ".sqlite3", ".lock",
}

#: Extensionless files that are plain text and architecturally relevant.
TEXT_BASENAMES = {
    "makefile", "dockerfile", "rakefile", "gemfile", "procfile",
    "vagrantfile", "jenkinsfile", "justfile", "license", "notice",
    "requirements", "gemfile.lock", "procfile",
}

MAX_READ_BYTES = 512 * 1024  # 512 KB per file: targeted, not wholesale
MAX_LIST_ENTRIES = 400
MAX_READ_LINES = 400  # per read call
MAX_FILE_BYTES_SEARCH = 2 * 1024 * 1024  # 2 MB per file during search
GIT_LS_FILES_TIMEOUT = 20


class InspectionError(Exception):
    """Raised for user-correctable inspection problems (missing path, binary)."""


@dataclass
class FileEntry:
    path: str
    size: int


@dataclass
class CodeHit:
    path: str
    line: int
    snippet: str
    score: float = 0.0


def _git_tracked(root: Path) -> set[str] | None:
    """Tracked paths from ``git ls-files``, or ``None`` when unavailable.

    When the checkout is a Git repository, the tracked set is how the
    repository's own ``.gitignore`` is respected without reimplementing it.
    """
    try:
        proc = subprocess.run(
            ["git", "-C", str(root), "ls-files", "-z"],
            capture_output=True,
            timeout=GIT_LS_FILES_TIMEOUT,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if proc.returncode != 0:
        return None
    return {p.decode("utf-8", errors="replace") for p in proc.stdout.split(b"\x00") if p}


def _dir_ignored(name: str) -> bool:
    return name in IGNORED_DIRS


def _is_text_file(path: Path) -> bool:
    suffix = path.suffix.lower()
    name = path.name.lower()
    if suffix in BINARY_SUFFIXES:
        return False
    if suffix in TEXT_SUFFIXES:
        return True
    if name in TEXT_BASENAMES or name.startswith("dockerfile") or name.startswith("requirements"):
        return True
    return False


def _looks_binary(content: bytes) -> bool:
    """Heuristic: a NUL byte or a low proportion of text bytes says binary."""
    if not content:
        return False
    if b"\x00" in content[:4096]:
        return True
    sample = content[:4096]
    text_chars = sum(
        1 for b in sample if b in (9, 10, 13) or 32 <= b < 127 or b >= 128
    )
    return (text_chars / len(sample)) < 0.7


def list_code_files(
    root: str | Path, subdir: str = ".", limit: int = MAX_LIST_ENTRIES
) -> list[FileEntry]:
    """List text files under ``subdir``, excluding ignored directories.

    Uses the repository's own ``.gitignore`` (via ``git ls-files``) when the
    checkout is a Git repository; a filename-pattern fallback keeps the same
    guarantees for plain directories.
    """
    root_path = Path(root)
    base = safe_path(root_path, subdir)
    if not base.exists() or not base.is_dir():
        raise InspectionError(f"Directory not found: {subdir}")

    tracked = _git_tracked(root_path)
    results: list[FileEntry] = []
    dirs_to_walk: list[Path] = [base]
    while dirs_to_walk and len(results) < limit:
        current = dirs_to_walk.pop()
        try:
            entries = sorted(
                current.iterdir(), key=lambda p: (p.is_file(), p.name.lower())
            )
        except OSError:
            continue
        for entry in entries:
            if len(results) >= limit:
                break
            if entry.is_dir():
                if not _dir_ignored(entry.name):
                    dirs_to_walk.append(entry)
                continue
            if not _is_text_file(entry):
                continue
            rel = to_rel_path(root_path, entry)
            if tracked is not None and rel not in tracked:
                # Respects .gitignore via git ls-files.
                continue
            try:
                size = entry.stat().st_size
            except OSError:
                continue
            results.append(FileEntry(path=rel, size=size))
    return sorted(results, key=lambda f: f.path.lower())


def read_source_file(
    root: str | Path, rel: str, *, start_line: int = 1, end_line: int = 0
) -> str:
    """Read a bounded slice of a text source file, with numbered lines.

    Binary files are refused with an explicit error rather than decoded into
    garbage. Output is capped at 400 lines per call so a single tool result can
    never become a whole-file dump.
    """
    target = safe_path(root, rel)
    if not target.exists():
        raise InspectionError(f"File not found: {rel}")
    if target.is_dir():
        raise InspectionError(f"{rel} is a directory, not a file.")
    if not _is_text_file(target):
        raise InspectionError(
            f"{rel} is a binary or unsupported file type; refusing to read it."
        )
    if target.stat().st_size > MAX_READ_BYTES:
        raise InspectionError(
            f"{rel} is too large to read in one call ({target.stat().st_size} bytes)."
        )

    try:
        raw = target.read_bytes()
    except OSError as exc:
        raise InspectionError(f"Could not read {rel}: {exc}") from exc
    if _looks_binary(raw):
        raise InspectionError(f"{rel} looks like a binary file; refusing to read it.")

    content = raw.decode("utf-8-sig", errors="replace")
    lines = content.splitlines()
    start = max(1, int(start_line or 1))
    stop = int(end_line) if end_line and int(end_line) > 0 else len(lines)
    stop = min(stop, len(lines), start + MAX_READ_LINES - 1)
    if start > len(lines):
        raise InspectionError(
            f"start_line {start} is beyond the end of {rel} ({len(lines)} lines)."
        )
    body = "\n".join(f"{i:4d}| {lines[i - 1]}" for i in range(start, stop + 1))
    header = f"{rel} (lines {start}-{stop} of {len(lines)})"
    return f"{header}\n{body}"


def search_code(
    root: str | Path, query: str, *, subdir: str = ".", limit: int = 20
) -> list[CodeHit]:
    """Lexical search over text files only, skipping ignored directories.

    Scores by matches in the path and in file content, and returns the matching
    line so the caller (AI or UI) can cite the exact location.
    """
    tokens = [t for t in re.findall(r"[A-Za-z0-9_]+", query.lower()) if len(t) > 1]
    if not tokens:
        return []

    hits: list[CodeHit] = []
    for entry in list_code_files(root, subdir):
        path = Path(root) / entry.path
        if entry.size > MAX_FILE_BYTES_SEARCH or entry.size == 0:
            continue
        try:
            raw = path.read_bytes()
        except OSError:
            continue
        if _looks_binary(raw):
            continue
        content = raw.decode("utf-8-sig", errors="replace")

        score = 0.0
        lowered_path = entry.path.lower()
        for token in tokens:
            if token in lowered_path:
                score += 5.0
            score += min(content.lower().count(token), 20) * 1.0
        if score <= 0:
            continue

        best: CodeHit | None = None
        for number, line in enumerate(content.splitlines(), start=1):
            lowered = line.lower()
            if any(token in lowered for token in tokens):
                best = CodeHit(path=entry.path, line=number, snippet=line.strip()[:240])
                break
        if best is None:
            first = content.splitlines()[0][:240] if content.splitlines() else ""
            best = CodeHit(path=entry.path, line=1, snippet=first)
        best.score = score
        hits.append(best)

    hits.sort(key=lambda h: h.score, reverse=True)
    return hits[:limit]


def count_lines(root: str | Path, rel: str) -> int:
    """Line count for a text file, or 0 when it cannot be read."""
    target = safe_path(root, rel)
    try:
        if not target.is_file() or not _is_text_file(target):
            return 0
        return len(target.read_bytes().decode("utf-8-sig", errors="replace").splitlines())
    except (OSError, PathSecurityError):
        return 0


def project_structure(root: str | Path, max_depth: int = 3) -> str:
    """A compact textual summary of a repository's directory structure.

    Depth-limited so a huge monorepo still fits in context; this is for
    orientation ("where does X live?"), not enumeration.
    """
    root_path = Path(root)
    lines: list[str] = []

    def walk(directory: Path, prefix: str, depth: int) -> None:
        if depth > max_depth:
            return
        try:
            entries = sorted(
                directory.iterdir(), key=lambda p: (p.is_file(), p.name.lower())
            )
        except OSError:
            return
        for entry in entries:
            if entry.name.startswith("."):
                continue
            if entry.is_dir():
                if _dir_ignored(entry.name):
                    continue
                lines.append(f"{prefix}{entry.name}/")
                walk(entry, prefix + "  ", depth + 1)
            elif _is_text_file(entry):
                lines.append(f"{prefix}{entry.name}")

    walk(root_path, "", 0)
    if len(lines) > MAX_LIST_ENTRIES:
        lines = lines[:MAX_LIST_ENTRIES] + ["... (truncated)"]
    return "\n".join(lines)
