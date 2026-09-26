"""Architecture Decision Record (ADR) synchronization service.

Coordinates rendering and writing durable ADR Markdown documents to the workspace
documentation repository when a Decision is approved.

Core invariants:
- The database remains the source of truth for the Decision entity.
- The Markdown document is the durable documentation representation.
- Synchronization is triggered explicitly (e.g. upon Decision approval).
- Repeated synchronization of the same Decision is idempotent and never creates duplicate files.
- Updating an existing ADR preserves non-managed/custom sections.
- Overwriting unrelated documents (conflicts) is strictly refused.
- Working tree changes are left uncommitted for human Git review; no automatic commit or push.
"""
from __future__ import annotations

import datetime as dt
import difflib
from dataclasses import dataclass, field
import os
from pathlib import Path, PurePosixPath
import re
from typing import Any

from app.models import Decision, utcnow
from app.services import git
from app.services.documents import DOC_SUFFIXES, read_document
from app.services.paths import PathSecurityError, safe_path, to_rel_path


class ADRError(Exception):
    """Base error for ADR synchronization issues."""


class AmbiguousADRLocationError(ADRError):
    """Raised when the target ADR folder cannot be determined unambiguously."""


class ADRConflictError(ADRError):
    """Raised when the target file does not correspond to the Decision being approved."""


@dataclass
class ADRSyncResult:
    decision: Decision
    sync_status: str  # "created" | "updated" | "unchanged" | "requires_review" | "skipped"
    markdown_path: str
    diff: str | None = None
    git_status: list[git.GitStatusEntry] = field(default_factory=list)
    message: str | None = None


# Candidate directory paths relative to documentation repository root.
# Ordered by architectural preference.
CANDIDATE_ADR_DIRS = (
    "architecture/decisions",
    "decisions",
    "docs/decisions",
    "docs/architecture/decisions",
    "doc/decisions",
    "architecture/adr",
    "adr",
)

MANAGED_SECTIONS = {
    "context",
    "decision",
    "rationale",
    "consequences",
    "related questions",
    "related documents",
    "references",
}


def slugify(text: str) -> str:
    """Convert a decision title into a safe lowercase kebab-case slug."""
    text = text.strip().lower()
    # Strip common leading prefix like "ADR 001:" or "ADR-1:"
    text = re.sub(r"^adr[\s\-_:]*\d*[\s\-_:]*", "", text)
    # Replace non-alphanumeric characters with hyphens
    text = re.sub(r"[^a-z0-9]+", "-", text)
    # Collapse consecutive hyphens
    text = re.sub(r"-+", "-", text).strip("-")
    return text[:60] if text else "decision"


def find_adr_directory(repo_root: Path) -> Path:
    """Determine the correct ADR directory from existing conventions.

    Raises AmbiguousADRLocationError if multiple distinct candidate directories exist
    or if no safe directory can be determined.
    """
    existing_candidates: list[Path] = []
    for cand in CANDIDATE_ADR_DIRS:
        p = repo_root / cand
        if p.exists() and p.is_dir():
            existing_candidates.append(p)

    if len(existing_candidates) == 1:
        return existing_candidates[0]

    if len(existing_candidates) > 1:
        # Check if only one candidate contains ADR-like files
        candidates_with_adrs = [
            c for c in existing_candidates
            if any(f.name.lower().startswith("adr-") for f in c.iterdir() if f.is_file())
        ]
        if len(candidates_with_adrs) == 1:
            return candidates_with_adrs[0]

        rel_paths = [to_rel_path(repo_root, c) for c in existing_candidates]
        raise AmbiguousADRLocationError(
            f"Multiple candidate ADR directories exist in repository: {rel_paths}. "
            "Cannot safely determine ADR location."
        )

    # None of the candidate directories currently exist.
    # Check if 'architecture' directory exists per Gaia documentation structure.
    arch_dir = repo_root / "architecture"
    if arch_dir.exists() and arch_dir.is_dir():
        target = arch_dir / "decisions"
        return target

    # If neither 'architecture' nor any candidate directory exists, we cannot guess safely.
    raise AmbiguousADRLocationError(
        "Cannot safely determine ADR directory: neither 'architecture/decisions' nor any "
        "candidate decision directory exists in the documentation repository."
    )


