"""Apollo's own storage: where documents dropped in from the desktop live.

A *registered repository* is a folder the operator already had. Apollo reads from
it, and writes to it only through the approval-gated proposal flow, because
somebody else owns that folder and its history. A *dropped-in document* has no
such home, so the application keeps one for it, under its own storage root, in a
directory whose name says what it is: ``Inbox``.

Three rules hold here:

* **Nothing is ever deleted.** There is no ``unlink`` and no ``rmtree`` in this
  module and no code path that reaches one. A name that is already taken gets a
  number instead of an overwrite, because the alternative to a second copy of
  ``verslag.pdf`` is a lost first one.
* **A file lands whole or not at all.** An upload is written to a temporary name
  in the same directory and then moved into place, so an interrupted write cannot
  leave a truncated document that still looks readable.
* **A stored document is never silently dropped.** A file Apollo cannot read back
  is stored anyway and reported as unreadable. Refusing it after the bytes
  arrived would leave the reader with neither the file nor an explanation of what
  happened to it.

Layout: ``<storage root>/workspace_<id>/Inbox/<document>``. One directory per
workspace, matching the managed checkout root, so two application-owned trees do
not look like two different designs. Only ``Inbox`` is created here: the folders
that give a collection its shape are a decision made later, and the archive
folder will take its name from the archive group rather than from a second
constant.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path

from app.config import settings
from app.services.documents import (
    DOC_SUFFIXES,
    MAX_READ_BYTES,
    DocumentError,
    read_document,
)
from app.services.paths import PathSecurityError

#: The one folder documents are dropped into. A constant rather than a literal
#: because the API returns it and the tests assert on it.
INBOX_DIR = "Inbox"

#: Suffix for an in-progress write. Recognisable on the filesystem as unfinished,
#: and excluded from every listing by not being a document suffix.
TEMP_SUFFIX = ".tmp-apollo"

#: Uploads and reads share one ceiling. Two numbers would eventually disagree,
#: and the disagreement would be a file that can be stored but never opened --
#: which reads to the person who dropped it as data loss, not as a limit.
MAX_UPLOAD_BYTES = MAX_READ_BYTES

#: Characters that cannot appear in a filename on at least one supported
#: platform, plus control characters. Replaced rather than removed so a name
#: stays recognisable to the person who dropped the file.
_UNSAFE_NAME_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')

#: Names Windows reserves for devices, with or without an extension: creating
#: ``CON.md`` fails there, and the storage root may be on either platform.
_RESERVED_NAMES = frozenset(
    {"CON", "PRN", "AUX", "NUL"}
    | {f"COM{i}" for i in range(1, 10)}
    | {f"LPT{i}" for i in range(1, 10)}
)


class StorageError(Exception):
    """Raised when a dropped-in document cannot be stored.

    Every message is written for the person who dropped the file: they say what
    was wrong with it and what is accepted instead.
    """


@dataclass(frozen=True)
class StoredDocument:
    """What one stored upload looks like to the caller."""

    #: Repository-relative, so it can be handed straight to the reading pane.
    path: str
    name: str
    size: int
    #: Whether Apollo can read the file back. False means stored-but-unopenable.
    readable: bool
    unreadable_reason: str | None = None


@dataclass(frozen=True)
class InboxEntry:
    """One document sitting in the inbox."""

    path: str
    name: str
    size: int


@dataclass(frozen=True)
class ImportedFile:
    """One document copied out of a folder the reader offered."""

    #: Absolute path in the source folder, exactly as the reader would name it.
    source_path: str
    #: Repository-relative, so the reading pane can open it like any other.
    path: str
    name: str
    size: int
    readable: bool
    unreadable_reason: str | None = None


@dataclass(frozen=True)
class RefusedFile:
    """One document that was not copied, and why.

    Refused rather than skipped: a folder added as a source is read once, and a
    file that silently did not arrive is a document the reader believes Apollo
    has.
    """

    source_path: str
    reason: str


@dataclass(frozen=True)
class FolderImport:
    """What adding a folder as a source did.

    ``truncated`` is the honest part: a folder with more documents than
    :data:`MAX_IMPORT_FILES` is only partly copied, and the reader is told
    instead of being left with a collection that looks complete.
    """

    folder_name: str
    found: int
    copied: list[ImportedFile]
    refused: list[RefusedFile]
    truncated: bool = False


#: Documents copied from one offered folder, at most. A cap rather than a refusal:
#: a project folder with a thousand documents is worth having a hundred of, and
#: the reader is told it was only partly copied instead of being left to believe
#: Apollo has the lot.
MAX_IMPORT_FILES = 500

#: Directories never walked when reading an offered folder. Every one of these is
#: either not the reader's work (a nested repository, a build output) or not
#: documents at all (dependencies), and walking into them would spend the import
#: budget on thousands of files nobody asked for. Hidden directories are skipped
#: by the same rule, which is why this list is short rather than exhaustive.
_SKIP_DIRS = frozenset(
    {"node_modules", "__pycache__", ".venv", "venv", "dist", "build", "target"}
)


def storage_root() -> Path:
    """The directory Apollo owns. Not created here: only an upload creates it."""
    return Path(settings.effective_storage_root)


def workspace_storage(workspace_id: int, working_dir: str | Path | None = None) -> Path:
    """Where one workspace's own documents live.

    The folder the reader chose, when they chose one. Otherwise the folder this
    application invents, which is what every workspace did before the choice
    existed and still does by default: a workspace that has never been configured
    keeps behaving exactly as it did, rather than being moved somewhere on the
    strength of a column nobody filled in.
    """
    if working_dir:
        return Path(working_dir)
    return storage_root() / f"workspace_{workspace_id}"


def workspace_inbox(workspace_id: int, working_dir: str | Path | None = None) -> Path:
    """The workspace's inbox directory. May not exist yet."""
    return workspace_storage(workspace_id, working_dir) / INBOX_DIR


