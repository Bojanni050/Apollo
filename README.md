# Gaia Docs Architect

An AI-powered documentation and architecture workspace for the Gaia ecosystem.

The application is a place where a human and an AI can **think together about
architecture before anything is changed**. Markdown files in your documentation
repository are the source of truth; the database holds only workspaces,
conversations, questions, decisions and analysis results.

> Milestones 1–3 status: backend complete and tested (76 tests). The React
> frontend and the document inventory arrive in later milestones.

## Principles encoded in the code

| Principle | Where it lives |
| --- | --- |
| Filesystem is the source of truth for documents | `services/documents.py` (read-only access) |
| **The AI cannot write files** | `services/tools.py` exposes six read-only tools; a test asserts no tool name implies a write |
| No writes without explicit human approval | `services/proposals.py` + `api/routes_proposals.py` — `accept_proposal` is the only writer |
| Source repositories are read-only | `_writable_repository()` returns 403 for `kind="source"` |
| No filesystem access outside authorized repos | `services/paths.py` — every path goes through `safe_path` |
| Original always preserved through Git | `apply_change()` refuses to touch untracked files |
| Claims are traceable | `Citation` records repo, path, lines, revision and evidence type |
| An interpretation is not a fact | evidence types are kept distinct and never silently upgraded |
| Being discussed is not being decided | `prompts.py` states this explicitly in every mode |
| No AI-generated shell commands | `services/git.py` — fixed command list, argument arrays, never a shell string |
| Git history preserved, nothing auto-committed | accepts leave the tree dirty; `requires_manual_commit` is returned to the UI |

## The proposal flow

There is deliberately **no endpoint that modifies documentation directly**.

```
plan  (read-only)          POST /proposals/move|edit|create   -> status: pending
review                     GET  /proposals/{id}               -> shows the diff
decide                     POST /proposals/{id}/accept        -> APPLIES the change
                           POST /proposals/{id}/reject        -> no-op
```

`accept` is a separate call the human makes. It re-validates against the current
filesystem, refuses untracked files, and **never commits** — the change is left
in the working tree for you to review and commit yourself.

## The agent

`Agent.run()` is a plain loop: ask the model, let it call read tools, feed the
results back, repeat (max 8 iterations, then force a final answer). There is no
agent framework and no autonomous planning.

Its six tools are all read-only: `search_documents`, `read_document`,
`read_code`, `list_documents`, `list_decisions`, `structure`. Every path
argument is untrusted model output and passes through the sandbox, so a model
that hallucinates `../../etc/passwd` gets an error string back — not a file.

Conversation modes (`explore` / `investigate` / `apply`) change **only the
system prompt**. Switching mode never discards the transcript, so a discussion
can move from exploring an idea to recording a decision without losing context.

## Layout

```
backend/
  app/
    main.py             FastAPI app
    config.py           environment-driven settings
    db.py               engine, session, declarative base
    models.py           SQLAlchemy models (state, never document content)
    schemas.py          API request/response models
    prompts.py          system prompts per conversation mode
    llm/
      base.py           LLMProvider protocol, Citation, LLMResponse
      openai_compat.py  OpenAI-compatible provider (stdlib only)
      __init__.py       provider registry
    api/
      deps.py           workspace/repository resolution + Git status
      routes_workspaces.py
      routes_documents.py
      routes_proposals.py
      routes_chat.py
    services/
      paths.py          PATH SANDBOX — security boundary
      documents.py      read-only document access
      search.py         lexical search (no vector store, by design)
      git.py            read-only Git helpers
      proposals.py      planning + the single apply path
      tools.py          the AI's read-only toolbelt
      agent.py          the tool-calling loop
  tests/                76 tests + a mock LLM server for manual runs
```

## Configuring the AI

Any OpenAI-compatible endpoint works. In `.env`:

```
LLM_BASE_URL=https://api.openai.com/v1
LLM_API_KEY=sk-...
LLM_MODEL=your-model-id
```