def next_adr_number(adr_dir: Path) -> int:
    """Find the highest existing ADR number and return next integer."""
    if not adr_dir.exists():
        return 1

    max_num = 0
    for entry in adr_dir.iterdir():
        if not entry.is_file() or entry.suffix.lower() not in DOC_SUFFIXES:
            continue
        # Check adr-001 or 001-
        match = re.search(r"(?:adr-|^)(\d+)", entry.stem.lower())
        if match:
            try:
                num = int(match.group(1))
                if num > max_num:
                    max_num = num
            except ValueError:
                pass
    return max_num + 1


def _extract_number_from_title(title: str) -> int | None:
    match = re.search(r"\bADR[\s\-_:]*(\d+)", title, re.IGNORECASE)
    if match:
        try:
            return int(match.group(1))
        except ValueError:
            pass
    return None


def resolve_adr_path(repo_root: Path, decision: Decision) -> Path:
    """Resolve the relative filesystem path for the Decision's ADR.

    Guarantees stable, idempotent mapping:
    1. If decision.markdown_path is set, reuses that exact path.
    2. Otherwise, locates the ADR directory, checks for existing match, or allocates next number.
    """
    if decision.markdown_path:
        return safe_path(repo_root, decision.markdown_path)

    adr_dir = find_adr_directory(repo_root)

    # Check if an existing file in adr_dir matches this decision by title
    slug = slugify(decision.title)
    if adr_dir.exists():
        for entry in adr_dir.iterdir():
            if not entry.is_file() or entry.suffix.lower() not in DOC_SUFFIXES:
                continue
            if slug and slug in entry.stem.lower():
                return entry

    num = _extract_number_from_title(decision.title) or next_adr_number(adr_dir)
    filename = f"adr-{num:03d}-{slug}.md" if slug else f"adr-{num:03d}.md"
    return adr_dir / filename


def _parse_markdown_sections(content: str) -> tuple[str, dict[str, str]]:
    """Parse Markdown into a preamble and a dictionary of '## Header' sections."""
    lines = content.splitlines(keepends=True)
    preamble_lines: list[str] = []
    sections: dict[str, list[str]] = {}
    current_section: str | None = None

    for line in lines:
        match = re.match(r"^##\s+(.+)$", line.strip())
        if match:
            current_section = match.group(1).strip()
            sections[current_section] = [line]
        elif current_section is not None:
            sections[current_section].append(line)
        else:
            preamble_lines.append(line)

    preamble = "".join(preamble_lines)
    rendered_sections = {k: "".join(v) for k, v in sections.items()}
    return preamble, rendered_sections


def verify_file_corresponds_to_decision(
    existing_content: str, decision: Decision, rel_path: str
) -> None:
    """Verify that an existing file is indeed an ADR corresponding to this Decision.

    Prevents accidentally overwriting unrelated documentation (e.g. principles.md).
    """
    # 1. Check title/first heading
    first_heading_match = re.search(r"^#\s+(.+)$", existing_content, re.MULTILINE)
    first_heading = first_heading_match.group(1).strip() if first_heading_match else ""

    norm_decision_title = re.sub(r"[^a-z0-9]", "", decision.title.lower())
    norm_file_heading = re.sub(r"[^a-z0-9]", "", first_heading.lower())

    # If the file heading contains significant parts of the decision title
    if norm_decision_title and (
        norm_decision_title in norm_file_heading or norm_file_heading in norm_decision_title
    ):
        return

    # Or if both mention the same ADR number
    dec_num = _extract_number_from_title(decision.title)
    file_num = _extract_number_from_title(first_heading)
    if dec_num is not None and dec_num == file_num:
        return

    # Check if the file is in an adr/decisions folder and has ADR structure
    if "decision" in rel_path.lower() or "adr" in rel_path.lower():
        if "adr" in first_heading.lower() or "decision" in first_heading.lower():
            return

    raise ADRConflictError(
        f"Target document '{rel_path}' does not correspond to Decision '{decision.title}' "
        f"(existing document heading: '{first_heading or 'None'}'). Refusing to overwrite."
    )


