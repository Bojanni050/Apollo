"""AI Pulse: scan documentation for themes, tags and connections.

A Pulse run walks the documentation repository's Markdown files, asks the
background LLM for a structural digest of each document, and extracts:

* thematic tags, so documents become findable by topic rather than only by
  folder or filename;
* connections to other documents in the same repository, with the relation
  (relates-to / supports / contradicts / extends) and the reason, so the
  documentation's own cross-links are surfaced instead of living only in a
  maintainer's head.

The background model is used on purpose: this is bulk classification over
potentially many documents, exactly the cheap-and-compact tier the two-tier
LLM setup exists for.

Two modes, stored per workspace:

* ``suggest`` -- the run only records suggestions; a human applies them one
  by one through the approval endpoints.
* ``apply`` -- suggestions are written into the documents immediately, as
  ``pulse-tags`` / ``pulse-connections`` YAML front matter, through the same
  guarded write path (plan_edit + apply_change) every other write uses.

Incremental by content hash: a run only re-examines documents whose content
changed since the last completed run. The hash is computed over the document
*without* the Pulse front matter, so applying suggestions never invalidates
itself and forces a re-scan.
"""
from __future__ import annotations

import hashlib
import json
import re
from typing import Any, Sequence

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.llm import BackgroundSettingsView
from app.llm.base import LLMError, LLMProvider, LLMResponse
from app.llm.context import ContextBudget, ContextBudgetError
from app.models import PulseItem, PulseRun
from app.services.documents import list_documents, read_document
from app.services.paths import PathSecurityError
from app.services.proposals import apply_change, plan_edit

#: How many documents fit in one LLM request. Batching keeps a run working on
#: repos of any size and lets the context budget cap each request.
_MAX_DOCS_PER_REQUEST = 8

#: Upper bound for a document's digest text inside the prompt.
_MAX_DIGEST_CHARS = 4000

#: Relations a connection may carry. Anything else the model produces is
#: dropped rather than stored, so the data stays predictable for the UI.
_RELATIONS = frozenset({"relates-to", "supports", "contradicts", "extends"})

#: Front matter keys this module owns. They are replaced (not appended) on
#: every apply, so re-applying a suggestion is idempotent.
FRONT_MATTER_TAGS = "pulse-tags"
FRONT_MATTER_CONNECTIONS = "pulse-connections"

_PULSE_FM_LINE = re.compile(
    rf"^\s*({FRONT_MATTER_TAGS}|{FRONT_MATTER_CONNECTIONS}):", re.MULTILINE
)


class PulseError(RuntimeError):
    """A Pulse run or apply step could not be completed."""


def _digest(content: str, max_chars: int = _MAX_DIGEST_CHARS) -> str:
    """The first `max_chars` of a document, headings included by nature.

    The head of a Markdown document carries its title, its summary and the
    opening of its argument -- enough to classify it and relate it to others
    without sending the whole file.
    """
    return content[:max_chars]


def _hash_document(content: str) -> str:
    """Hash the *analysis-relevant* content: the body, without front matter.

    Front matter is metadata -- Pulse's own keys when a suggestion was
    applied, or the author's -- and the prose is what Pulse analyses. Hashing
    the body only is what makes the incremental scan honest: applying a
    suggestion rewrites the file's front matter but not its prose, so an
    applied document must not come back as "changed" on the next run.
    """
    lines = content.splitlines()
    if lines and lines[0].strip() == "---":
        end = next((i for i in range(1, len(lines)) if lines[i].strip() == "---"), None)
        if end is not None:
            relevant = "\n".join(lines[end + 1 :])
        else:
            relevant = content
    else:
        relevant = content
    return hashlib.sha256(relevant.strip().encode("utf-8", errors="replace")).hexdigest()


