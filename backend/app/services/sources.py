"""Repository sources: the unified abstraction over local and GitHub repos.

A *source* is a code repository registered as architecture evidence. It reuses
the existing :class:`app.models.Repository` row (kind ``"source"``); this module
adds the behaviour that makes local Git repositories and GitHub URLs behave the
same way from the rest of the application's point of view:

* every source has a stable identity (a normalized URL or a resolved local path),
  which is what idempotency and duplicate detection are built on;
* GitHub sources are cloned into a **managed checkout directory** on first sync
  and pulled (fast-forward only) afterwards -- the remote is never modified;
* local sources are never cloned or copied, exactly like documentation repos.

Nothing here stores credentials: GitHub sources are read-only, public-first.
If a private repository requires authentication, the sync fails with a clear
message instead of inventing insecure credential handling.
"""
from __future__ import annotations

import datetime as dt
import re
from pathlib import Path
from urllib.parse import urlsplit

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.models import Repository, Workspace
from app.services import git
from app.services.paths import PathSecurityError, assert_authorized_root


class SourceError(ValueError):
    """Raised for user-correctable source problems (bad URL, name, path)."""


#: Substrings in Git's stderr that mean "this remote wants credentials". They
#: are matched case-insensitively; a match produces the private-repository
#: message rather than a raw Git error.
_AUTH_FAILURE_MARKERS = (
    "authentication failed",
    "could not read username",
    "terminal prompts disabled",
    "permission denied",
    "403",
    "repository not found",
)

_URL_SCHEMES = ("http", "https")


def classify_location(location: str) -> str:
    """Decide whether ``location`` is a remote URL or a local path.

    Any ``scheme://`` form counts as remote so that non-http schemes (``ftp:``,
    ``file:``) are rejected by :func:`normalize_repo_url` instead of being
    silently treated as local paths.
    """
    cleaned = (location or "").strip()
    if re.match(r"^[A-Za-z][A-Za-z0-9+.-]*://", cleaned):
        return "github"
    return "local"


def normalize_repo_url(url: str) -> str:
    """Normalize a Git repository URL for identity comparison.

    Deliberately narrow: only the ``.git`` suffix, trailing slashes and scheme /
    host casing are normalized, so ``https://github.com/O/R`` and
    ``https://github.com/O/R.git`` compare equal while two genuinely different
    URLs never collapse together. Embedded credentials, query strings and
    fragments are rejected outright -- a URL carrying a token is a
    misconfiguration, not something to quietly strip and keep.
    """
    raw = (url or "").strip()
    if not raw:
        raise SourceError("A repository URL is required.")
    if "\x00" in raw:
        raise SourceError("A repository URL cannot contain NUL bytes.")

    if raw.endswith(".git"):
        raw = raw[:-4]

    parts = urlsplit(raw)
    scheme = parts.scheme.lower()
    if scheme not in _URL_SCHEMES:
        raise SourceError(f"Invalid repository URL {url!r}: only http(s) URLs are supported.")
    if not parts.hostname:
        raise SourceError(f"Invalid repository URL {url!r}: no host.")
    if parts.username or parts.password:
        raise SourceError(
            f"Invalid repository URL {url!r}: embedded credentials are not allowed. "
            "Credentials must never be stored in .sources.yaml."
        )
    if parts.query or parts.fragment:
        raise SourceError(
            f"Invalid repository URL {url!r}: query strings and fragments are not allowed."
        )

    host = parts.hostname.lower()
    path = parts.path.rstrip("/")
    if not path or path == "/":
        raise SourceError(f"Invalid repository URL {url!r}: no repository path.")
    return f"{scheme}://{host}{path}"


def local_identity(path: str) -> str:
    """The stable identity of a local source: its resolved absolute path."""
    return str(Path(path).expanduser().resolve())


def source_identity(source_type: str, location: str) -> str:
    if source_type == "github":
        return normalize_repo_url(location)
    return local_identity(location)


def slug_for(url: str, fallback: str = "repo") -> str:
    """A filesystem-safe directory name derived from the repository URL."""
    parts = [p for p in urlsplit(url).path.split("/") if p]
    if parts:
        slug = "-".join(parts[-2:])
    else:
        slug = fallback
    slug = re.sub(r"[^A-Za-z0-9._-]+", "-", slug).strip("-.")
    return slug or fallback


