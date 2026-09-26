"""Tools the AI can call during a conversation.

Every tool here is strictly READ-ONLY. The AI can look at documentation and
code, but it has no ability to move, edit or create a file. Changes reach the
filesystem only through a ChangeProposal that a human accepts.

All path arguments are untrusted model output and therefore pass through the
path sandbox.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.config import settings
from app.llm.base import Citation
from app.models import Decision, OpenQuestion, Repository, Workspace
from app.services import git
from app.services.documents import DocumentError, build_tree, list_documents, read_document
from app.services.inspection import (
    InspectionError,
    list_code_files,
    project_structure,
    read_source_file,
    search_code,
)
from app.services.paths import PathSecurityError, safe_path
from app.services.search import search_documents

#: A ceiling on any single tool result, in characters. This is the *source*
#: limit: it stops an unbounded read before the token budget ever sees it. The
#: budget applies a second, token-accurate limit afterwards, because characters
#: and tokens are not the same thing.
MAX_TOOL_RESULT_CHARS = 20_000
READABLE_SUFFIXES = {".md", ".py", ".ts", ".tsx", ".js", ".go", ".rs", ".java",
                     ".cs", ".rb", ".sql", ".yaml", ".yml", ".toml", ".json"}


@dataclass
class ToolContext:
    workspace: Workspace
    repositories: list[Repository]
    citations: list[Citation]
    db: Session | None = None

    def by_name(self, name: str) -> Repository | None:
        for repo in self.repositories:
            if repo.name == name:
                return repo
        return None


def _clip(text: str) -> str:
    if len(text) <= MAX_TOOL_RESULT_CHARS:
        return text
    return text[:MAX_TOOL_RESULT_CHARS] + "\n\n[truncated]"


def _resolve_repo(ctx: ToolContext, name: str) -> Repository:
    repo = ctx.by_name(name)
    if repo is None:
        available = ", ".join(r.name for r in ctx.repositories)
        raise ValueError(f"Unknown repository {name!r}. Available: {available}.")
    return repo


def _record(ctx: ToolContext, repo: Repository, path: str, evidence_type: str) -> None:
    """Remember a reference so the assistant's claims stay traceable."""
    revision = git.head_revision(repo.local_path) if git.is_repo(repo.local_path) else None
    ctx.citations.append(
        Citation(
            repository=repo.name,
            path=path,
            revision=revision,
            evidence_type=evidence_type,
        )
    )


def _numbered(content: str, start_line: Any, end_line: Any) -> tuple[str, int, int, int]:
    """Slice a line range out of file content, returning numbered text."""
    lines = content.splitlines()
    start = max(1, int(start_line or 1))
    stop = int(end_line) if end_line and int(end_line) > 0 else len(lines)
    stop = min(stop, len(lines))
    body = "\n".join(f"{i:4d}| {lines[i - 1]}" for i in range(start, stop + 1))
    return body, start, stop, len(lines)


# --------------------------------------------------------------------------
# Tool implementations
# --------------------------------------------------------------------------


def tool_list_documents(ctx: ToolContext, repository: str, path: str = ".") -> str:
    repo = _resolve_repo(ctx, repository)
    try:
        documents = list_documents(repo.local_path, path)
    except (DocumentError, PathSecurityError) as exc:
        return f"Error: {exc}"
    if not documents:
        return "No documents found."
    return "\n".join(documents)


def tool_read_document(
    ctx: ToolContext, repository: str, path: str, start_line: int = 1, end_line: int = 0
) -> str:
    """Read a document, optionally a line range. Lines are 1-based, inclusive."""
    repo = _resolve_repo(ctx, repository)
    try:
        content = read_document(repo.local_path, path)
    except (DocumentError, PathSecurityError) as exc:
        return f"Error: {exc}"

    if int(start_line or 1) > len(content.splitlines()):
        return f"Error: start_line is beyond the end of {path}."

    body, start, stop, total = _numbered(content, start_line, end_line)
    _record(ctx, repo, path, "documented_intention")
    return _clip(f"{repository}/{path} (lines {start}-{stop} of {total})\n{body}")


