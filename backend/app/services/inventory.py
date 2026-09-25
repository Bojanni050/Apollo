"""Document inventory: understand what exists, and where it belongs.

The AI reads each document's actual CONTENT and classifies it from that --
never from its filename. A file called `notes.md` may turn out to be an
architectural decision, and the inventory should say so.

The output is always a *proposal*. Creating a plan moves nothing; a human
accepts individual suggestions or the whole plan before anything is touched.

Classification is deliberately conservative. When a document does not clearly
belong anywhere, it is marked ambiguous rather than being confidently filed
somewhere plausible-looking.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from app.llm.base import LLMProvider
from app.services.documents import list_documents, read_document
from app.services.paths import PathSecurityError

# The intended documentation structure.
TARGET_STRUCTURE = {
    "foundation": "Core concepts, principles and vocabulary that the rest of the system assumes.",
    # A document may legitimately sit directly in architecture/ as an overview,
    # so the parent folder is a valid destination in its own right.
    "architecture": "Cross-cutting architecture material: an overview of the system as a whole.",
    "architecture/components": "A description of a single component, service or module: what it owns and its boundaries.",
    "architecture/flows": "How work moves through the system: sequences of steps across components and services.",
    "architecture/contracts": "Interfaces, APIs, schemas and data shapes exchanged between parts of the system.",
    "architecture/decisions": "Records of choices that were made, and the reasoning behind them.",
    "development": "How to build, test and contribute: local setup, tooling, conventions, workflow.",
    "operations": "Running the system: deployment, configuration, monitoring, troubleshooting.",
}

MAX_DOCUMENT_CHARS = 12_000
BATCH_SIZE = 12



@dataclass
class Classification:
    """The AI's reading of one document."""

    path: str
    purpose: str
    suggested_path: str | None
    confidence: float = 0.0
    overlaps: list[str] = field(default_factory=list)
    ambiguous: bool = False
    note: str | None = None


@dataclass
class InventoryResult:
    classifications: list[Classification]
    summary: str = ""


def _system_prompt() -> str:
    layout = "\n".join(f"- {path}/ — {desc}" for path, desc in TARGET_STRUCTURE.items())
    return f"""\
You are organising a software architecture documentation repository for Gaia.

You will be shown the CONTENTS of documents. Classify each one from what it
actually says, not from its filename or its current location. A file named
"notes.md" might turn out to record an architectural decision, and a file named
"architecture.md" might be a scratch note.

The intended structure is:
{layout}

For each document report:
- purpose: one or two sentences on what the document is really for.
- suggested_path: the folder it belongs in, or null if you are unsure.
- confidence: 0.0 to 1.0 in your classification.
- overlaps: paths of OTHER documents in this batch covering the same ground.
- ambiguous: true when you genuinely cannot place it. Say so rather than
  guessing. An honest "I am not sure" beats a confident wrong answer.
- note: a brief reason, especially for surprising or ambiguous cases.

Rules:
- Never rewrite or summarise a document. You are only classifying it.
- If a document is already in the right place, still report it with its
  current folder as suggested_path, so the plan is complete.
- Base every claim on the text provided. Do not speculate about unseen files.

Answer with JSON only, in the form:
{{"classifications": [{{"path": "...", "purpose": "...", "suggested_path": "...",
  "confidence": 0.0, "overlaps": [], "ambiguous": false, "note": "..."}}],
  "summary": "one paragraph on the state of this documentation"}}
"""



def _parse_response(raw: str, expected: list[str]) -> InventoryResult:
    """Parse the model's JSON, tolerating code fences and stray prose."""
    text = (raw or "").strip()
    if text.startswith("```"):
        text = text.split("```")[1]
        if text.startswith("json"):
            text = text[4:]
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end == -1:
        return InventoryResult(classifications=[], summary="Could not parse the response.")
    try:
        data = json.loads(text[start : end + 1])
    except ValueError:
        return InventoryResult(classifications=[], summary="Could not parse the response.")

    known = set(expected)
    out: list[Classification] = []
    for entry in data.get("classifications", []) or []:
        path = entry.get("path")
        if not path or path not in known:
            # Ignore hallucinated paths: only real documents are reported.
            continue
        suggested = entry.get("suggested_path")
        if suggested is not None:
            suggested = str(suggested).strip().strip("/")
            if suggested and suggested not in TARGET_STRUCTURE:
                # The model invented a folder; treat the item as unplaced.
                suggested = None
        try:
            confidence = max(0.0, min(1.0, float(entry.get("confidence", 0.0))))
        except (TypeError, ValueError):
            confidence = 0.0

        overlaps = [o for o in (entry.get("overlaps") or []) if o in known]
        out.append(
            Classification(
                path=path,
                purpose=str(entry.get("purpose", "")).strip(),
                suggested_path=suggested,
                confidence=confidence,
                overlaps=overlaps,
                ambiguous=bool(entry.get("ambiguous", False)) or suggested is None,
                note=(str(entry["note"]).strip() or None) if entry.get("note") else None,
            )
        )
    return InventoryResult(
        classifications=out, summary=str(data.get("summary", "")).strip()
    )


def run_inventory(provider: LLMProvider, root: str, subdir: str = ".") -> InventoryResult:
    """Read every document and ask the AI to classify it by content.

    Documents are processed in batches so a large repository does not produce
    one enormous request.
    """
    try:
        paths = list_documents(root, subdir)
    except PathSecurityError as exc:
        return InventoryResult(classifications=[], summary=f"Cannot read repository: {exc}")

    if not paths:
        return InventoryResult(classifications=[], summary="No Markdown documents found.")

    documents: list[dict[str, str]] = []
    for path in paths:
        try:
            documents.append({"path": path, "content": read_document(root, path)})
        except Exception:  # noqa: BLE001 - skip unreadable files, report the rest
            continue

    if not documents:
        return InventoryResult(classifications=[], summary="No readable documents found.")

    all_paths = [d["path"] for d in documents]
    results: list[Classification] = []
    summaries: list[str] = []

    for start in range(0, len(documents), BATCH_SIZE):
        batch = documents[start : start + BATCH_SIZE]
        response = provider.chat(
            [
                {"role": "system", "content": _system_prompt()},
                {"role": "user", "content": _user_prompt(batch)},
            ],
            tools=None,
        )
        parsed = _parse_response(response.content, all_paths)
        results.extend(parsed.classifications)
        if parsed.summary:
            summaries.append(parsed.summary)

    # Any document the model silently skipped is reported as unclassified
    # rather than vanishing from the plan.
    seen = {c.path for c in results}
    for path in all_paths:
        if path not in seen:
            results.append(
                Classification(
                    path=path,
                    purpose="(not classified)",
                    suggested_path=None,
                    confidence=0.0,
                    ambiguous=True,
                    note="The model did not return a classification for this document.",
                )
            )

    return InventoryResult(classifications=results, summary=" ".join(summaries).strip())


def _user_prompt(documents: list[dict[str, str]]) -> str:
    parts = []
    for doc in documents:
        content = doc["content"]
        body = (
            content[:MAX_DOCUMENT_CHARS] + "\n[...truncated...]"
            if len(content) > MAX_DOCUMENT_CHARS
            else content
        )
        parts.append(f"--- {doc['path']} ---\n{body}")
    return "\n\n".join(parts)