def render_adr_content(
    decision: Decision,
    adr_number: int,
    existing_content: str | None = None,
) -> str:
    """Render the ADR Markdown document.

    Preserves any non-managed / custom sections from existing_content.
    """
    # Determine title
    if re.search(r"^ADR[\s\-_:]*\d*", decision.title, re.IGNORECASE):
        title_line = f"# {decision.title.strip()}"
    else:
        title_line = f"# ADR {adr_number:03d}: {decision.title.strip()}"

    # Determine date
    date_val = decision.decided_on or decision.approved_at or utcnow()
    if isinstance(date_val, (dt.datetime, dt.date)):
        date_str = date_val.strftime("%Y-%m-%d")
    else:
        date_str = str(date_val)[:10]

    status_str = decision.status or "approved"

    parts: list[str] = [
        title_line,
        "",
        f"- Status: {status_str}",
        f"- Date: {date_str}",
        "",
    ]

    # Context
    if decision.context and decision.context.strip():
        parts.extend(["## Context", decision.context.strip(), ""])

    # Decision
    if decision.decision and decision.decision.strip():
        parts.extend(["## Decision", decision.decision.strip(), ""])

    # Rationale
    if decision.rationale and decision.rationale.strip():
        parts.extend(["## Rationale", decision.rationale.strip(), ""])

    # Consequences
    if decision.consequences and decision.consequences.strip():
        parts.extend(["## Consequences", decision.consequences.strip(), ""])

    # Related Questions
    if decision.related_questions:
        q_lines = ["## Related Questions"]
        for q in decision.related_questions:
            q_lines.append(f"- Question reference: {q}")
        parts.extend(q_lines + [""])

    # Related Documents
    if decision.related_documents:
        doc_lines = ["## Related Documents"]
        for doc in decision.related_documents:
            doc_lines.append(f"- [{doc}]({doc})")
        parts.extend(doc_lines + [""])

    # If updating existing content, preserve any custom sections
    if existing_content:
        _, existing_sections = _parse_markdown_sections(existing_content)
        for header, section_text in existing_sections.items():
            if header.strip().lower() not in MANAGED_SECTIONS:
                parts.append(section_text.rstrip())
                parts.append("")

    return "\n".join(parts).rstrip() + "\n"


def _atomic_write(target: Path, content: str) -> None:
    """Write content to target atomically via a temporary file."""
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(target.name + ".tmp-adr")
    tmp.write_text(content, encoding="utf-8")
    tmp.replace(target)


def _render_unified_diff(path: str, before: str, after: str) -> str:
    """Generate unified diff between before and after contents."""
    diff = difflib.unified_diff(
        before.splitlines(keepends=True),
        after.splitlines(keepends=True),
        fromfile=f"a/{path}",
        tofile=f"b/{path}",
        n=3,
    )
    return "".join(diff)