def with_pulse_front_matter(
    content: str,
    tags: list | None,
    connections: list | None,
) -> str:
    """Return `content` with Pulse keys written into its YAML front matter.

    Existing front matter is preserved; only the ``pulse-*`` keys are
    replaced. A document without front matter gets one. The author's prose is
    never touched, which is why front matter was chosen over inline text.

    ``tags`` and ``connections`` are independent and either may be None, which
    means "leave this key exactly as the author had it". That is what makes it
    possible to accept the tags and decline the connections, or the reverse:
    passing a list writes it, passing None leaves the existing line alone.
    Passing an empty list clears the key on purpose.
    """
    lines = content.splitlines()
    existing_tags = None
    existing_conns = None
    if lines and lines[0].strip() == "---":
        end = next((i for i in range(1, len(lines)) if lines[i].strip() == "---"), None)
        if end is None:
            raise PulseError("The document starts with '---' but has no closing fence.")
        for line in lines[1:end]:
            if line.startswith(f"{FRONT_MATTER_TAGS}:"):
                existing_tags = line.split(":", 1)[1].strip()
            elif line.startswith(f"{FRONT_MATTER_CONNECTIONS}:"):
                existing_conns = line.split(":", 1)[1].strip()
        kept = [
            line
            for line in lines[1:end]
            if not _PULSE_FM_LINE.match(line)
        ]
        # Rebuild the body from its own lines so nothing is lost or
        # duplicated: splitlines() drops the final newline, so one is
        # re-added when the original had it.
        body_lines = lines[end + 1 :]
        while body_lines and not body_lines[0].strip():
            body_lines.pop(0)
        body = "\n".join(body_lines)
        if content.endswith("\n"):
            body += "\n"
        # A key left as None keeps whatever the author (or an earlier run) had,
        # so accepting the tags alone cannot silently wipe the connections.
        tag_value = json.dumps(tags, ensure_ascii=False) if tags is not None else existing_tags
        conn_value = (
            json.dumps(connections, ensure_ascii=False)
            if connections is not None
            else existing_conns
        )
        tag_line = f"{FRONT_MATTER_TAGS}: {tag_value}" if tag_value is not None else None
        conn_line = f"{FRONT_MATTER_CONNECTIONS}: {conn_value}" if conn_value is not None else None
        block = kept + [line for line in (tag_line, conn_line) if line is not None]
        return "---\n" + "\n".join(block) + "\n---\n\n" + body

    # No front matter to preserve, so both keys are written when given.
    lines_out = []
    if tags is not None:
        lines_out.append(f"{FRONT_MATTER_TAGS}: {json.dumps(tags, ensure_ascii=False)}")
    if connections is not None:
        lines_out.append(
            f"{FRONT_MATTER_CONNECTIONS}: {json.dumps(connections, ensure_ascii=False)}"
        )
    if not lines_out:
        raise PulseError("Nothing to write: no tags or connections were selected.")
    return "---\n" + "\n".join(lines_out) + "\n---\n\n" + content

def apply_pulse_item(
    root: str,
    item: PulseItem,
    parts: Sequence[str] = ("tags", "connections"),
) -> str:
    """Write a suggestion into its document. Returns the applied path.

    Goes through plan_edit + apply_change -- the same guarded write path as
    every other write -- so path safety and the atomic write are not bypassed
    here.

    ``parts`` selects which halves of the suggestion to write. A reader often
    likes the tags and not the inferred connections, so the two are separable:
    passing ``("tags",)`` writes the tags and leaves any existing connections
    untouched, and vice versa. The item is only marked ``applied`` when both
    halves are in ``parts``; a partial write stays ``pending`` so the remainder
    can still be decided.
    """
    unknown = set(parts) - {"tags", "connections"}
    if unknown:
        raise PulseError(f"Unknown suggestion part(s): {', '.join(sorted(unknown))}.")
    selected_tags = list(item.tags) if "tags" in parts else None
    selected_conns = list(item.connections) if "connections" in parts else None
    if not any((selected_tags, selected_conns)):
        item.decision = "skipped"
        raise PulseError("Nothing to apply: this suggestion has no tags or connections.")
    try:
        content = read_document(root, item.file_path)
    except (OSError, PathSecurityError) as exc:
        raise PulseError(f"Cannot read {item.file_path}: {exc}") from exc
    updated = with_pulse_front_matter(content, selected_tags, selected_conns)
    try:
        change = plan_edit(root, item.file_path, updated)
        applied = apply_change(root, change)
    except Exception as exc:  # ProposalError and friends; surface verbatim
        raise PulseError(str(exc)) from exc
    # Both halves are now on disk, so track that rather than trusting this
    # call's `parts` alone: accepting the tags and then the connections is two
    # partial calls that together complete the suggestion. The earlier halves
    # live in the file, not on the item, so they are carried across here.
    written = set(getattr(item, "applied_parts", None) or ()) | set(parts)
    item.applied_parts = sorted(written)
    item.decision = "applied" if written >= {"tags", "connections"} else "pending"
    return applied


