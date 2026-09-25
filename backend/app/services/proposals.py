"""Change proposals: the only route by which documentation is ever modified.

Nothing in this module writes to disk while *planning*. A proposal is a plan --
affected files, reason, evidence, and a readable diff -- that the user must
explicitly accept. Even after acceptance nothing is committed: the working
tree is left dirty so the user can review and commit with Git themselves.

The design intent is that an AI response can never reach the filesystem
directly. It may only create a proposal, and applying that proposal is a
separate, human-initiated action.
"""
from __future__ import annotations

import difflib
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from app.services import git
from app.services.documents import DOC_SUFFIXES, DocumentError, read_document
from app.services.paths import PathSecurityError, safe_path


class ProposalError(Exception):
    """Raised when a proposal cannot be created or applied."""


@dataclass
class PlannedChange:
    """One concrete file operation belonging to a proposal."""

    action: str  # move | rename | edit | create
    source_path: str | None
    target_path: str
    diff: str = ""
    content_preview: str | None = None


def _assert_markdown(path: str, label: str) -> None:
    if Path(path).suffix.lower() not in DOC_SUFFIXES:
        raise ProposalError(f"{label} must be a Markdown file: {path!r}")


def plan_move(
    root: str | Path,
    source: str,
    target_dir: str,
    new_name: str | None = None,
    allow_missing_dir: bool = False,
) -> PlannedChange:
    """Plan moving (or renaming) a document. No filesystem mutation occurs.

    ``allow_missing_dir`` is set only by the inventory, whose job is to
    establish the target structure and therefore needs to be able to file a
    document into a folder that does not exist yet. A manually requested move
    into a non-existent folder is still refused, because it is more likely a
    typo than an intention.
    """
    _assert_markdown(source, "Source")
    if new_name is not None:
        _assert_markdown(new_name, "New name")
        if "/" in new_name or "\\" in new_name or new_name in (".", ".."):
            raise ProposalError("New name must be a plain file name.")

    try:
        src_path = safe_path(root, source)
        dest_dir = safe_path(root, target_dir or ".")
    except PathSecurityError as exc:
        raise ProposalError(str(exc)) from exc

    if not src_path.exists():
        raise ProposalError(f"Document not found: {source}")
    if src_path.is_dir():
        raise ProposalError(f"{source} is a directory, not a document.")
    if src_path.suffix.lower() not in DOC_SUFFIXES:
        raise ProposalError(f"Not a document file: {source}")

    if not allow_missing_dir:
        if not dest_dir.exists():
            raise ProposalError(f"Target directory does not exist: {target_dir}")
        if not dest_dir.is_dir():
            raise ProposalError(f"Target is not a directory: {target_dir}")

    filename = new_name or src_path.name
    if "/" in filename or "\\" in filename:
        raise ProposalError("Target filename must not contain path separators.")

    # Build the repo-RELATIVE target, not an absolute one: the target_dir and
    # filename are still untrusted and must pass through the sandbox themselves.
    relative_target = Path(target_dir or ".") / filename
    try:
        resolved_dest = safe_path(root, relative_target.as_posix())
    except PathSecurityError as exc:
        raise ProposalError(str(exc)) from exc

    if resolved_dest.exists():
        raise ProposalError(f"Target already exists: {relative_target.as_posix()}")

    target_rel = relative_target.as_posix()
    # A rename keeps the file in its existing folder; a move changes folders.
    source_parent = PurePosixPath(source.replace("\\", "/")).parent.as_posix()
    target_parent = PurePosixPath(target_rel).parent.as_posix()
    action = "rename" if source_parent == target_parent else "move"
    return PlannedChange(
        action=action,
        source_path=source,
        target_path=target_rel,
        diff=_render_rename_diff(source, target_rel),
    )


def plan_edit(root: str | Path, source: str, new_content: str) -> PlannedChange:
    """Plan replacing a document's contents, keeping the original via Git."""
    try:
        original = read_document(root, source)
    except (DocumentError, PathSecurityError) as exc:
        raise ProposalError(str(exc)) from exc

    if original == new_content:
        raise ProposalError("The proposed content is identical to the current file.")

    return PlannedChange(
        action="edit",
        source_path=source,
        target_path=source,
        diff=_render_unified_diff(source, original, new_content),
        content_preview=new_content,
    )


def plan_create(root: str | Path, target: str, content: str) -> PlannedChange:
    _assert_markdown(target, "New document")
    try:
        dest = safe_path(root, target)
    except PathSecurityError as exc:
        raise ProposalError(str(exc)) from exc
    if dest.exists():
        raise ProposalError(f"Target already exists: {target}")
    return PlannedChange(
        action="create",
        source_path=None,
        target_path=target,
        diff=_render_unified_diff(target, "", content),
        content_preview=content,
    )


# --------------------------------------------------------------------------
# Diff rendering
# --------------------------------------------------------------------------


def _render_unified_diff(path: str, before: str, after: str) -> str:
    """A readable diff of the document content, independent of Git state."""
    diff = difflib.unified_diff(
        before.splitlines(keepends=True),
        after.splitlines(keepends=True),
        fromfile=f"a/{path}",
        tofile=f"b/{path}",
        n=3,
    )
    return "".join(diff)


def _render_rename_diff(source: str, target: str) -> str:
    """A move has no content diff; show the path change and Git's view."""
    return (
        f"--- a/{source}\n"
        f"+++ b/{target}\n"
        f"@@ rename @@\n"
        f"-# moved from {source}\n"
        f"+# moved to {target}\n"
        f"(content unchanged; Git will record this as a rename)"
    )


# --------------------------------------------------------------------------
# Application (only ever called from an explicit user acceptance)
# --------------------------------------------------------------------------


def apply_change(root: str | Path, change: PlannedChange) -> str:
    """Perform a planned change. Callers MUST have obtained user approval.

    Refuses to move, rename or overwrite a file that Git does not track,
    because the original would not be recoverable -- overwriting must always
    preserve the original through Git.
    """
    root_str = str(root)

    if change.action in ("move", "rename"):
        source = change.source_path or ""
        if not git.is_repo(root_str) or not git.is_tracked(root_str, source):
            raise ProposalError(
                "Refusing to move an untracked document: the original would not be "
                "preserved through Git. Commit it first."
            )
        src = safe_path(root_str, source)
        dest = safe_path(root_str, change.target_path)
        dest.parent.mkdir(parents=True, exist_ok=True)
        src.replace(dest)
        return change.target_path

    if change.action == "create":
        dest = safe_path(root_str, change.target_path)
        dest.parent.mkdir(parents=True, exist_ok=True)
        _atomic_write(dest, change.content_preview or "")
        return change.target_path

    if change.action == "edit":
        target = safe_path(root_str, change.target_path)
        if not git.is_repo(root_str) or not git.is_tracked(root_str, change.target_path):
            raise ProposalError(
                "Refusing to overwrite an untracked document: the original would not "
                "be preserved through Git. Commit it first."
            )
        _atomic_write(target, change.content_preview or "")
        return change.target_path

    raise ProposalError(f"Unknown change action: {change.action!r}")


def _atomic_write(target: Path, content: str) -> None:
    """Write via a temp file and replace, so a crash cannot truncate a document."""
    tmp = target.with_name(target.name + ".tmp-gaia")
    tmp.write_text(content, encoding="utf-8")
    tmp.replace(target)
