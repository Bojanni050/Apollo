"""`.sources.yaml` manifest parsing, validation and rendering.

The manifest is a portable description of a workspace's configured repository
sources:

    sources:
      - repo: Gaia
        path: https://github.com/Bojanni050/Gaia-Cloud
      - repo: Local Thing
        path: C:/src/local-thing

Design constraints:

* **YAML is configuration, nothing more.** Parsing uses ``yaml.safe_load``, so
  no tag can instantiate a Python object or execute anything. Any structure
  other than ``{"sources": [{"repo": str, "path": str}, ...]}`` is invalid,
  including entries with unknown keys -- an imported file must not be able to
  smuggle configuration into the application.
* **Nothing is applied on parse.** Validation produces a preview; importing is
  a separate, explicitly confirmed operation.
* **Invalid entries are never silently skipped.** They are reported with the
  reason, so the operator can inspect them before deciding.
* **No credentials.** URLs carrying embedded credentials are rejected.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import yaml

from app.services.sources import SourceError, classify_location, local_identity, normalize_repo_url

MAX_MANIFEST_BYTES = 512 * 1024  # a manifest describes sources; 512 KB is generous
ALLOWED_ENTRY_KEYS = {"repo", "path", "branch"}


class ManifestError(ValueError):
    """Raised when a manifest cannot be parsed or is structurally invalid."""


@dataclass
class ManifestEntry:
    """One parsed entry, plus its validation outcome."""

    index: int
    repo: str
    path: str
    branch: str | None = None
    source_type: str = "local"
    identity: str = ""
    valid: bool = True
    error: str | None = None
    action: str = "add"  # add | duplicate
    existing_name: str | None = None


@dataclass
class ManifestReport:
    """The preview the operator confirms before anything is imported."""

    entries: list[ManifestEntry] = field(default_factory=list)

    @property
    def valid_entries(self) -> list[ManifestEntry]:
        return [e for e in self.entries if e.valid]

    @property
    def invalid_entries(self) -> list[ManifestEntry]:
        return [e for e in self.entries if not e.valid]

    @property
    def new_entries(self) -> list[ManifestEntry]:
        return [e for e in self.entries if e.valid and e.action == "add"]

    @property
    def duplicates(self) -> list[ManifestEntry]:
        return [e for e in self.entries if e.valid and e.action == "duplicate"]


def parse_manifest(text: str) -> list[dict[str, Any]]:
    """Parse YAML safely into a list of raw entry dicts.

    Everything that can be wrong with the *structure* is rejected here:
    non-YAML text, unexpected top-level types, a missing ``sources`` key,
    entries that are not mappings, and unknown keys (so a manifest cannot
    override arbitrary application configuration).
    """
    if not (text and text.strip()):
        raise ManifestError("The manifest is empty.")
    if len(text.encode("utf-8")) > MAX_MANIFEST_BYTES:
        raise ManifestError("The manifest is too large to be a .sources.yaml file.")

    try:
        # safe_load: no arbitrary object construction, no code execution.
        # A YAML file is configuration, never a program.
        data = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise ManifestError(f"Invalid YAML: {getattr(exc, 'problem', None) or exc}") from exc

    if not isinstance(data, dict):
        raise ManifestError("The manifest root must be a mapping with a 'sources' key.")
    if "sources" not in data:
        raise ManifestError("The manifest has no 'sources' key.")
    if len(data) != 1:
        raise ManifestError(
            f"Unexpected top-level key(s) in the manifest: "
            f"{', '.join(sorted(set(data) - {'sources'}))}. Only 'sources' is allowed."
        )

    sources = data["sources"]
    if sources is None:
        raise ManifestError("'sources' must be a list, not null.")
    if not isinstance(sources, list):
        raise ManifestError("'sources' must be a list of entries.")
    if not sources:
        raise ManifestError("'sources' is empty; there is nothing to import.")

    entries: list[dict[str, Any]] = []
    for position, item in enumerate(sources):
        if not isinstance(item, dict):
            raise ManifestError(
                f"Entry #{position + 1} is not a mapping; expected 'repo' and 'path'."
            )
        unknown = set(item) - ALLOWED_ENTRY_KEYS
        if unknown:
            raise ManifestError(
                f"Entry #{position + 1} has unsupported key(s): {', '.join(sorted(unknown))}. "
                "A manifest entry may only have 'repo', 'path' and 'branch'."
            )
        if not isinstance(item.get("repo"), str) or not item.get("repo", "").strip():
            raise ManifestError(f"Entry #{position + 1} is missing the required 'repo' field.")
        if not isinstance(item.get("path"), str) or not item.get("path", "").strip():
            raise ManifestError(f"Entry #{position + 1} is missing the required 'path' field.")
        branch = item.get("branch")
        if branch is not None and (not isinstance(branch, str) or not branch.strip()):
            raise ManifestError(f"Entry #{position + 1} has an invalid 'branch' field.")
        entries.append(
            {
                "repo": item["repo"].strip(),
                "path": item["path"].strip(),
                "branch": branch.strip() if isinstance(branch, str) else None,
            }
        )
    return entries


def validate_manifest(
    text: str,
    existing_identities: dict[str, dict[str, str]],
) -> ManifestReport:
    """Validate a manifest into a preview, without touching the workspace.

    ``existing_identities`` maps source type ("local" / "github") to a dict of
    ``identity -> display name`` for the sources already configured in this
    workspace, so the preview can mark which entries would be duplicates rather
    than new sources. Duplicates are *valid* entries: importing them is a
    no-op, which is what makes repeated imports idempotent.
    """
    entries = parse_manifest(text)
    report = ManifestReport()
    seen: dict[str, str] = {}  # identity -> repo name, for in-manifest duplicates

    for position, item in enumerate(entries):
        entry = ManifestEntry(index=position, repo=item["repo"], path=item["path"])
        entry.branch = item.get("branch")
        stype = classify_location(item["path"])
        entry.source_type = stype

        try:
            if stype == "github":
                entry.identity = normalize_repo_url(item["path"])
            else:
                entry.identity = local_identity(item["path"])
        except SourceError as exc:
            entry.valid = False
            entry.error = str(exc)
            report.entries.append(entry)
            continue

        if entry.identity in seen:
            # A duplicate *within the manifest itself*: importing it would
            # create the same source twice, so it is flagged as invalid with
            # an explicit message rather than silently dropped.
            entry.valid = False
            entry.error = (
                f"Duplicate repository: identical to entry {seen[entry.identity]!r}."
            )
            report.entries.append(entry)
            continue
        seen[entry.identity] = entry.repo

        known = existing_identities.get(stype, {})
        if entry.identity in known:
            entry.action = "duplicate"
            entry.existing_name = known[entry.identity]
        report.entries.append(entry)

    return report


def render_manifest(db, workspace) -> str:
    """Serialize a workspace's sources back into portable .sources.yaml form.

    The rendered manifest describes source identity and location only -- local
    checkout paths stay out of it on purpose, because they are machine-specific
    configuration, not portable description.
    """
    from app.services.sources import manifest_entries

    lines = ["sources:"]
    for entry in manifest_entries(db, workspace):
        lines.append(f"  - repo: {entry['repo']}")
        lines.append(f"    path: {entry['path']}")
    return "\n".join(lines) + "\n"
