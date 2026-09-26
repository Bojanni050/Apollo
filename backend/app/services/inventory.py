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

from app.config import settings
from app.llm.base import LLMProvider
from app.llm.context import (
    MESSAGE_OVERHEAD_TOKENS,
    ContextBudget,
    ContextBudgetError,
    count_tokens,
)
from app.services.documents import list_documents, read_document
from app.services.markdown_structure import build_representation
from app.services.paths import PathSecurityError

#: Framing cost charged per document when sizing a batch (its header, the
#: separators, and the newline joins around it).
DOCUMENT_FRAMING_TOKENS = 12

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

#: Ceiling on documents per request. The real limit is the token budget: a
#: document is added to a batch only while the batch still fits. This bound
#: just stops a huge repository producing thousands of tiny documents in one
#: request, which would waste the budget on framing overhead.
BATCH_SIZE = 12


def _system_prompt() -> str:
    layout = "\n".join(f"- {path}/ — {desc}" for path, desc in TARGET_STRUCTURE.items())
    return f"""\
You are organising a software architecture documentation repository for Gaia.

You will be shown the CONTENTS of documents. Classify each one from what it
actually says, not from its filename or its current location. A file named
"notes.md" might turn out to record an architectural decision, and a file named
"architecture.md" might be a scratch note.

Long documents are shown to you as a STRUCTURAL DIGEST: the complete heading
outline, the opening of every section, and an inventory of code blocks and
links. The digest covers the WHOLE file, not just its beginning. Read the
headings before deciding -- a document's purpose is often only clear from the
combination of its sections. If a digest says it is partial, treat that
document as lower confidence and say so in your note.

The intended structure is:
{layout}

For each document report:
- purpose: one or two sentences on what the document is really for.
- suggested_path: the folder it belongs in, or null if you are unsure.
- confidence: 0.0 to 1.0 in your classification. Be honest: 0.9 means the
  document's own content makes the placement clear. If you had to guess, or
  you only saw a digest, report a low number rather than a reassuring one.
- alternatives: other folders you seriously considered. Empty when the
  placement is clear.
- overlaps: paths of OTHER documents in this batch covering the same ground.
- ambiguous: true when you genuinely cannot place it. Say so rather than
  guessing. An honest "I am not sure" beats a confident wrong answer.
- note: a brief reason, especially for surprising or ambiguous cases.
- reason: one sentence on the evidence in the document that drove the decision.

Rules:
- Never rewrite or summarise a document. You are only classifying it.
- If a document is already in the right place, still report it with its
  current folder as suggested_path, so the plan is complete.
- Base every claim on the text provided. Do not speculate about unseen files.
- Judge from the whole document, including its later sections. The point of the
  digest is that you can.

Answer with JSON only, in the form:
{{"classifications": [{{"path": "...", "purpose": "...", "suggested_path": "...",
  "confidence": 0.0, "alternatives": [], "overlaps": [], "ambiguous": false,
  "note": "...", "reason": "..."}}],
  "summary": "one paragraph on the state of this documentation"}}
"""


#: Below this confidence a classification is treated as low-confidence and
#: surfaced to the user rather than presented as settled. A suggestion the
#: model is not sure about is worse than no suggestion: it looks like a fact.
LOW_CONFIDENCE_THRESHOLD = 0.6


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
    #: Categories the model considered and rejected, with no explanation of why.
    #: These are what make a judgement reviewable rather than magic.
    alternatives: list[str] = field(default_factory=list)
    #: The model's stated reasoning, kept separately from the user-facing note.
    reason: str | None = None
    #: True when the document was represented by a structural digest rather than
    #: sent in full, so the user knows the classification rests on a summary.
    partial: bool = False

    @property
    def low_confidence(self) -> bool:
        return self.confidence < LOW_CONFIDENCE_THRESHOLD