def ensure_inbox(workspace_id: int, working_dir: str | Path | None = None) -> Path:
    """Create the inbox directory, and nothing beside it.

    Creating the parent is unavoidable -- the workspace directory is part of the
    layout -- but a folder per category is not: an empty ``Projecten`` for a
    category nobody has used is a claim about the reader's documents that the
    reader did not make.
    """
    inbox = workspace_inbox(workspace_id, working_dir)
    try:
        inbox.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        # A folder the process cannot write to is a configuration problem the
        # reader can act on (a wrong path, a read-only disk, a folder that is
        # really a file), so it is reported as one rather than surfacing as a
        # server error.
        raise StorageError(f"Could not create the inbox folder: {exc}") from exc
    return inbox


def sanitize_filename(filename: str) -> str:
    """Reduce a dropped filename to something safe to create.

    Only the final component is kept, so ``../../etc/passwd`` becomes ``passwd``:
    the traversal is not cleaned up but discarded. That is a different guarantee
    from :func:`app.services.paths.safe_path`, which refuses a traversal -- here
    the name never becomes a path at all, because the directory is chosen by the
    application and only the name comes from the caller.
    """
    base = (filename or "").replace("\\", "/").split("/")[-1].strip()
    base = _UNSAFE_NAME_CHARS.sub("-", base)
    # A trailing dot or space is legal on Linux and not on Windows; stripping it
    # keeps one name working on both.
    base = base.rstrip(". ")
    if base in ("", ".", ".."):
        raise StorageError("The dropped file has no usable name.")

    stem, dot, suffix = base.rpartition(".")
    if not dot:
        stem, suffix = base, ""
    if stem.upper() in _RESERVED_NAMES:
        stem = f"{stem}-file"
    # A name that is nothing but a suffix (``.md``) has no document name in it.
    if not stem:
        raise StorageError("The dropped file has no usable name.")
    return f"{stem}.{suffix}" if suffix else stem


def _check_suffix(name: str) -> str:
    """The document suffix, or a refusal that names what is accepted."""
    suffix = Path(name).suffix.lower()
    if suffix not in DOC_SUFFIXES:
        accepted = ", ".join(sorted(DOC_SUFFIXES))
        raise StorageError(
            f"{name} is not a document Apollo can read. Accepted: {accepted}."
        )
    return suffix


def _unique_target(directory: Path, name: str) -> Path:
    """The path to store under: ``name``, or the first free numbered variant.

    Never an overwrite. Dropping a second ``verslag.pdf`` is a plausible thing to
    do deliberately -- a new draft under the same name -- and silently replacing
    the first one would destroy a document the reader never asked to lose.
    """
    candidate = directory / name
    if not candidate.exists():
        return candidate
    stem, dot, suffix = name.rpartition(".")
    if not dot:
        stem, suffix = name, ""
    index = 2
    while True:
        numbered = f"{stem}-{index}.{suffix}" if suffix else f"{stem}-{index}"
        candidate = directory / numbered
        if not candidate.exists():
            return candidate
        index += 1


