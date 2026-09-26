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


def assert_authorized_root(
    root: str | Path,
    allowed_roots: list[str],
    *,
    allow_unrestricted: bool = False,
) -> Path:
    """Verify a repository root is inside one of the configured roots.

    The candidate is resolved *before* the check, so a symlink pointing
    outside the allowed tree is rejected rather than followed, and ``..``
    segments are collapsed before containment is decided.

    An empty ``allowed_roots`` list means the operator has not configured any
    roots. That is a refusal, not a permission: registering a repository would
    otherwise expose an arbitrary directory on the host. The only exception is
    ``allow_unrestricted``, which the settings layer grants in development mode
    alone. Failing closed is deliberate -- an unconfigured deployment can serve
    no repository rather than serving every repository.
    """
    resolved = Path(root).expanduser().resolve()
    if not resolved.exists():
        raise PathSecurityError(f"Repository path does not exist: {resolved}")
    if not resolved.is_dir():
        raise PathSecurityError(f"Repository path is not a directory: {resolved}")

    if not allowed_roots:
        if allow_unrestricted:
            return resolved
        raise PathSecurityError(
            "No workspace roots are configured, so no repository can be "
            "authorized. Set ALLOWED_WORKSPACE_ROOTS to the directories that "
            "may be registered."
        )

    for allowed in allowed_roots:
        # The allowed root is resolved too, so a candidate that only matches
        # lexically (via a symlinked parent, say) is still caught.
        allowed_resolved = Path(allowed).expanduser().resolve()
        if _is_within(resolved, allowed_resolved):
            return resolved

    raise PathSecurityError(
        f"Path {str(resolved)!r} is not inside any authorized workspace root."
    )


def to_rel_path(root: str | Path, absolute: Path) -> str:
    """Express ``absolute`` as a repo-relative POSIX path for API responses."""
    return Path(absolute).resolve().relative_to(Path(root).resolve()).as_posix()
