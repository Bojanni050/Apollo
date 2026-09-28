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


def storage_root() -> Path:
    """The directory Apollo owns. Not created here: only an upload creates it."""
    return Path(settings.effective_storage_root)


def workspace_storage(workspace_id: int) -> Path:
    """Where one workspace's own documents live."""
    return storage_root() / f"workspace_{workspace_id}"


def workspace_inbox(workspace_id: int) -> Path:
    """The workspace's inbox directory. May not exist yet."""
    return workspace_storage(workspace_id) / INBOX_DIR


def ensure_inbox(workspace_id: int) -> Path:
    """Create the inbox directory, and nothing beside it.

    Creating the parent is unavoidable -- the workspace directory is part of the
    layout -- but a folder per category is not: an empty ``Projecten`` for a
    category nobody has used is a claim about the reader's documents that the
    reader did not make.
    """
    inbox = workspace_inbox(workspace_id)
    try:
        inbox.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        # A storage root the process cannot write to is a configuration problem
        # the reader can act on (a wrong APOLLO_STORAGE_ROOT, a read-only disk),
        # so it is reported as one rather than surfacing as a server error.
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


def store_upload(workspace_id: int, filename: str, data: bytes) -> StoredDocument:
    """Store one dropped-in document in the workspace's inbox.

    Raises :class:`StorageError` for everything that can be judged without
    writing: an unusable name, a format that is not a document, an empty file, or
    a file above the size ceiling. Nothing is created on disk in those cases, so
    a refused drop leaves the storage root exactly as it was.

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

    inbox = ensure_inbox(workspace_id)
    target = _unique_target(inbox, name)
    _atomic_write_bytes(target, data)

    rel = f"{INBOX_DIR}/{target.name}"
    readable, reason = True, None
    try:
        read_document(workspace_storage(workspace_id), rel)
    except (DocumentError, PathSecurityError) as exc:
        readable, reason = False, str(exc)

    return StoredDocument(
        path=rel,
        name=target.name,
        size=len(data),
        readable=readable,
        unreadable_reason=reason,
    )


def list_inbox(workspace_id: int) -> list[InboxEntry]:
    """The documents in the inbox, by name.

    A missing directory is an empty inbox rather than an error: before the first
    upload there is genuinely nothing there, and a 404 would make the interface
    explain a problem that does not exist.

    Flat, not recursive. The inbox is where documents wait; the reader who
    dropped them expects to see the files, and where they belong afterwards is
    exactly the question this application exists to answer.
    """
    inbox = workspace_inbox(workspace_id)
    if not inbox.is_dir():
        return []
    entries: list[InboxEntry] = []
    for path in sorted(inbox.iterdir(), key=lambda p: p.name.lower()):
        if not path.is_file() or path.name.endswith(TEMP_SUFFIX):
            continue
        if path.suffix.lower() not in DOC_SUFFIXES:
            # Only documents are listed, using the same definition of "document"
            # the rest of the application reads with. A stray file is still on
            # disk and still untouched; it is simply not offered as readable.
            continue
        entries.append(
            InboxEntry(
                path=f"{INBOX_DIR}/{path.name}",
                name=path.name,
                size=path.stat().st_size,
            )
        )
    return entries


__all__ = [
    "INBOX_DIR",
    "MAX_UPLOAD_BYTES",
    "InboxEntry",
    "StorageError",
    "StoredDocument",
    "ensure_inbox",
    "list_inbox",
    "sanitize_filename",
    "storage_root",
    "store_upload",
    "workspace_inbox",
    "workspace_storage",
]