def managed_checkout_root() -> Path:
    """The machine-specific root under which GitHub checkouts are cloned."""
    return Path(settings.effective_source_checkout_root)


def managed_checkout_path(workspace_id: int, url: str, name: str) -> Path:
    return managed_checkout_root() / f"workspace_{workspace_id}" / slug_for(url, name)


def find_existing_source(
    db: Session, workspace_id: int, source_type: str, identity: str
) -> Repository | None:
    """Find a source with the same identity, whatever its display name is."""
    repos = db.scalars(
        select(Repository).where(
            Repository.workspace_id == workspace_id, Repository.kind == "source"
        )
    ).all()
    for repo in repos:
        if repo.source_type != source_type:
            continue
        if source_type == "github":
            if repo.source_url and normalize_repo_url(repo.source_url) == identity:
                return repo
        elif local_identity(repo.local_path) == identity:
            return repo
    return None


def _unique_name(db: Session, workspace_id: int, desired: str) -> str:
    existing = set(
        db.scalars(
            select(Repository.name).where(Repository.workspace_id == workspace_id)
        ).all()
    )
    if desired not in existing:
        return desired
    n = 2
    while f"{desired} ({n})" in existing:
        n += 1
    return f"{desired} ({n})"


def register_source(
    db: Session,
    workspace: Workspace,
    *,
    name: str,
    location: str,
    source_type: str | None = None,
    branch: str | None = None,
    description: str | None = None,
    idempotent: bool = False,
    dedupe_name: bool = False,
) -> Repository:
    """Register (or, with ``idempotent``, return) a repository source.

    ``location`` is either a local filesystem path or an http(s) Git URL; the
    type is inferred when not given explicitly. Identity, not the display name,
    decides whether the source already exists.
    """
    location = (location or "").strip()
    if not location:
        raise SourceError("A source location (local path or repository URL) is required.")
    stype = source_type or classify_location(location)
    if stype not in ("local", "github"):
        raise SourceError(f"Unknown source type {stype!r}.")

    clean_name = (name or "").strip()
    if not clean_name:
        raise SourceError("A source name is required.")

    if stype == "github":
        identity = normalize_repo_url(location)
    else:
        identity = local_identity(location)

    existing = find_existing_source(db, workspace.id, stype, identity)
    if existing is not None:
        if idempotent:
            return existing
        raise SourceError(
            f"This repository is already registered as {existing.name!r}."
        )

    if dedupe_name:
        clean_name = _unique_name(db, workspace.id, clean_name)
    else:
        clash = db.scalar(
            select(Repository).where(
                Repository.workspace_id == workspace.id, Repository.name == clean_name
            )
        )
        if clash is not None:
            raise SourceError(f"A repository named {clean_name!r} already exists.")

    if stype == "github":
        checkout = managed_checkout_path(workspace.id, identity, clean_name)
        repo = Repository(
            workspace_id=workspace.id,
            name=clean_name,
            local_path=str(checkout),
            branch=(branch or "main").strip() or "main",
            kind="source",
            writable=False,
            description=description,
            source_type="github",
            source_url=identity,
            status="pending",
            status_message="Not synchronized yet.",
        )
    else:
        try:
            root = assert_authorized_root(
                location,
                settings.allowed_workspace_roots,
                allow_unrestricted=settings.unrestricted_workspace_roots,
            )
        except PathSecurityError as exc:
            raise SourceError(str(exc)) from exc
        resolved_branch = (branch or "").strip()
        if not resolved_branch and git.is_repo(str(root)):
            resolved_branch = git.current_branch(str(root)) or "main"
        repo = Repository(
            workspace_id=workspace.id,
            name=clean_name,
            local_path=str(root),
            branch=resolved_branch or "main",
            kind="source",
            writable=False,
            description=description,
            source_type="local",
            source_url=None,
            status="ready",
            status_message=None,
        )

    db.add(repo)
    db.flush()
    return repo