def _system_prompt() -> str:
    return (
        "You analyse documentation repositories. For each document you receive, "
        "produce a concise summary (one sentence), 1-5 thematic tags (single words "
        "or short hyphenated phrases, lowercase), and connections to OTHER documents "
        "from the provided list only. A connection states the relation "
        "(relates-to, supports, contradicts, extends) and one short sentence why. "
        "Only connect documents when the content genuinely supports it; an empty "
        "connection list is a valid answer. Respond with JSON only, shaped as "
        '{"documents": [{"path": "...", "summary": "...", "tags": ["..."], '
        '"connections": [{"path": "...", "relation": "relates-to", "why": "..."}], '
        '"confidence": 0.0}]}. No markdown fences, no commentary.'
    )


def _user_prompt(docs: list[dict[str, str]], all_paths: list[str]) -> str:
    parts = [
        "Documents to analyse:",
        "",
        json.dumps(
            [
                {"path": d["path"], "digest": d["digest"][:_MAX_DIGEST_CHARS]}
                for d in docs
            ],
            ensure_ascii=False,
        ),
        "",
        "All document paths in this repository (connections must reference these):",
        json.dumps(all_paths, ensure_ascii=False),
    ]
    return "\n".join(parts)


def _parse_response(text: str, expected_paths: set[str]) -> list[dict[str, Any]]:
    """Parse the model's JSON, tolerating prose around it and dropping junk."""
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.strip("`")
        if cleaned.startswith("json"):
            cleaned = cleaned[4:]
    start, end = cleaned.find("{"), cleaned.rfind("}")
    if start == -1 or end == -1:
        raise PulseError("The model response contained no JSON object.")
    try:
        data = json.loads(cleaned[start : end + 1])
    except ValueError as exc:
        raise PulseError("The model response was not valid JSON.") from exc

    out: list[dict[str, Any]] = []
    for entry in data.get("documents", []) or []:
        path = entry.get("path")
        if not path or path not in expected_paths:
            # Ignore hallucinated paths: only real documents are reported.
            continue
        tags = [
            str(t).strip().lower()
            for t in (entry.get("tags") or [])
            if isinstance(t, str) and str(t).strip()
        ][:5]
        connections = []
        for c in entry.get("connections") or []:
            if not isinstance(c, dict):
                continue
            target = c.get("path")
            relation = str(c.get("relation", "")).strip().lower()
            if target in expected_paths and target != path and relation in _RELATIONS:
                connections.append(
                    {
                        "path": target,
                        "relation": relation,
                        "why": str(c.get("why", "")).strip()[:500],
                    }
                )
        try:
            confidence = max(0.0, min(1.0, float(entry.get("confidence", 0.0))))
        except (TypeError, ValueError):
            confidence = 0.0
        out.append(
            {
                "path": path,
                "summary": str(entry.get("summary", "")).strip()[:1000],
                "tags": tags,
                "connections": connections,
                "confidence": confidence,
            }
        )
    return out