def tool_search_documents(
    ctx: ToolContext, query: str, repository: str = "", limit: int = 10
) -> str:
    hits: list[str] = []
    targets = [r for r in ctx.repositories if r.name == repository] if repository else ctx.repositories
    for repo in targets:
        try:
            for hit in search_documents(repo.local_path, query, limit=limit):
                hits.append(f"{repo.name}/{hit.path} (line {hit.line or 1}) - {hit.snippet}")
                _record(ctx, repo, hit.path, "documented_intention")
        except (DocumentError, PathSecurityError):
            continue
    if not hits:
        return f"No documents matched {query!r}."
    return "\n".join(hits)


def tool_read_code(
    ctx: ToolContext, repository: str, path: str, start_line: int = 1, end_line: int = 0
) -> str:
    """Read a source file so claims about implementation can be verified."""
    repo = _resolve_repo(ctx, repository)
    try:
        target = safe_path(repo.local_path, path)
    except PathSecurityError as exc:
        return f"Error: {exc}"
    if not target.exists() or target.is_dir():
        return f"Error: {path} not found in {repository}."
    if target.suffix.lower() not in READABLE_SUFFIXES:
        return f"Error: {path} is not a file type worth reading here."

    try:
        content = target.read_text(encoding="utf-8-sig", errors="replace")
    except OSError as exc:
        return f"Error: could not read {path}: {exc}"

    if int(start_line or 1) > len(content.splitlines()):
        return f"Error: start_line is beyond the end of {path}."

    body, start, stop, total = _numbered(content, start_line, end_line)
    _record(ctx, repo, path, "verified_implementation")
    return _clip(f"{repository}/{path} (lines {start}-{stop} of {total})\n{body}")


def tool_list_decisions(ctx: ToolContext, repository: str = "") -> str:
    """List decision records found on disk."""
    repo = _resolve_repo(ctx, repository or (ctx.repositories[0].name if ctx.repositories else ""))
    try:
        documents = list_documents(repo.local_path, ".")
    except (DocumentError, PathSecurityError) as exc:
        return f"Error: {exc}"
    decisions = [d for d in documents if "decision" in d.lower() or "adr" in Path(d).stem.lower()]
    if not decisions:
        return "No decision records found."
    for path in decisions:
        _record(ctx, repo, path, "explicit_decision")
    return "\n".join(decisions)


def tool_structure(ctx: ToolContext, repository: str) -> str:
    repo = _resolve_repo(ctx, repository)
    try:
        node = build_tree(repo.local_path, ".")
    except (DocumentError, PathSecurityError) as exc:
        return f"Error: {exc}"

    lines: list[str] = []

    def walk(current, depth: int) -> None:
        for child in current.children:
            lines.append(f"{'  ' * depth}{child.name}{'/' if child.is_dir else ''}")
            if child.is_dir:
                walk(child, depth + 1)

    walk(node, 0)
    return _clip(f"Structure of {repository}:\n" + "\n".join(lines))


# --------------------------------------------------------------------------
# Source-repository tools (code as architecture evidence)
#
# Source repositories are treated differently from documentation: they hold
# code, configuration and schemas. These tools give the assistant targeted
# retrieval -- never a whole-repository dump into context -- and record
# citations so architectural findings can point at the exact file.
# --------------------------------------------------------------------------


def _source_repositories(ctx: ToolContext) -> list[Repository]:
    return [r for r in ctx.repositories if r.kind == "source"]


def tool_search_code(
    ctx: ToolContext, query: str, repository: str = "", limit: int = 10
) -> str:
    """Search source code across the workspace's source repositories.

    Use this to find where something is implemented -- a class, function,
    configuration key or API -- as opposed to what is documented.
    """
    candidates = (
        [r for r in _source_repositories(ctx) if r.name == repository]
        if repository
        else _source_repositories(ctx)
    )
    if not candidates:
        names = ", ".join(r.name for r in _source_repositories(ctx)) or "(none)"
        return f"No source repositories to search. Available: {names}."

    hits: list[str] = []
    for repo in candidates:
        try:
            for hit in search_code(repo.local_path, query, limit=limit):
                hits.append(f"{repo.name}/{hit.path}:{hit.line} - {hit.snippet}")
                _record(ctx, repo, hit.path, "verified_implementation")
        except (InspectionError, PathSecurityError):
            continue
    if not hits:
        return f"No source code matched {query!r}."
    return _clip("\n".join(hits))