def _auth_failure_message(detail: str) -> str | None:
    lowered = detail.lower()
    if any(marker in lowered for marker in _AUTH_FAILURE_MARKERS):
        return (
            "GitHub could not fetch this repository. If it is private, note that "
            "private repository authentication is not supported in this milestone: "
            "only public repositories can be synchronized, and credentials are "
            "never stored in .sources.yaml. If the repository is public, check "
            "the URL. Original error: " + detail
        )
    return None


def sync_source(repo: Repository) -> dict:
    """Synchronize a source. Clone on first sync, fast-forward pull afterwards.

    Never pushes, never commits, never touches the remote: GitHub sources are
    evidence, read-only by design.
    """
    if repo.kind != "source":
        raise SourceError("Only source repositories can be synchronized.")

    if repo.source_type == "local":
        status, message = effective_status(repo)
        repo.status = status
        repo.status_message = message
        return {"action": "refreshed", "status": status, "message": message}

    if not repo.source_url:
        raise SourceError("This GitHub source has no repository URL recorded.")
    # The URL was normalized at registration; the stored identity is trusted
    # here so a row created by an operator or a migration is still syncable.
    url = repo.source_url
    checkout = Path(repo.local_path)

    if checkout.exists() and git.is_repo(checkout):
        action = "updated"
        try:
            git.pull_ff_only(checkout)
        except git.GitError as exc:
            raise _sync_failure(repo, str(exc)) from exc
    else:
        action = "cloned"
        checkout.parent.mkdir(parents=True, exist_ok=True)
        try:
            git.clone(url, checkout)
        except git.GitError as exc:
            raise _sync_failure(repo, str(exc)) from exc

    repo.status = "ready"
    repo.status_message = None
    repo.last_synced_at = dt.datetime.now(dt.timezone.utc)
    repo.branch = git.current_branch(checkout) or repo.branch
    return {
        "action": action,
        "status": "ready",
        "message": None,
        "branch": git.current_branch(checkout),
        "revision": git.head_revision(checkout),
    }


def _sync_failure(repo: Repository, detail: str) -> Exception:
    repo.status = "error"
    message = _auth_failure_message(detail) or f"Git synchronization failed: {detail}"
    repo.status_message = message
    return SourceError(message)


def effective_status(repo: Repository) -> tuple[str, str | None]:
    """The source's current status and a human-readable explanation.

    Computed live from the filesystem rather than trusted from the database, so
    a checkout deleted behind our back or a local path that moved is reported
    as what it is instead of quietly 400-ing every listing.
    """
    if repo.kind != "source":
        return "ready", None

    if repo.source_type == "github":
        checkout = Path(repo.local_path).expanduser()
        if checkout.exists() and git.is_repo(checkout):
            return "ready", None
        if repo.status == "error":
            return "error", repo.status_message
        return "pending", repo.status_message or "Not synchronized yet."

    local = Path(repo.local_path).expanduser()
    if not local.exists():
        return "missing", "The local path does not exist (or is not readable)."
    if repo.status == "error":
        return "error", repo.status_message
    return "ready", None


# ---------------------------------------------------------------------------
# Workspace manifest (.sources.yaml)
# ---------------------------------------------------------------------------


def manifest_entries(db: Session, workspace: Workspace) -> list[dict[str, str]]:
    """The portable description of a workspace's configured sources."""
    repos = db.scalars(
        select(Repository)
        .where(Repository.workspace_id == workspace.id, Repository.kind == "source")
        .order_by(Repository.id)
    ).all()
    entries: list[dict[str, str]] = []
    for repo in repos:
        entries.append({"repo": repo.name, "path": repo.source_url or repo.local_path})
    return entries


def manifest_path_for(workspace_id: int) -> Path:
    """Where the workspace's .sources.yaml is retained on this machine.

    Lives under the managed checkout root, deliberately outside any user
    repository: local checkout paths are machine-specific configuration, while
    the manifest itself stays a portable description of source identity.
    """
    return managed_checkout_root() / f"workspace_{workspace_id}" / ".sources.yaml"


def persist_manifest(db: Session, workspace: Workspace) -> Path | None:
    """Write the workspace's .sources.yaml next to the managed checkouts."""
    from app.services.manifest import render_manifest

    target = manifest_path_for(workspace.id)
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(render_manifest(db, workspace), encoding="utf-8")
    except OSError:
        return None
    return target