Omit `LLM_API_KEY` for local endpoints (Ollama, LM Studio, vLLM) that do not
require one. The app starts and the document explorer works even with no LLM
configured — only chat returns `503` with an explanatory message, and
`GET /api/workspaces/{id}/chat/status` reports `llm_configured: false`.

No model is hardcoded anywhere.

To try the chat without any API key, `backend/tests/mock_llm_server.py` is a
tiny OpenAI-compatible server that exercises the whole tool loop:

```
python tests/mock_llm_server.py 8097
LLM_BASE_URL=http://127.0.0.1:8097/v1 LLM_MODEL=mock uvicorn app.main:app
```

## Running

```bash
python -m venv .venv
.\.venv\Scripts\python -m pip install -e backend[dev]
cd backend
copy .env.example .env      # then edit DATABASE_URL
..\.venv\Scripts\python -m uvicorn app.main:app --reload
```

Interactive API docs at http://localhost:8000/docs.

### Tests

```bash
cd backend
..\.venv\Scripts\python -m pytest -q
```

Tests use an in-memory SQLite database and temporary Git repositories; they
never touch your real documentation.

### Database

PostgreSQL is the production store and the default. For a quick local start
without a database server:

```
DATABASE_URL=sqlite:///./dev.db
```

Tables are created on startup via `init_db()`. When PostgreSQL is adopted
later, replace this with a migration tool (Alembic).

## Using the API

```bash
# 1. Create a workspace
curl -X POST localhost:8000/api/workspaces -H "content-type: application/json" \
  -d '{"name": "Gaia"}'

# 2. Register your EXISTING local documentation repo (nothing is cloned)
curl -X POST localhost:8000/api/workspaces/1/repositories \
  -H "content-type: application/json" \
  -d '{"name":"gaia-docs","local_path":"C:/src/gaia-docs","kind":"documentation","writable":true}'

# 3. Browse, read, search, inspect changes
curl localhost:8000/api/workspaces/1/repositories/1/tree
curl "localhost:8000/api/workspaces/1/repositories/1/document?path=architecture/overview.md"
curl "localhost:8000/api/workspaces/1/repositories/1/search?q=memory"
curl localhost:8000/api/workspaces/1/repositories/1/git

# 4. PLAN a move -- this changes nothing on disk
curl -X POST localhost:8000/api/workspaces/1/proposals/move \
  -H "content-type: application/json" \
  -d '{"repository_id":1,"source_path":"notes.md","target_dir":"architecture/components","reason":"Memory notes belong with the component."}'

# 5. Review it (GET /proposals/2 shows the diff), then ACCEPT explicitly
curl -X POST localhost:8000/api/workspaces/1/proposals/2/accept

# 6. Discuss the architecture with the AI
curl -X POST localhost:8000/api/workspaces/1/conversations \
  -H "content-type: application/json" -d '{"mode":"investigate"}'

curl -X POST localhost:8000/api/workspaces/1/conversations/1/messages \
  -H "content-type: application/json" \
  -d '{"content":"I think our memory architecture is unnecessarily complicated."}'
```

After accepting, `git status` in your documentation repo will show the change
as uncommitted. Review it and commit it yourself.

## Safety configuration

`ALLOWED_WORKSPACE_ROOTS` restricts which directories may be registered as
repositories. Empty (the default) allows any existing local path, which is
convenient in development. Set it in production, e.g.:

```
ALLOWED_WORKSPACE_ROOTS=["C:/src/gaia-docs","C:/src/gaia"]
```

## Roadmap

- **Milestone 1 (done)** — workspaces, repositories, path sandbox, document
  tree/read/search, Git inspection.
- **Milestone 2 (done)** — approval-gated move/rename/edit/create proposals with
  readable diffs, untracked-file protection, no auto-commit.
- **Milestone 3 (done)** — LLM provider abstraction + architecture chat
  (explore / investigate / apply) with citable references and read-only tools.
- **Milestone 4** — AI document inventory and an approval-gated organization
  proposal (reuses the Milestone 2 proposal machinery).
- **Milestone 5** — React three-panel UI over everything above.
- **Later** — open questions, decision records, reconciliation.