def tool_read_source(
    ctx: ToolContext, repository: str, path: str, start_line: int = 1, end_line: int = 0
) -> str:
    """Read a source file from a source repository with numbered lines.

    Binary and oversized files are refused; at most 400 lines are returned per
    call, so a single result can never flood the context window.
    """
    repo = _resolve_repo(ctx, repository)
    if repo.kind != "source":
        return f"Error: {repository!r} is not a source repository."
    try:
        body = read_source_file(
            repo.local_path, path, start_line=start_line, end_line=end_line
        )
    except (InspectionError, PathSecurityError) as exc:
        return f"Error: {exc}"
    _record(ctx, repo, path, "verified_implementation")
    return _clip(body)


def tool_list_source_files(
    ctx: ToolContext, repository: str, path: str = ".", limit: int = 100
) -> str:
    """List text source files under a path, skipping ignored directories."""
    repo = _resolve_repo(ctx, repository)
    if repo.kind != "source":
        return f"Error: {repository!r} is not a source repository."
    try:
        entries = list_code_files(
            repo.local_path, path, limit=max(1, min(int(limit), 400))
        )
    except (InspectionError, PathSecurityError) as exc:
        return f"Error: {exc}"
    if not entries:
        return f"No source files found under {path!r}."
    return _clip(
        f"Source files in {repository}/{path}:\n"
        + "\n".join(f"{e.path} ({e.size} bytes)" for e in entries)
    )


def tool_source_structure(ctx: ToolContext, repository: str, depth: int = 3) -> str:
    """Show a compact project-structure summary of a source repository.

    Depth-limited; use it to orient before listing or reading specific files.
    """
    repo = _resolve_repo(ctx, repository)
    if repo.kind != "source":
        return f"Error: {repository!r} is not a source repository."
    try:
        structure = project_structure(
            repo.local_path, max_depth=max(1, min(int(depth), 6))
        )
    except (InspectionError, PathSecurityError) as exc:
        return f"Error: {exc}"
    _record(ctx, repo, ".", "verified_implementation")
    return _clip(f"Structure of {repository}:\n{structure}")




def tool_search_questions(
    ctx: ToolContext, query: str = "", status: str = "", limit: int = 10
) -> str:
    """Search open questions in the current workspace by query and/or status."""
    if ctx.db is None:
        return "Error: Database session not available."

    stmt = select(OpenQuestion).where(OpenQuestion.workspace_id == ctx.workspace.id)
    if status.strip():
        stmt = stmt.where(OpenQuestion.status == status.strip().lower())
    if query.strip():
        pattern = f"%{query.strip()}%"
        stmt = stmt.where(
            or_(
                OpenQuestion.title.ilike(pattern),
                OpenQuestion.description.ilike(pattern),
            )
        )
    stmt = stmt.order_by(OpenQuestion.updated_at.desc()).limit(max(1, min(int(limit or 10), 20)))
    questions = ctx.db.scalars(stmt).all()
    if not questions:
        return f"No open questions found matching query={query!r} status={status!r}."

    lines: list[str] = []
    for q in questions:
        aff = f" (affected: {', '.join(str(a) for a in q.affected)})" if q.affected else ""
        desc_snippet = (q.description[:100] + "...") if len(q.description) > 100 else q.description
        lines.append(f"#{q.id} [{q.status}] {q.title}{aff} - {desc_snippet}")
        ctx.citations.append(
            Citation(
                repository="workspace",
                path=f"questions/{q.id}",
                evidence_type="uncertainty",
                note=q.title,
                question_id=q.id,
            )
        )
    return "\n".join(lines)