def _atomic_write_bytes(target: Path, data: bytes) -> None:
    """Write via a temporary file and replace, so a crash cannot truncate.

    The temporary file is a sibling rather than a system temp file, so the
    replace is a rename inside one directory and therefore atomic on every
    supported platform.
    """
    tmp = target.with_name(target.name + TEMP_SUFFIX)
    try:
        tmp.write_bytes(data)
        tmp.replace(target)
    except OSError as exc:
        # A leftover temporary file is not a document and is never listed, but
        # leaving one behind on a failed write would accumulate quietly.
        tmp.unlink(missing_ok=True)
        raise StorageError(f"Could not write {target.name}: {exc}") from exc


def store_upload(
    workspace_id: int,
    filename: str,
    data: bytes,
    working_dir: str | Path | None = None,
) -> StoredDocument:
    """Store one dropped-in document in the workspace's inbox.

    Raises :class:`StorageError` for everything that can be judged without
    writing: an unusable name, a format that is not a document, an empty file, or
    a file above the size ceiling. Nothing is created on disk in those cases, so
    a refused drop leaves the working folder exactly as it was.

    Everything else is written and then read back. An unreadable file is
    *reported*, not removed: the bytes arrived, and throwing them away would
    leave the reader with neither the document nor a reason.
    """
    name = sanitize_filename(filename)
    _check_suffix(name)
    if not data:
        raise StorageError(f"{name} is empty.")
    if len(data) > MAX_UPLOAD_BYTES:
        megabytes = MAX_UPLOAD_BYTES // (1024 * 1024)
        raise StorageError(
            f"{name} is larger than {megabytes} MB, which is the most Apollo "
            "stores. Split the file, or leave it where it is and register that "
            "folder as a repository instead."
        )

    inbox = ensure_inbox(workspace_id, working_dir)
    target = _unique_target(inbox, name)
    _atomic_write_bytes(target, data)

    rel = f"{INBOX_DIR}/{target.name}"
    readable, reason = True, None
    try:
        read_document(workspace_storage(workspace_id, working_dir), rel)
    except (DocumentError, PathSecurityError) as exc:
        readable, reason = False, str(exc)

    # Recorded, so that a move of this document can ever be undone. Nothing when
    # the folder is not a repository, which is the case for the default storage.
    record_added(
        workspace_storage(workspace_id, working_dir),
        [rel],
        f"Added by Apollo: {target.name}",
    )

    return StoredDocument(
        path=rel,
        name=target.name,
        size=len(data),
        readable=readable,
        unreadable_reason=reason,
    )


def list_inbox(
    workspace_id: int, working_dir: str | Path | None = None
) -> list[InboxEntry]:
    """The documents in the inbox, by path.

    A missing directory is an empty inbox rather than an error: before the first
    upload there is genuinely nothing there, and a 404 would make the interface
    explain a problem that does not exist.

    Recursive, because a folder offered as a source arrives as a folder. The
    earlier flat listing was right for a drop -- one file, one row -- but a project
    copied in whole would lose the one piece of information the reader already had
    and Apollo cannot recover: which subfolder a document came from. Grouping is
    the answer to "where do these belong", never a reason to throw away where they
    were.
    """
    inbox = workspace_inbox(workspace_id, working_dir)
    if not inbox.is_dir():
        return []
    entries: list[InboxEntry] = []
    for path in sorted(
        inbox.rglob("*"), key=lambda p: p.relative_to(inbox).as_posix().lower()
    ):
        if not path.is_file() or path.name.endswith(TEMP_SUFFIX):
            continue
        if path.suffix.lower() not in DOC_SUFFIXES:
            # Only documents are listed, using the same definition of "document"
            # the rest of the application reads with. A stray file is still on
            # disk and still untouched; it is simply not offered as readable.
            continue
        entries.append(
            InboxEntry(
                path=f"{INBOX_DIR}/{path.relative_to(inbox).as_posix()}",
                name=path.name,
                size=path.stat().st_size,
            )
        )
    return entries