def _previous_hashes(db: Session, workspace_id: int) -> dict[str, str]:
    """The content hashes from the last completed run, by file path."""
    run = db.scalar(
        select(PulseRun)
        .where(PulseRun.workspace_id == workspace_id, PulseRun.status == "completed")
        .order_by(PulseRun.id.desc())
        .limit(1)
    )
    if run is None or not run.scanned_state:
        return {}
    hashes = run.scanned_state.get("hashes") or {}
    return {str(k): str(v) for k, v in hashes.items() if k and v}


def run_pulse(
    provider: LLMProvider,
    db: Session,
    workspace_id: int,
    root: str,
    mode: str = "suggest",
) -> PulseRun:
    """Scan the documentation repository and record a Pulse run.

    In ``apply`` mode the suggestions are also written into the documents
    immediately; in ``suggest`` mode they wait for approval.
    """
    run = PulseRun(workspace_id=workspace_id, status="pending", mode=mode)
    db.add(run)
    db.flush()

    try:
        paths = list_documents(root, ".")
    except (PathSecurityError, OSError) as exc:
        run.status = "failed"
        run.summary = f"Cannot read repository: {exc}"
        return run

    budget = ContextBudget.from_settings(BackgroundSettingsView())
    all_hashes: dict[str, str] = {}
    previous = _previous_hashes(db, workspace_id)
    changed: list[dict[str, str]] = []

    for path in paths:
        try:
            content = read_document(root, path)
        except (OSError, PathSecurityError):
            continue
        digest = _digest(content)
        if not digest.strip():
            continue
        h = _hash_document(content)
        all_hashes[path] = h
        if previous.get(path) != h:
            changed.append({"path": path, "digest": digest})

    if not changed:
        run.status = "completed"
        run.scanned_state = {"hashes": all_hashes}
        run.summary = "No changed documents since the last run."
        return run

    system = _system_prompt()
    all_paths = paths
    results: list[dict[str, Any]] = []
    errors: list[str] = []

    for i in range(0, len(changed), _MAX_DOCS_PER_REQUEST):
        batch = changed[i : i + _MAX_DOCS_PER_REQUEST]
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": _user_prompt(batch, all_paths)},
        ]
        # The context budget decides whether the request is sendable at all;
        # without the check a large batch would fail inside the provider
        # with a less actionable message.
        if not budget.fits(messages):
            errors.append(
                "A document batch did not fit the context window. "
                "Raise LLM_CONTEXT_TOKENS or lower the batch size."
            )
            continue
        try:
            response: LLMResponse = provider.chat(messages)
        except LLMError as exc:
            errors.append(str(exc))
            continue
        try:
            results.extend(
                _parse_response(response.content, set(all_paths))
            )
        except PulseError as exc:
            errors.append(str(exc))

    items: list[PulseItem] = []
    for r in results:
        item = PulseItem(
            run_id=run.id,
            file_path=r["path"],
            summary=r["summary"] or None,
            tags=r["tags"],
            connections=r["connections"],
            confidence=r["confidence"],
        )
        db.add(item)
        items.append(item)
    db.flush()

    applied_paths: list[str] = []
    if mode == "apply":
        for item in items:
            if not item.tags and not item.connections:
                item.decision = "skipped"
                continue
            try:
                applied_paths.append(apply_pulse_item(root, item))
            except PulseError as exc:
                errors.append(f"{item.file_path}: {exc}")

    run.status = "completed" if results or not errors else "failed"
    run.scanned_state = {"hashes": all_hashes}
    if errors and not results:
        run.summary = "; ".join(errors)[:2000]
    elif errors:
        run.summary = (
            f"{len(results)} document(s) analysed; "
            f"{len(errors)} failure(s): {'; '.join(errors)[:500]}"
        )
    elif applied_paths:
        run.summary = f"{len(applied_paths)} document(s) analysed and updated."
    else:
        run.summary = f"{len(results)} document(s) analysed."
    return run