def tool_search_decisions(
    ctx: ToolContext, query: str = "", status: str = "", limit: int = 10
) -> str:
    """Search architectural decisions in the current workspace by query and/or status."""
    if ctx.db is None:
        return "Error: Database session not available."

    stmt = select(Decision).where(Decision.workspace_id == ctx.workspace.id)
    if status.strip():
        stmt = stmt.where(Decision.status == status.strip().lower())
    if query.strip():
        pattern = f"%{query.strip()}%"
        stmt = stmt.where(
            or_(
                Decision.title.ilike(pattern),
                Decision.context.ilike(pattern),
                Decision.decision.ilike(pattern),
                Decision.rationale.ilike(pattern),
            )
        )
    stmt = stmt.order_by(Decision.updated_at.desc()).limit(max(1, min(int(limit or 10), 20)))
    decisions = ctx.db.scalars(stmt).all()
    if not decisions:
        return f"No decisions found matching query={query!r} status={status!r}."

    lines: list[str] = []
    repo_name = ctx.repositories[0].name if ctx.repositories else "workspace"
    for d in decisions:
        adr = f" (ADR: {d.markdown_path})" if d.markdown_path else ""
        snippet = (d.decision[:100] + "...") if len(d.decision) > 100 else d.decision
        status_label = d.status
        if d.superseded_by_id:
            status_label = f"superseded by #{d.superseded_by_id}"
        elif d.supersedes:
            status_label = f"{d.status} (supersedes #{', #'.join(str(s.id) for s in d.supersedes)})"
        lines.append(f"#{d.id} [{status_label}] {d.title}{adr} - {snippet}")
        citation_note = d.title
        if d.superseded_by_id:
            citation_note = f"[Superseded by #{d.superseded_by_id}] {d.title}"
        ctx.citations.append(
            Citation(
                repository=repo_name if d.markdown_path else "workspace",
                path=d.markdown_path or f"decisions/{d.id}",
                evidence_type="explicit_decision",
                note=citation_note,
                decision_id=d.id,
            )
        )
    return "\n".join(lines)


def tool_get_question(ctx: ToolContext, question_id: int) -> str:
    """Retrieve full details of an open architectural question by ID."""
    if ctx.db is None:
        return "Error: Database session not available."

    q = ctx.db.scalar(
        select(OpenQuestion).where(
            OpenQuestion.id == int(question_id),
            OpenQuestion.workspace_id == ctx.workspace.id,
        )
    )
    if q is None:
        return f"Error: Question #{question_id} not found in workspace {ctx.workspace.id}."

    ctx.citations.append(
        Citation(
            repository="workspace",
            path=f"questions/{q.id}",
            evidence_type="uncertainty",
            note=q.title,
            question_id=q.id,
        )
    )

    lines = [
        f"Question #{q.id} (UID: {q.uid})",
        f"Title: {q.title}",
        f"Status: {q.status}",
        f"Source: {q.source}",
        f"Affected: {', '.join(str(a) for a in q.affected) if q.affected else 'none'}",
        f"Description:\n{q.description or '(no description)'}",
    ]
    if q.resolution:
        lines.append(f"Resolution:\n{q.resolution}")
    return "\n".join(lines)