def _is_within(inner: Path, outer: Path) -> bool:
    """True when ``inner`` is ``outer`` or sits under it."""
    try:
        inner.resolve().relative_to(outer.resolve())
        return True
    except (ValueError, OSError):
        return False


def _walk_documents(source: Path) -> list[Path]:
    """Every file under ``source`` that is worth reading, in a stable order.

    Sorted by the path relative to the source, so the same folder imported twice
    reports the same list in the same order, and a folder's own files come out
    together rather than interleaved with its subfolders' by the accident of how
    the walk descends.

    Symlinks are skipped rather than followed: a link is a way out of the folder
    the reader offered, and following one could walk back into Apollo's own
    storage and copy that into itself.
    """
    found: list[Path] = []
    for dirpath, dirnames, filenames in os.walk(source, followlinks=False):
        dirnames[:] = sorted(
            d for d in dirnames if not d.startswith(".") and d not in _SKIP_DIRS
        )
        for filename in sorted(filenames):
            candidate = Path(dirpath) / filename
            if candidate.is_symlink() or not candidate.is_file():
                continue
            found.append(candidate)
    return sorted(found, key=lambda p: p.relative_to(source).as_posix().lower())


def import_folder(
    workspace_id: int, source: str | Path, working_dir: str | Path | None = None
) -> FolderImport:
    """Copy an offered folder's documents into the inbox. The folder is read.

    The reader points at a folder they already have, and Apollo copies what is in
    it. Three things follow from "copied", and together they are the design:

    * **The source is never written to.** Nothing here opens the source for
      writing, moves anything out of it, or renames anything in it. The reader's
      own project keeps its own layout whatever happens in here.
    * **The structure is kept, under the folder's name.** ``Inbox/Project/notes/x``
      rather than one flat pile, because a collection that has lost where its
      documents came from cannot be organised afterwards.
    * **Nothing is overwritten.** A name already taken gets a number, exactly as a
      dropped file does.

    One thing is refused outright: a folder that overlaps Apollo's own storage in
    either direction. Pointing at the working folder copies Apollo's output into
    itself; pointing at a folder *containing* it walks into it. Both are a mistyped
    path rather than an intention, and both fail silently otherwise -- a
    self-copying import looks exactly like a successful one.
    """
    raw = str(source or "").strip()
    if not raw:
        raise StorageError("No folder was named.")

    origin = Path(raw).expanduser()
    origin = origin.resolve() if origin.is_absolute() else (Path.cwd() / origin).resolve()

    # The overlap checks come before the existence check, and that order is the
    # point: pointing at the workspace's own folder is a real mistake, and "that
    # folder does not exist" would be a confusing answer to it. The checks work on
    # paths that do not exist yet, so the message is always the useful one.
    #
    # Both places count, not just Apollo's storage: a workspace whose working
    # folder is somewhere else entirely would otherwise be able to import itself.
    for base, complaint in (
        (storage_root().resolve(), "That is Apollo's own storage folder."),
        (
            workspace_storage(workspace_id, working_dir).resolve(),
            "That is this workspace's own working folder.",
        ),
    ):
        if _is_within(origin, base):
            raise StorageError(
                f"{complaint} Add the folder your documents live in, not the one "
                "Apollo keeps them in."
            )
        if _is_within(base, origin):
            raise StorageError(
                "That folder contains Apollo's own storage. Pick the folder your "
                "documents are in, not one that happens to contain Apollo's."
            )

    if not origin.exists():
        raise StorageError(f"That folder does not exist: {raw}")
    if not origin.is_dir():
        raise StorageError(f"{raw} is a file, not a folder.")

    folder_name = sanitize_filename(origin.name or "map")
    candidates = _walk_documents(origin)

    copied: list[ImportedFile] = []
    refused: list[RefusedFile] = []
    truncated = False
    for candidate in candidates:
        if len(copied) >= MAX_IMPORT_FILES:
            truncated = True
            break
        if candidate.suffix.lower() not in DOC_SUFFIXES:
            # Not a document Apollo reads. Counted in `found`, not refused: nobody
            # offered it, so nobody is waiting for it.
            continue
        source_label = str(candidate)
        try:
            size = candidate.stat().st_size
            if size > MAX_UPLOAD_BYTES:
                megabytes = MAX_UPLOAD_BYTES // (1024 * 1024)
                refused.append(
                    RefusedFile(
                        source_label,
                        f"{candidate.name} is larger than {megabytes} MB, which is the "
                        "most Apollo stores.",
                    )
                )
                continue
            data = candidate.read_bytes()
            if not data:
                refused.append(RefusedFile(source_label, f"{candidate.name} is empty."))
                continue

            relative = candidate.relative_to(origin)
            target_dir = ensure_inbox(workspace_id, working_dir) / folder_name / relative.parent
            target_dir.mkdir(parents=True, exist_ok=True)
            target = _unique_target(target_dir, candidate.name)
            _atomic_write_bytes(target, data)

            stored_rel = target.relative_to(
                workspace_inbox(workspace_id, working_dir)
            ).as_posix()
            rel = f"{INBOX_DIR}/{stored_rel}"
            readable, reason = True, None
            try:
                read_document(workspace_storage(workspace_id, working_dir), rel)
            except (DocumentError, PathSecurityError) as exc:
                readable, reason = False, str(exc)
            copied.append(
                ImportedFile(
                    source_path=source_label,
                    path=rel,
                    name=target.name,
                    size=len(data),
                    readable=readable,
                    unreadable_reason=reason,
                )
            )
        except StorageError as exc:
            refused.append(RefusedFile(source_label, str(exc)))
        except OSError as exc:
            refused.append(RefusedFile(source_label, f"Could not copy it: {exc}"))

    # One commit for the whole import rather than one per document: a folder of
    # forty documents is one act by the reader, and a log of forty identical
    # entries would bury everything else in it.
    if copied:
        record_added(
            workspace_storage(workspace_id, working_dir),
            [f.path for f in copied],
            f"Added by Apollo: {len(copied)} documents from {folder_name}",
        )

    return FolderImport(
        folder_name=folder_name,
        found=len(candidates),
        copied=copied,
        refused=refused,
        truncated=truncated,
    )