@dataclass
class InventoryResult:
    classifications: list[Classification]
    summary: str = ""


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
        # Competing categories are kept only when they are real folders in the
        # target structure; an invented alternative is noise, not information.
        alternatives = [
            str(a).strip().strip("/")
            for a in (entry.get("alternatives") or [])
            if str(a).strip().strip("/") in TARGET_STRUCTURE
        ]
        out.append(
            Classification(
                path=path,
                purpose=str(entry.get("purpose", "")).strip(),
                suggested_path=suggested,
                confidence=confidence,
                overlaps=overlaps,
                ambiguous=bool(entry.get("ambiguous", False)) or suggested is None,
                note=(str(entry["note"]).strip() or None) if entry.get("note") else None,
                alternatives=alternatives,
                reason=(
                    (str(entry["reason"]).strip() or None) if entry.get("reason") else None
                ),
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

    budget = ContextBudget.from_settings(settings)
    system = _system_prompt()

    for batch in _batches(documents, budget):
        user_content, digest_paths = _user_prompt(batch, budget)
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": user_content},
        ]
        if not budget.fits(messages):
            # Should be unreachable: _batches sizes each batch to the budget and
            # build_representation fits each document to its share. Asserted
            # rather than assumed, because sending this would fail at the
            # provider with a worse message.
            raise ContextBudgetError(
                "The inventory request for this batch does not fit the configured "
                f"context window ({budget.total_tokens(messages):,} tokens, "
                f"{budget.context_tokens:,} available). Raise LLM_CONTEXT_TOKENS."
            )

        response = provider.chat(messages, tools=None)
        parsed = _parse_response(response.content, all_paths)
        # Anything the model saw only as a digest is marked, so a lower
        # confidence is visibly explained rather than mysterious.
        for classification in parsed.classifications:
            classification.partial = classification.path in digest_paths
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


def _document_budget(budget: ContextBudget) -> int:
    """Tokens available for document bodies in a single request.

    The system prompt and the message framing come out of the input budget
    first. Allocating the whole ``input_budget`` to documents and only
    discovering the prompt does not fit afterwards is how a request ends up
    oversized by exactly the amount nobody accounted for.
    """
    available = budget.input_budget - count_tokens(_system_prompt()) - MESSAGE_OVERHEAD_TOKENS
    if available <= 0:
        raise ContextBudgetError(
            "The inventory prompt alone exceeds the configured context window. "
            "Raise LLM_CONTEXT_TOKENS."
        )
    return available


def _batches(documents: list[dict[str, str]], budget: ContextBudget) -> list[list[dict[str, str]]]:
    """Group documents into requests that each fit the context budget.

    A document that is too large on its own is still sent, represented as a
    structural digest -- dropping it entirely would leave the inventory
    silently incomplete, and the user has no way to know which files were
    skipped.
    """
    available = _document_budget(budget)
    groups: list[list[dict[str, str]]] = []
    current: list[dict[str, str]] = []
    used = 0

    for doc in documents:
        cost = count_tokens(doc["content"]) + DOCUMENT_FRAMING_TOKENS
        if current and (len(current) >= BATCH_SIZE or used + cost > available):
            groups.append(current)
            current, used = [], 0
        current.append(doc)
        used += cost

    if current:
        groups.append(current)
    return groups


def _user_prompt(
    documents: list[dict[str, str]], budget: ContextBudget
) -> tuple[str, list[str]]:
    """Render a batch, representing each document within its share of the budget.

    Each document gets an equal share, so one enormous file cannot crowd out the
    rest of the repository. Within that share:

    * a document that **fits is sent verbatim** -- no wrapper, no annotation;
    * a document too large is replaced by a **structural digest** covering the
      whole file: complete heading outline, sampled section openings, and a
      code-block/link inventory.

    The digest is labelled, so the model is never left believing it classified a
    document it only partly saw. Returns the prompt and the paths that were
    represented as digests.
    """
    per_document = max(64, _document_budget(budget) // max(1, len(documents)))
    parts: list[str] = []
    partial: list[str] = []

    for doc in documents:
        body, mode = build_representation(doc["content"], per_document)
        if mode == "digest":
            partial.append(doc["path"])
            header = f"--- {doc['path']} (STRUCTURAL DIGEST, whole document) ---"
        else:
            header = f"--- {doc['path']} ---"
        parts.append(f"{header}\n{body}")

    return "\n\n".join(parts), partial