def tool_get_decision(ctx: ToolContext, decision_id: int) -> str:
    """Retrieve full details of an architectural decision by ID."""
    if ctx.db is None:
        return "Error: Database session not available."

    d = ctx.db.scalar(
        select(Decision).where(
            Decision.id == int(decision_id),
            Decision.workspace_id == ctx.workspace.id,
        )
    )
    if d is None:
        return f"Error: Decision #{decision_id} not found in workspace {ctx.workspace.id}."

    repo_name = ctx.repositories[0].name if ctx.repositories else "workspace"
    primary_note = d.title
    if d.superseded_by_id:
        primary_note = f"[Superseded by #{d.superseded_by_id}] {d.title}"

    ctx.citations.append(
        Citation(
            repository=repo_name if d.markdown_path else "workspace",
            path=d.markdown_path or f"decisions/{d.id}",
            evidence_type="explicit_decision",
            note=primary_note,
            decision_id=d.id,
        )
    )

    lines = [
        f"Decision #{d.id}",
        f"Title: {d.title}",
        f"Status: {d.status}",
    ]
    if d.superseded_by_id:
        lines.append(f"Superseded By: Decision #{d.superseded_by_id}" + (f" ({d.superseded_by.title})" if d.superseded_by else ""))
        if d.superseded_by:
            ctx.citations.append(
                Citation(
                    repository=repo_name if d.superseded_by.markdown_path else "workspace",
                    path=d.superseded_by.markdown_path or f"decisions/{d.superseded_by.id}",
                    evidence_type="explicit_decision",
                    note=f"[Superseding Decision] {d.superseded_by.title}",
                    decision_id=d.superseded_by.id,
                )
            )
    if d.supersedes:
        lines.append(f"Supersedes: {', '.join(f'Decision #{s.id} ({s.title})' for s in d.supersedes)}")
        for s in d.supersedes:
            ctx.citations.append(
                Citation(
                    repository=repo_name if s.markdown_path else "workspace",
                    path=s.markdown_path or f"decisions/{s.id}",
                    evidence_type="explicit_decision",
                    note=f"[Superseded Decision] {s.title}",
                    decision_id=s.id,
                )
            )

    lines.extend([
        f"ADR Path: {d.markdown_path or '(no ADR generated yet)'}",
        f"Decided On: {d.decided_on.isoformat() if d.decided_on else 'none'}",
        f"Approved At: {d.approved_at.isoformat() if d.approved_at else 'none'}",
        f"Context:\n{d.context or '(none)'}",
        f"Decision:\n{d.decision or '(none)'}",
        f"Rationale:\n{d.rationale or '(none)'}",
        f"Consequences:\n{d.consequences or '(none)'}",
    ])
    if d.related_questions:
        lines.append(f"Related Questions: {', '.join(str(q) for q in d.related_questions)}")
    if d.related_documents:
        lines.append(f"Related Documents: {', '.join(str(doc) for doc in d.related_documents)}")
    return "\n".join(lines)


def tool_draft_question(
    ctx: ToolContext,
    title: str,
    description: str = "",
    affected: list[str] | None = None,
    status: str = "open",
) -> str:
    """Draft an OpenQuestion for operator review.

    IMPORTANT: Drafting is not persistence. This tool does NOT write to the
    database. The draft is surfaced to the operator in the UI for review.
    """
    clean_title = (title or "").strip()
    if not clean_title:
        return "Error: Question title cannot be empty."

    return (
        f"Drafted OpenQuestion: {clean_title!r}. "
        "This draft proposal has been presented to the operator for review in the chat UI. "
        "It is NOT yet saved in the database. The operator must explicitly review and save it."
    )


def tool_draft_decision(
    ctx: ToolContext,
    title: str,
    context: str = "",
    decision: str = "",
    rationale: str = "",
    consequences: str = "",
    related_questions: list[Any] | None = None,
    related_documents: list[str] | None = None,
) -> str:
    """Draft an architectural Decision for operator review.

    IMPORTANT: Drafting is not persistence. This tool does NOT write to the
    database, does NOT approve, and does NOT generate an ADR. The draft is
    surfaced to the operator in the UI for review.
    """
    clean_title = (title or "").strip()
    if not clean_title:
        return "Error: Decision title cannot be empty."

    return (
        f"Drafted Decision: {clean_title!r}. "
        "This draft decision proposal has been presented to the operator for review in the chat UI. "
        "It is NOT yet saved in the database, and is NOT approved. "
        "If the operator saves it, it enters the workspace with status 'proposed', "
        "which still requires explicit approval before any ADR is generated."
    )


