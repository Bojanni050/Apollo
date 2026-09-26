"""Thin, read-only Git helpers.

Only a fixed set of non-destructive commands is ever constructed here, each
with an argument list (never a shell string), so an AI-generated value can
never become a shell command. Nothing in this module commits, checks out,
resets, or otherwise mutates history.
"""
from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path

TIMEOUT_SECONDS = 20

#: Cloning a repository can legitimately take minutes; pulls are usually fast
#: but still touch the network. A separate, larger ceiling keeps the interactive
#: helpers snappy while allowing a real clone to finish.
SYNC_TIMEOUT_SECONDS = 300


class GitError(RuntimeError):
    pass


@dataclass
class GitStatusEntry:
    path: str
    status: str  # M, A, D, R, ?


def _run(repo: str | Path, args: list[str]) -> str:
    cmd = ["git", "-C", str(repo), *args]
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=TIMEOUT_SECONDS,
            check=False,
        )
    except FileNotFoundError as exc:
        raise GitError("git executable not found on PATH.") from exc
    except subprocess.TimeoutExpired as exc:
        raise GitError("git command timed out.") from exc

    if proc.returncode != 0:
        raise GitError((proc.stderr or proc.stdout).strip() or "git command failed")
    return proc.stdout


def is_repo(repo: str | Path) -> bool:
    try:
        return _run(repo, ["rev-parse", "--is-inside-work-tree"]).strip() == "true"
    except GitError:
        return False


def current_branch(repo: str | Path) -> str | None:
    try:
        return _run(repo, ["rev-parse", "--abbrev-ref", "HEAD"]).strip()
    except GitError:
        return None


def head_revision(repo: str | Path) -> str | None:
    try:
        return _run(repo, ["rev-parse", "HEAD"]).strip()
    except GitError:
        return None


def status(repo: str | Path) -> list[GitStatusEntry]:
    """Porcelain status of the working tree, used by the 'inspect changes' view."""
    out = _run(repo, ["status", "--porcelain=v1", "--untracked-files=all"])
    entries: list[GitStatusEntry] = []
    for line in out.splitlines():
        if len(line) < 4:
            continue
        code, path = line[:2].strip(), line[3:].strip()
        if " -> " in path:  # rename
            path = path.split(" -> ", 1)[1]
        entries.append(GitStatusEntry(path=path, status=code or "?"))
    return entries


def diff(repo: str | Path, path: str | None = None, staged: bool = False) -> str:
    args = ["diff", "--no-color"]
    if staged:
        args.append("--staged")
    if path:
        args.extend(["--", path])
    return _run(repo, args)


def recent_log(repo: str | Path, limit: int = 10) -> list[dict[str, str]]:
    limit = max(1, min(int(limit), 100))
    out = _run(repo, ["log", f"-{limit}", "--pretty=format:%H%x1f%an%x1f%ad%x1f%s", "--date=short"])
    commits: list[dict[str, str]] = []
    for line in out.splitlines():
        parts = line.split("\x1f")
        if len(parts) == 4:
            commits.append(
                {"revision": parts[0], "author": parts[1], "date": parts[2], "subject": parts[3]}
            )
    return commits


def is_tracked(repo: str | Path, path: str) -> bool:
    """True when Git tracks the path -- the precondition for safe overwrites."""
    try:
        _run(repo, ["ls-files", "--error-unmatch", "--", path])
        return True
    except GitError:
        return False


# ---------------------------------------------------------------------------
# Synchronization (source repositories only)
# ---------------------------------------------------------------------------
#
# These are the *only* mutating Git commands in the application. They are used
# exclusively by services/sources.py to clone or fast-forward pull a source
# repository into the managed checkout directory. They never commit, never
# push, and never modify the remote: a source repository is evidence.


def clone(url: str, destination: Path, branch: str | None = None) -> str:
    """Clone ``url`` into ``destination``.

    The URL comes from validated input, never from the model, and is passed as
    an argument -- never a shell string. No credentials are attached; an
    unreachable or private repository simply fails with Git's own error, which
    services.sources translates into an operator-readable message.
    """
    args = ["clone", "--quiet"]
    if branch:
        args.extend(["--branch", branch])
    args.extend([str(url), str(destination)])
    return _run_standalone(args)


def pull_ff_only(repo: str | Path) -> str:
    """Fast-forward only pull: refuses to create commits or merge anything."""
    return _run(repo, ["pull", "--quiet", "--ff-only"])


def _run_standalone(args: list[str]) -> str:
    """``git -C`` does not work before the directory exists, so clone runs
    without it -- with the same fixed-argument discipline as ``_run``."""
    cmd = ["git", *args]
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=SYNC_TIMEOUT_SECONDS,
            check=False,
        )
    except FileNotFoundError as exc:
        raise GitError("git executable not found on PATH.") from exc
    except subprocess.TimeoutExpired as exc:
        raise GitError("git clone timed out.") from exc
    if proc.returncode != 0:
        raise GitError((proc.stderr or proc.stdout).strip() or "git clone failed")
    return proc.stdout
