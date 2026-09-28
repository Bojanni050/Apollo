"""Thin Git helpers.

Read-only by default: only a fixed set of non-destructive commands is ever
constructed here, each with an argument list (never a shell string), so an
AI-generated value can never become a shell command. Nothing here checks out,
resets, or otherwise rewrites history.

The single exception is :func:`commit_paths`, added because the write path
refuses to overwrite a document Git does not track -- correctly, since the
original would be unrecoverable -- and a repository with no commits yet had no
way out of that refusal from inside the app. It is deliberately narrow: an
explicit list of paths, no ``-A``/``.`` wildcards, no amend, no push, and the
caller states which paths it wants recorded.
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


def untracked_paths(repo: str | Path) -> list[str]:
    """Every file in the working tree that Git does not track yet.

    Used to offer "commit these" where a refusal would otherwise be a dead end.
    Includes ignored files? No -- ``--others`` without ``--exclude-standard``
    would list node_modules and build output, which nobody wants committed by
    accident. Standard exclusions apply, so this is the set a human would stage.
    """
    out = _run(repo, ["ls-files", "--others", "--exclude-standard"])
    return [line.strip() for line in out.splitlines() if line.strip()]


# ---------------------------------------------------------------------------
# Recording the current state
# ---------------------------------------------------------------------------


def commit_paths(repo: str | Path, paths: list[str], message: str) -> str | None:
    """Stage exactly ``paths`` and commit them. Returns the new revision.

    This is the one function here that writes history, and the narrowness is
    the whole point:

    * only the given paths are staged -- never ``-A``/``.``, so committing the
      open document cannot quietly sweep in a build directory or a stray editor
      backup;
    * no ``--amend``, so an existing commit is never rewritten;
    * no ``push``, so nothing leaves the machine;
    * a message is required, so no commit is ever left with Git's default
      "Update file" prose.

    Returns None when there is nothing to commit -- the caller treats that as
    "already recorded" rather than as a failure, because the user's intent
    (keep the original recoverable) is satisfied either way.
    """
    if not paths:
        return None
    if not message.strip():
        raise GitError("A commit needs a message.")

    # `--` before the paths ends option parsing, so a path beginning with a dash
    # is a path and not a flag.
    _run(repo, ["add", "--", *paths])
    staged = _run(repo, ["diff", "--staged", "--name-only"])
    if not staged.strip():
        return None
    _run(repo, ["commit", "--quiet", "-m", message, "--", *paths])
    return head_revision(repo)


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