def ensure_working_repo(root: str | Path) -> str | None:
    """Make the reader's working folder a Git repository, once.

    Why this exists at all, since nothing in the application commits: the move
    engine refuses to relocate a file Git does not track, because the original
    would then be unrecoverable. A folder the reader chose is almost never a Git
    repository, so without this every move proposal for a dropped-in document
    would be refused -- the proposal flow would be present and inert, and the
    first person to try it would conclude the feature was broken.

    So the folder gets a history of its own. The initial commit records the folder
    as the reader left it, which is the point: from that moment on, every document
    Apollo stores or moves here is recoverable with ``git log`` and ``git status``,
    and the reader can see in a file manager *and* in a diff what has happened to
    their documents. Nothing is pushed anywhere.

    The identity is set on the *repository*, never globally: the commits in
    somebody's folder are Apollo's work, and attributing them to that person's
    Git configuration would be a small lie in a log they will read. It is also
    what makes the commit work at all on a machine with no Git identity set, where
    ``git commit`` fails outright and the history would silently never exist.

    Idempotent, and it does not commit anything of its own once the repository
    exists: only the very first call does.
    """
    from app.services import git

    target = Path(root)
    if git.is_repo(target):
        return git.head_revision(target)
    try:
        # `init` is one of the few commands here that is not read-only, and it
        # writes nothing but a .git directory into a folder that has none.
        git._run(target, ["init", "--quiet", "--initial-branch=main"])
        git._run(target, ["config", "user.name", "Apollo"])
        git._run(target, ["config", "user.email", "apollo@localhost"])
        target.mkdir(parents=True, exist_ok=True)
        existing = _document_relatives(target)
        if existing:
            git.commit_paths(
                target,
                existing,
                "The folder as it was when it was chosen to work in",
            )
        else:
            # An empty commit on purpose, and not because nothing was left to do.
            # It gives the history a starting point that exists from the first
            # second, so there is always a revision to diff a later move against
            # and "this folder has no history" is never the answer.
            git._run(
                target,
                [
                    "commit",
                    "--quiet",
                    "--allow-empty",
                    "-m",
                    "The folder as it was when it was chosen to work in: empty",
                ],
            )
    except git.GitError:
        # No history is a diminished guarantee, not a broken feature: the moves
        # will be refused by the engine rather than performed unrecoverably, and
        # the reader is told that where it matters.
        return None
    return git.head_revision(target)