def tool_check_architectural_consistency(
    ctx: ToolContext,
    title: str,
    decision: str = "",
    context: str = "",
    rationale: str = "",
    decision_id: int | None = None,
) -> str:
    """Check a proposed architectural decision against existing approved decisions and ADRs.

    Identifies potential conflicts, overlapping decisions, or compatibility.
    Does NOT approve, reject, modify, or commit anything.
    """
    if ctx.db is None:
        return "Error: Database session is not available in tool context."

    from app.services.consistency import check_consistency

    clean_title = (title or "").strip()
    if not clean_title:
        return "Error: Decision title cannot be empty."

    proposal = {
        "title": clean_title,
        "decision": (decision or "").strip(),
        "context": (context or "").strip(),
        "rationale": (rationale or "").strip(),
        "decision_id": decision_id,
    }

    result = check_consistency(
        db=ctx.db,
        workspace=ctx.workspace,
        repositories=ctx.repositories,
        proposal=proposal,
    )

    status_tag = result["status"]
    summary = result["summary"]
    findings = result["findings"]
    evidence = result["evidence"]

    # Record citations for each evidence decision
    for ev in evidence:
        ctx.citations.append(
            Citation(
                repository="",
                path=ev.get("path") or "",
                start_line=1,
                end_line=1,
                evidence_type="explicit_decision",
                decision_id=ev.get("decision_id"),
            )
        )

    lines = [
        f"Architectural Consistency Check Result: [{status_tag}]",
        f"Summary: {summary}",
    ]

    if findings:
        lines.append("\nFindings:")
        for f in findings:
            f_type = f.get("type", "finding").upper()
            d_id = f.get("decision_id")
            f_title = f.get("title", "")
            reason = f.get("reason", "")
            lines.append(f"- [{f_type}] Decision #{d_id}: '{f_title}'")
            lines.append(f"  Reason: {reason}")
            if f.get("proposed_claim") and f.get("existing_claim"):
                lines.append(f"  Proposed Claim: {f['proposed_claim']}")
                lines.append(f"  Existing Claim: {f['existing_claim']}")
            if f.get("markdown_path"):
                lines.append(f"  ADR: {f['markdown_path']}")

    if evidence:
        lines.append("\nEvidence Decisions Evaluated:")
        for ev in evidence:
            lines.append(f"- Decision #{ev['decision_id']}: '{ev['title']}' (ADR: {ev.get('path') or 'none'})")

    lines.append("\nNote: This check reports observations and evidence for operator review. It does NOT automatically approve, reject, or modify any decision.")
    return "\n".join(lines)



def _repo_property() -> dict[str, Any]:
    return {
        "type": "string",
        "description": "Repository name, exactly as listed in the workspace.",
    }