def sync_decision_adr(repo_root: str | Path, decision: Decision) -> ADRSyncResult:
    """Synchronize an approved Decision to its corresponding ADR Markdown document.

    Idempotent:
    - If the ADR does not exist, creates it.
    - If it already exists, updates managed sections and preserves custom sections.
    - If the content is already identical, reports sync_status="unchanged".
    - Leaves changes in the working tree without committing or pushing.
    """
    root_path = Path(repo_root)
    dest_path = resolve_adr_path(root_path, decision)
    rel_path = to_rel_path(root_path, dest_path)

    adr_num = (
        _extract_number_from_title(decision.title)
        or _extract_number_from_title(dest_path.name)
        or next_adr_number(dest_path.parent)
    )

    if dest_path.exists():
        existing_content = read_document(root_path, rel_path)
        verify_file_corresponds_to_decision(existing_content, decision, rel_path)
        new_content = render_adr_content(decision, adr_num, existing_content)

        if existing_content.strip() == new_content.strip():
            # Already in sync
            entries = git.status(root_path) if git.is_repo(root_path) else []
            return ADRSyncResult(
                decision=decision,
                sync_status="unchanged",
                markdown_path=rel_path,
                diff=None,
                git_status=entries,
                message=f"ADR at '{rel_path}' is already up to date.",
            )

        diff_str = _render_unified_diff(rel_path, existing_content, new_content)
        _atomic_write(dest_path, new_content)
        sync_status = "updated"
        message = f"ADR at '{rel_path}' successfully updated."
    else:
        new_content = render_adr_content(decision, adr_num, None)
        diff_str = _render_unified_diff(rel_path, "", new_content)
        _atomic_write(dest_path, new_content)
        sync_status = "created"
        message = f"ADR at '{rel_path}' successfully created."

    decision.markdown_path = rel_path
    entries = git.status(root_path) if git.is_repo(root_path) else []

    return ADRSyncResult(
        decision=decision,
        sync_status=sync_status,
        markdown_path=rel_path,
        diff=diff_str,
        git_status=entries,
        message=message,
    )


def apply_superseded_notice_to_content(
    content: str,
    superseding_decision: Decision,
) -> str:
    """Insert or update supersession notice near the top of ADR and update status."""
    num = (
        _extract_number_from_title(superseding_decision.title)
        or (_extract_number_from_title(superseding_decision.markdown_path) if superseding_decision.markdown_path else None)
    )
    if num is not None:
        ref_str = f"ADR-{num:03d}"
    else:
        ref_str = f"Decision #{superseding_decision.id} ({superseding_decision.title.strip()})"

    notice_line = f"> Superseded by {ref_str}."

    lines = content.splitlines(keepends=True)

    h1_index = -1
    notice_index = -1

    for i, line in enumerate(lines):
        if h1_index == -1 and line.startswith("# "):
            h1_index = i
        if re.match(r"^>\s*Superseded by\b", line.strip(), re.IGNORECASE):
            notice_index = i

    if notice_index != -1:
        lines[notice_index] = notice_line + "\n"
    elif h1_index != -1:
        # Insert notice directly following H1
        lines.insert(h1_index + 1, f"\n{notice_line}\n")
    else:
        lines.insert(0, f"{notice_line}\n\n")

    new_content = "".join(lines)

    # Update or add - Status: superseded
    if re.search(r"^[-*]\s*Status\s*:\s*.+$", new_content, re.MULTILINE | re.IGNORECASE):
        new_content = re.sub(
            r"^([-*]\s*Status\s*:\s*).+$",
            r"\g<1>superseded",
            new_content,
            count=1,
            flags=re.MULTILINE | re.IGNORECASE,
        )
    else:
        new_content = new_content.replace(notice_line, f"{notice_line}\n\n- Status: superseded", 1)

    return new_content


def remove_superseded_notice_from_content(content: str) -> str:
    """Remove supersession notice and reset status to approved."""
    lines = content.splitlines(keepends=True)
    filtered = [l for l in lines if not re.match(r"^>\s*Superseded by\b", l.strip(), re.IGNORECASE)]
    new_content = "".join(filtered)
    new_content = re.sub(r"\n{3,}", "\n\n", new_content)

    if re.search(r"^[-*]\s*Status\s*:\s*.+$", new_content, re.MULTILINE | re.IGNORECASE):
        new_content = re.sub(
            r"^([-*]\s*Status\s*:\s*).+$",
            r"\g<1>approved",
            new_content,
            count=1,
            flags=re.MULTILINE | re.IGNORECASE,
        )
    return new_content


