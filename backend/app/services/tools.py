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

from sqlalchemy.orm import Session

from app.config import settings
from app.llm.base import Citation
from app.models import Repository, Workspace
from app.services import git
from app.services.documents import DocumentError, build_tree, list_documents, read_document
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
        return "No Markdown documents found."
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
                "description": "List the architecture decision records found in the documentation repository.",
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
}