def tool_schemas(repository_names: list[str]) -> list[dict[str, Any]]:
    """OpenAI-format tool definitions, specialised to the workspace's repos."""
    repo_hint = f" Known repositories: {', '.join(repository_names)}." if repository_names else ""
    return [
        {
            "type": "function",
            "function": {
                "name": "search_documents",
                "description": "Search documentation and code across the workspace for relevant content. Use this first when you do not know which file holds the answer.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "query": {"type": "string", "description": "Keywords or phrase to search for."},
                        "repository": {
                            **_repo_property(),
                            "description": "Restrict the search to one repository." + repo_hint,
                        },
                        "limit": {"type": "integer", "description": "Maximum results (default 10)."},
                    },
                    "required": ["query"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "read_document",
                "description": "Read a Markdown document, optionally a line range. Returns numbered lines so you can cite exact line numbers.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "repository": _repo_property(),
                        "path": {"type": "string", "description": "Repository-relative path, e.g. architecture/overview.md"},
                        "start_line": {"type": "integer", "description": "1-based first line."},
                        "end_line": {"type": "integer", "description": "1-based last line; 0 means to the end."},
                    },
                    "required": ["repository", "path"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "read_code",
                "description": "Read a source file from a code repository. Use this to verify what is actually implemented, as opposed to what is documented.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "repository": _repo_property(),
                        "path": {"type": "string", "description": "Repository-relative path, e.g. src/memory.py"},
                        "start_line": {"type": "integer"},
                        "end_line": {"type": "integer"},
                    },
                    "required": ["repository", "path"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "list_documents",
                "description": "List all Markdown documents under a path in a repository.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "repository": _repo_property(),
                        "path": {"type": "string", "description": "Subdirectory; use '.' for the repository root."},
                    },
                    "required": ["repository"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "list_decisions",
                "description": "List the architecture decision records found on disk in the documentation repository.",
                "parameters": {
                    "type": "object",
                    "properties": {"repository": _repo_property()},
                    "required": [],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "structure",
                "description": "Show the directory structure of a repository.",
                "parameters": {
                    "type": "object",
                    "properties": {"repository": _repo_property()},
                    "required": ["repository"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "search_code",
                "description": (
                    "Search SOURCE CODE in the workspace's source repositories (code, config, "
                    "schemas, APIs) for a class, function, identifier or keyword. Use this to "
                    "find where something is actually implemented, as opposed to what the "
                    "documentation says. Returns repository, file path and line number; cite them."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "query": {
                            "type": "string",
                            "description": "Identifier, class, function or keyword to find.",
                        },
                        "repository": {
                            **_repo_property(),
                            "description": "Restrict the search to one source repository."
                            + repo_hint,
                        },
                        "limit": {
                            "type": "integer",
                            "description": "Maximum results (default 10).",
                        },
                    },
                    "required": ["query"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "read_source",
                "description": (
                    "Read a source file from a source repository, optionally a line range. "
                    "Returns numbered lines. Refuses binary files. Use it to verify what the "
                    "code actually does before claiming it matches (or contradicts) the documentation."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "repository": _repo_property(),
                        "path": {
                            "type": "string",
                            "description": "Repository-relative path, e.g. src/memory.py",
                        },
                        "start_line": {"type": "integer", "description": "1-based first line."},
                        "end_line": {
                            "type": "integer",
                            "description": "1-based last line; 0 means auto (max 400 lines).",
                        },
                    },
                    "required": ["repository", "path"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "list_source_files",
                "description": (
                    "List text source files under a path in a source repository, skipping "
                    "ignored directories (node_modules, build output, etc.). Use it to orient "
                    "yourself before reading specific files."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "repository": _repo_property(),
                        "path": {
                            "type": "string",
                            "description": "Subdirectory; '.' for the repository root.",
                        },
                        "limit": {
                            "type": "integer",
                            "description": "Maximum files (default 100).",
                        },
                    },
                    "required": ["repository"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "source_structure",
                "description": (
                    "Show a compact, depth-limited project structure of a source repository "
                    "(directories and text files, ignoring build artifacts). Use it to answer "
                    '"where does X live?" questions.'
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "repository": _repo_property(),
                        "depth": {
                            "type": "integer",
                            "description": "Maximum depth (default 3).",
                        },
                    },
                    "required": ["repository"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "search_questions",
                "description": "Search existing open architectural questions in the workspace by query or status (open, answered, resolved).",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "query": {"type": "string", "description": "Search keywords matching title or description."},
                        "status": {
                            "type": "string",
                            "enum": ["open", "answered", "resolved"],
                            "description": "Optional status filter.",
                        },
                        "limit": {"type": "integer", "description": "Maximum number of questions (default 10)."},
                    },
                    "required": [],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "search_decisions",
                "description": "Search architectural decisions in the workspace by query or status (proposed, approved, rejected, superseded).",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "query": {"type": "string", "description": "Search keywords matching title, context, decision, or rationale."},
                        "status": {
                            "type": "string",
                            "enum": ["proposed", "approved", "rejected", "superseded"],
                            "description": "Optional status filter.",
                        },
                        "limit": {"type": "integer", "description": "Maximum number of decisions (default 10)."},
                    },
                    "required": [],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "get_question",
                "description": "Retrieve full details of an open architectural question by its ID.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "question_id": {"type": "integer", "description": "The ID of the question to retrieve."},
                    },
                    "required": ["question_id"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "get_decision",
                "description": "Retrieve full details of an architectural decision by its ID.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "decision_id": {"type": "integer", "description": "The ID of the decision to retrieve."},
                    },
                    "required": ["decision_id"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "draft_question",
                "description": "Draft a new OpenQuestion for operator review. IMPORTANT: Drafting does not save to the database. The operator will review and save it in the UI.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "title": {"type": "string", "description": "Question title summary."},
                        "description": {"type": "string", "description": "Background, trade-offs, and why this is unresolved."},
                        "affected": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "Components, files, or subsystems affected.",
                        },
                        "status": {
                            "type": "string",
                            "enum": ["open", "answered", "resolved"],
                            "description": "Initial status (default 'open').",
                        },
                    },
                    "required": ["title"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "draft_decision",
                "description": "Draft an architectural Decision for operator review. IMPORTANT: Drafting does not save to the database and does not approve or create an ADR. The operator will review and save it in the UI, and must explicitly approve it later.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "title": {"type": "string", "description": "Decision title."},
                        "context": {"type": "string", "description": "Context and problem statement."},
                        "decision": {"type": "string", "description": "The chosen architectural solution or policy."},
                        "rationale": {"type": "string", "description": "Why this was chosen over alternatives."},
                        "consequences": {"type": "string", "description": "Positive and negative consequences, trade-offs."},
                        "related_questions": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "Referenced question IDs or titles.",
                        },
                        "related_documents": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "Referenced document file paths.",
                        },
                    },
                    "required": ["title"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "check_architectural_consistency",
                "description": (
                    "Check a proposed Decision or draft against existing approved architectural decisions and ADRs. "
                    "Reports potential conflicts, contradictions, overlapping decisions, or compatibility. "
                    "Does not approve, reject, or modify any decision."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "title": {"type": "string", "description": "Title of the proposed decision."},
                        "decision": {"type": "string", "description": "The proposed architectural choice or policy."},
                        "context": {"type": "string", "description": "Context or problem statement."},
                        "rationale": {"type": "string", "description": "Rationale or justification."},
                        "decision_id": {"type": "integer", "description": "Optional ID of an existing decision in the database to exclude from self-comparison."},
                    },
                    "required": ["title"],
                },
            },
        },
    ]


def run_tool(name: str, ctx: ToolContext, arguments: dict[str, Any]) -> str:
    """Execute a tool by name, converting failures into readable messages.

    A failing tool must never crash the conversation; the model is told what
    went wrong and can correct itself.
    """
    function = TOOL_REGISTRY.get(name)
    if function is None:
        return f"Error: unknown tool {name!r}. Available: {', '.join(sorted(TOOL_REGISTRY))}."

    # Only pass arguments the tool actually accepts, so a hallucinated extra
    # key cannot raise a TypeError.
    import inspect

    accepted = set(inspect.signature(function).parameters) - {"ctx"}
    kwargs = {k: v for k, v in (arguments or {}).items() if k in accepted}
    try:
        return function(ctx, **kwargs)
    except PathSecurityError as exc:
        return f"Error: {exc}"
    except Exception as exc:  # noqa: BLE001 - surfaced to the model, never fatal
        return f"Error: {type(exc).__name__}: {exc}"

# --------------------------------------------------------------------------
# Registry
# --------------------------------------------------------------------------

ToolFunction = Callable[..., str]

TOOL_REGISTRY: dict[str, ToolFunction] = {
    "list_documents": tool_list_documents,
    "read_document": tool_read_document,
    "search_documents": tool_search_documents,
    "read_code": tool_read_code,
    "list_decisions": tool_list_decisions,
    "structure": tool_structure,
    "search_code": tool_search_code,
    "read_source": tool_read_source,
    "list_source_files": tool_list_source_files,
    "source_structure": tool_source_structure,
    "search_questions": tool_search_questions,
    "search_decisions": tool_search_decisions,
    "get_question": tool_get_question,
    "get_decision": tool_get_decision,
    "draft_question": tool_draft_question,
    "draft_decision": tool_draft_decision,
    "check_architectural_consistency": tool_check_architectural_consistency,
}