def mark_adr_superseded(
    repo_root: str | Path,
    superseded_decision: Decision,
    superseding_decision: Decision,
) -> ADRSyncResult:
    """Mark an existing historical ADR as superseded by a newer decision.

    Guarantees:
    - Original decision content, rationale, context, and references are preserved.
    - Historical ADR is modified in-place; not deleted or regenerated.
    - Idempotent: repeated marking with the same target causes no redundant diffs.
    - Missing or conflicting ADRs are safely reported without data loss.
    - Changes remain uncommitted in working tree for operator inspection.
    """
    root_path = Path(repo_root)

    if superseded_decision.markdown_path:
        dest_path = safe_path(root_path, superseded_decision.markdown_path)
    else:
        try:
            dest_path = resolve_adr_path(root_path, superseded_decision)
        except Exception:
            dest_path = None

    if dest_path is None or not dest_path.exists():
        rel = superseded_decision.markdown_path or (to_rel_path(root_path, dest_path) if dest_path else "unknown")
        entries = git.status(root_path) if git.is_repo(root_path) else []
        return ADRSyncResult(
            decision=superseded_decision,
            sync_status="skipped",
            markdown_path=rel,
            diff=None,
            git_status=entries,
            message=f"Historical ADR document not found at '{rel}'. Database record marked as superseded.",
        )

    rel_path = to_rel_path(root_path, dest_path)
    existing_content = read_document(root_path, rel_path)
    verify_file_corresponds_to_decision(existing_content, superseded_decision, rel_path)

    new_content = apply_superseded_notice_to_content(existing_content, superseding_decision)

    if existing_content.strip() == new_content.strip():
        entries = git.status(root_path) if git.is_repo(root_path) else []
        return ADRSyncResult(
            decision=superseded_decision,
            sync_status="unchanged",
            markdown_path=rel_path,
            diff=None,
            git_status=entries,
            message=f"ADR at '{rel_path}' is already marked as superseded by '{superseding_decision.title}'.",
        )

    diff_str = _render_unified_diff(rel_path, existing_content, new_content)
    _atomic_write(dest_path, new_content)
    superseded_decision.markdown_path = rel_path
    entries = git.status(root_path) if git.is_repo(root_path) else []

    return ADRSyncResult(
        decision=superseded_decision,
        sync_status="updated",
        markdown_path=rel_path,
        diff=diff_str,
        git_status=entries,
        message=f"ADR at '{rel_path}' updated with supersession notice.",
    )


def unmark_adr_superseded(
    repo_root: str | Path,
    superseded_decision: Decision,
) -> ADRSyncResult:
    """Revert an ADR supersession notice back to approved."""
    root_path = Path(repo_root)
    if not superseded_decision.markdown_path:
        entries = git.status(root_path) if git.is_repo(root_path) else []
        return ADRSyncResult(
            decision=superseded_decision,
            sync_status="skipped",
            markdown_path="",
            diff=None,
            git_status=entries,
            message="No markdown_path recorded for decision.",
        )

    dest_path = safe_path(root_path, superseded_decision.markdown_path)
    if not dest_path.exists():
        entries = git.status(root_path) if git.is_repo(root_path) else []
        return ADRSyncResult(
            decision=superseded_decision,
            sync_status="skipped",
            markdown_path=superseded_decision.markdown_path,
            diff=None,
            git_status=entries,
            message="ADR file not found on disk.",
        )

    rel_path = to_rel_path(root_path, dest_path)
    existing_content = read_document(root_path, rel_path)
    verify_file_corresponds_to_decision(existing_content, superseded_decision, rel_path)

    new_content = remove_superseded_notice_from_content(existing_content)
    if existing_content.strip() == new_content.strip():
        entries = git.status(root_path) if git.is_repo(root_path) else []
        return ADRSyncResult(
            decision=superseded_decision,
            sync_status="unchanged",
            markdown_path=rel_path,
            diff=None,
            git_status=entries,
            message=f"ADR at '{rel_path}' does not have a supersession notice.",
        )

    diff_str = _render_unified_diff(rel_path, existing_content, new_content)
    _atomic_write(dest_path, new_content)
    entries = git.status(root_path) if git.is_repo(root_path) else []

    return ADRSyncResult(
        decision=superseded_decision,
        sync_status="updated",
        markdown_path=rel_path,
        diff=diff_str,
        git_status=entries,
        message=f"ADR at '{rel_path}' supersession notice removed.",
    )
