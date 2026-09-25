"""Filesystem path safety.

Every filesystem access in this application goes through :func:`safe_path`.
It resolves the candidate path and asserts it stays inside the root of a
repository that the user has explicitly registered. Symlinks that escape a
repository root are rejected too, because ``resolve()`` follows them.
"""
from __future__ import annotations

from pathlib import Path, PurePosixPath


class PathSecurityError(ValueError):
    """Raised when a requested path escapes its authorized repository."""


def normalize_rel_path(rel: str) -> Path:
    """Normalize a repo-relative POSIX path to a safe relative ``Path``.

    Rejects absolute paths, drive letters, ``..`` traversal, and NUL bytes.
    """
    if "\x00" in rel:
        raise PathSecurityError("Path contains a NUL byte.")

    cleaned = rel.strip().replace("\\", "/")
    if not cleaned or cleaned in {".", "/"}:
        return Path(".")

    pure = PurePosixPath(cleaned)
    if pure.is_absolute() or cleaned.startswith("/"):
        raise PathSecurityError(f"Absolute paths are not allowed: {rel!r}")
    if len(cleaned) > 1 and cleaned[1] == ":":
        raise PathSecurityError(f"Drive-qualified paths are not allowed: {rel!r}")

    parts: list[str] = []
    for part in pure.parts:
        if part in ("", "."):
            continue
        if part == "..":
            raise PathSecurityError(f"Path traversal is not allowed: {rel!r}")
        parts.append(part)

    return Path(*parts) if parts else Path(".")


def safe_path(root: str | Path, rel: str) -> Path:
    """Return the absolute path for ``rel`` inside ``root``.

    The returned path is guaranteed to be contained by ``root`` after
    resolution, so writes cannot escape the authorized repository.
    """
    root_resolved = Path(root).expanduser().resolve()
    relative = normalize_rel_path(rel)
    target = (root_resolved / relative).resolve()

    if not _is_within(target, root_resolved):
        raise PathSecurityError(
            f"Resolved path escapes repository root {str(root_resolved)!r}: {rel!r}"
        )
    return target


def _is_within(candidate: Path, root: Path) -> bool:
    try:
        candidate.relative_to(root)
    except ValueError:
        return False
    return True


def assert_authorized_root(root: str | Path, allowed_roots: list[str]) -> Path:
    """Verify a repository root is inside one of the configured roots.

    An empty ``allowed_roots`` list means the operator has not restricted the
    app yet; in that case any existing directory is accepted so the app is
    usable out of the box, but this is logged-worthy and documented.
    """
    resolved = Path(root).expanduser().resolve()
    if not resolved.exists():
        raise PathSecurityError(f"Repository path does not exist: {resolved}")
    if not resolved.is_dir():
        raise PathSecurityError(f"Repository path is not a directory: {resolved}")

    if not allowed_roots:
        return resolved

    for allowed in allowed_roots:
        allowed_resolved = Path(allowed).expanduser().resolve()
        if _is_within(resolved, allowed_resolved):
            return resolved

    raise PathSecurityError(
        f"Path {str(resolved)!r} is not inside any authorized workspace root."
    )


def to_rel_path(root: str | Path, absolute: Path) -> str:
    """Express ``absolute`` as a repo-relative POSIX path for API responses."""
    return Path(absolute).resolve().relative_to(Path(root).resolve()).as_posix()