def _document_relatives(target: Path) -> list[str]:
    """Every file under ``target``, repo-relative, excluding Git's own.

    The ``.git`` directory is never committed to itself, and passing it to
    ``git add`` is how a repository commits a path that cannot be committed.
    """
    return sorted(
        p.relative_to(target).as_posix()
        for p in target.rglob("*")
        if p.is_file() and ".git" not in p.relative_to(target).parts
    )


def adopt_folder_history(root: str | Path) -> dict[str, object]:
    """Make the documents already in the reader's folder recoverable, once.

    The gap this fills is concrete. :func:`ensure_working_repo` returns
    immediately when the folder is *already* a Git repository -- which is exactly
    the folder a reader is most likely to choose: their own documents folder, a
    project they already keep under version control. Their files stay untracked,
    and ``apply_change`` refuses every move of an untracked document. So a group
    with a folder proposes a move, the reader accepts it, and the engine says no
    for a reason the interface never mentioned.

    This is the way out of that, and the reader takes it deliberately: one commit
    recording the folder as it is now. Nothing is moved, renamed or rewritten --
    the commit only makes the current state recoverable, which is the
    precondition for any later move being safe.

    Existing commits are never touched, and nothing is pushed. In a folder that
    is not a repository this creates one first, exactly as choosing the folder
    does, so the two paths cannot disagree.
    """
    from app.services import git

    target = Path(root)
    try:
        ensure_working_repo(target)
        if not git.is_repo(target):
            return {"ok": False, "committed": 0, "message": _ADOPT_FAILED}

        # What a human would stage: Git's own exclusions apply, so build output
        # and dependencies are left alone rather than swept in.
        pending = [p for p in git.untracked_paths(target) if p]
        if not pending:
            return {
                "ok": True,
                "committed": 0,
                "message": (
                    "Everything in this folder is already recorded, so a document "
                    "can be moved and still recovered."
                ),
            }

        git.commit_paths(
            target,
            pending,
            "The documents that were already in this folder",
        )
        return {
            "ok": True,
            "committed": len(pending),
            "paths": pending,
            "message": (
                f"Recorded {len(pending)} "
                f"{'file' if len(pending) == 1 else 'files'} that were already in "
                "this folder. Nothing was moved or changed; from now on a "
                "document Apollo proposes to move can be recovered."
            ),
        }
    except git.GitError as exc:
        # Git's own words, because a failure here is a Git problem the reader
        # can act on -- a locked folder, a dubious ownership complaint -- and
        # "something went wrong" would leave them with nothing to do.
        return {"ok": False, "committed": 0, "message": f"{_ADOPT_FAILED} {exc}"}


#: Said when the folder could not be made recoverable, which is a diminished
#: guarantee rather than a broken feature: the moves are refused by the engine
#: rather than performed unrecoverably, and the reader is told that where it
#: matters.
_ADOPT_FAILED = (
    "This folder's history could not be written, so Apollo will refuse to move "
    "documents in it -- it will not move a file it cannot recover."
)


def record_added(root: str | Path, relatives: list[str], what: str) -> None:
    """Commit documents Apollo just wrote, so a later move can be undone.

    This is the half of the history that has to happen *while* documents arrive.
    ``apply_change`` refuses to relocate a file Git does not track, and a document
    dropped in an hour ago is untracked until something commits it. Without this,
    the first accepted move would be refused for the same reason the repository
    was created in the first place, and the guarantee would be no better than
    having no history at all.

    Silent when the folder is not a repository: the fallback storage folder is
    not one, and a failure to record a convenience must not fail the drop that
    succeeded.
    """
    from app.services import git

    if not relatives:
        return
    try:
        if not git.is_repo(root):
            return
        git.commit_paths(root, relatives, what)
    except git.GitError:
        return


__all__ = [
    "INBOX_DIR",
    "MAX_IMPORT_FILES",
    "MAX_UPLOAD_BYTES",
    "FolderImport",
    "ImportedFile",
    "InboxEntry",
    "RefusedFile",
    "StorageError",
    "StoredDocument",
    "ensure_inbox",
    "ensure_working_repo",
    "import_folder",
    "list_inbox",
    "record_added",
    "sanitize_filename",
    "storage_root",
    "store_upload",
    "workspace_inbox",
    "workspace_storage",
]
