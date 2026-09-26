# Gaia Docs Architect

An AI-powered documentation and architecture workspace for the Gaia ecosystem.

The application is a place where a human and an AI can **think together about
architecture before anything is changed**. Markdown files in your documentation
repository are the source of truth; the database holds only workspaces,
conversations, questions, decisions and analysis results.

> Milestones 1â€“5 complete, plus a security hardening pass. Backend (188 tests)
> and frontend (TypeScript-clean, browser-verified) are both working together.

## Running the whole thing

### As a desktop app (Tauri 2)

Double-click **`desktop.cmd`**, or:

```bash
npm run dev      # development build, with hot reload
npm run build    # release installer (.msi / NSIS .exe) in src-tauri\target\release\bundle\
```

A real application window: no browser, no second terminal, no ports to think
about. The Rust shell starts the Python API as a child process, waits until it
answers, and points the window at it. Closing the window stops the API.

**Prerequisites** (all verified present on a current Windows dev box):

| Requirement | Notes |
| --- | --- |
| Rust stable | `rustup toolchain install stable` — <https://rustup.rs/> |
| MSVC build tools | The C++ workload; Tauri will not link without it |
| WebView2 runtime | Preinstalled on Windows 10/11; the installer fetches it if not |
| Node.js + the venv | As for the browser version |

The first build compiles the Rust dependency tree and takes a few minutes;
later builds are incremental.

**How it is wired**

- `src-tauri/` holds the Rust crate. It only does two things: supervise the
  Python process and manage the window.
- The child runs `python -m app.serve`, which mounts the built frontend on the
  same port as the API. The webview and the API are therefore **same-origin** —
  no CORS configuration, no `tauri://` asset protocol, and the session cookie
  behaves like an ordinary cookie.
- The port is chosen at runtime by binding to port 0, so a running dev server
  never blocks the desktop app and two instances can coexist.
- On Windows the child is placed in a **Job Object** with
  `JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE`, so the OS reaps the whole process tree
  even if the app is force-killed. A stray server holding a port is otherwise
  easy to leave behind.

**Security.** The window requests only `core:default` capabilities — no
filesystem, no shell, no process permissions. The API is spawned from Rust
with a fixed argument list, so the webview cannot start commands at all; the
usual `tauri-plugin-shell` + `shell:allow-execute` route is deliberately not
used.

### For development

Double-click **`start.cmd`** (or `start.bat` — they are the same thing), or run:

```powershell
powershell -ExecutionPolicy Bypass -File scripts\dev.ps1
```

This starts **both** halves: it installs what is missing
(Python virtualenv, backend packages, frontend packages), writes a development
`backend/.env` if there isn't one, starts the backend, waits until
`/api/health` actually answers, starts the Vite dev server, and opens
http://127.0.0.1:5273. When it prints **The app is running**, the page works.

Shut down with **`stop.cmd`** (or `stop.bat`) / `scripts\stop.ps1`. Both servers
run in the background with their output in `.dev\`, so the script returns as
soon as the app is up rather than holding the terminal.

| | Port | Address |
| --- | --- | --- |
| App (Vite dev server) | **5273** | http://127.0.0.1:5273 |
| API (FastAPI/uvicorn) | **5274** | http://127.0.0.1:5274 |
| Interactive API docs | 5274 | http://127.0.0.1:5274/docs |

These are deliberately not the tools' defaults (Vite 5173, uvicorn 8000), which
are frequently taken by other projects on the same machine. Override them with
`-BackendPort` / `-FrontendPort`.

| Script | What it does |
| --- | --- |
| `start.cmd` / `start.bat` | **Start everything** (backend + frontend), then open the browser |
| `stop.cmd` / `stop.bat` | Stop both servers (`-Clean` also deletes `.dev\`) |
| `scripts\dev.ps1` | The same thing, if you prefer to run it from a terminal |
| `scripts\setup.ps1` | Install dependencies and create `backend\.env` |
| `scripts\start-backend.ps1` | Start only the API |
| `scripts\start-frontend.ps1` | Start only the dev server |
| `scripts\test.ps1` | Run the backend tests and the frontend build |

Useful options: `-NoBrowser` (don't open a browser), `-Port`/`-FrontendPort` to
move off a busy port, and `-Force` to restart over a previous run. All scripts
take `-Verbose` in the usual PowerShell way.

> The scripts only ever stop processes they started themselves, identified by
> the pid files in `.dev\`. If something *else* is already listening on 5274 or
> 5273, they report the pid and tell you what to do rather than killing it.

### First run: setting up a workspace

With an empty database the app opens a two-step setup wizard instead of the
three-panel view:

1. **Name the workspace** (description optional).
2. **Point it at your documentation folder** — an existing local directory of
   Markdown files. Nothing is cloned or copied. The name defaults to the folder
   name, and you can tick **Allow edits** to permit move/rename/edit proposals
   (still approval-gated; nothing is written without your explicit accept).

You can skip step 2 and register a repository later, but a workspace with no
documentation repository has an empty document tree and chat has nothing to
read, so it is worth doing now.

The path is validated by the backend, not the form: the allow-list in
`ALLOWED_WORKSPACE_ROOTS` is the security boundary, and the form shows the
server's rejection message verbatim rather than duplicating those rules in
TypeScript. The default development config allows any path.

### Doing it by hand

Two processes. Start the backend first:

```bash
cd backend
..\.venv\Scripts\python -m uvicorn app.main:app --port 5274
```

> **Set `APP_ENV=development` in `backend/.env` before starting locally.**
> The default is production, which correctly refuses to start without
> credentials, a session secret, workspace roots and CORS origins. See
> [Security](#security) and `backend/.env.example`.

Then the frontend, in a second terminal:

```bash
cd frontend
npm install
npm run dev          # http://localhost:5273
```

Vite proxies `/api` to the backend, so the browser stays same-origin and CORS
never bites in development. Point it elsewhere with
`VITE_BACKEND_URL=http://127.0.0.1:9000 npm run dev`.

### The generated development config

`scripts\setup.ps1` writes a `backend\.env` for you on first run, because the
application defaults to `APP_ENV=production` and a production configuration
without credentials deliberately refuses to start. The generated file sets:

| Setting | Value | Why |
| --- | --- | --- |
| `APP_ENV` | `development` | Relaxes the production startup checks |
| `DATABASE_URL` | `sqlite:///./gaia_dev.db` | No database server needed; the schema is created from the models on startup |
| `AUTH_ENABLED` | `false` | No login screen while working locally. Production refuses this |
| `ALLOW_UNRESTRICTED_WORKSPACE_ROOTS` | `true` | Any local directory can be registered as a repository |

Both servers bind to `127.0.0.1` only, so even unauthenticated the API is not
reachable from the network. `.env` is gitignored, and an existing one is never
overwritten.

To use a real LLM, add `LLM_API_KEY` and `LLM_MODEL` to `backend\.env`.
Everything except chat works without them. To require a login locally, set
`AUTH_ENABLED=true` and fill in `AUTH_PASSWORD_HASH` and `SESSION_SECRET`
(generate both with `python -m app.security`) â€” see
[Security](#security).

### The three panels

| Panel | Contains |
| --- | --- |
| **Left â€” Workspace** | Repositories, the real documentation tree, Git revision |
| **Centre â€” Conversation** | The architectural discussion, mode switch, citations |
| **Right â€” Context** | Document viewer, inventory plan, proposals (collapsible) |

The centre panel is the point of the application; the right panel collapses so
the discussion can take the full width.

## Security

This app reads and writes Markdown files on the host's filesystem and can run
an LLM. It is built to be reachable only by its owner.

### Threat model

The realistic attacker is **anyone who can reach the HTTP port but is not the
operator** â€” a browser-based attacker on another site, or a process on the same
network. The defences below aim at that, plus at the "I forgot to configure
something" failure mode, which is treated as just as dangerous.

### Authentication

A single local account, configured entirely through the environment. There is
no registration, no password reset and no third-party identity provider: the
operator who deploys the app is the user.

Two credentials are accepted, both checked **server-side on every request**:

| Credential | How it is obtained | Used by |
| --- | --- | --- |
| Session cookie | `POST /api/auth/login` | The browser UI |
| Bearer token | `Authorization: Bearer $AUTH_API_TOKEN` | curl, scripts, CI |

- Passwords are stored as **PBKDF2-HMAC-SHA256** hashes (600k iterations, 16-byte
  random salt). Verification is constant-time and always performs a full
  derivation, so a wrong username and a wrong password take the same time.
- Session cookies are opaque, **HMAC-signed**, and carry an expiry. The
  signature is verified *before* any other field is trusted. There is no
  server-side session store, so a stateless deployment behind a reverse proxy
  needs no shared session memory.
- Cookies are `HttpOnly` (unreadable by page scripts, so an XSS bug cannot
  exfiltrate the session), `SameSite=Strict` (blocks cross-site submission), and
  `Secure` in production.
- Every comparison uses `hmac.compare_digest`, never `==`.
- A malformed or corrupt stored hash **fails closed** â€” it denies access rather
  than raising, so a broken configuration can never become a bypass.

Enforcement lives in `AuthenticationMiddleware` in `app/main.py`, not on
individual routes. That is deliberate: a route added to a router later is
protected by default and cannot ship unauthenticated by forgetting a dependency.
The public allowlist is exactly four paths:

```
/api/health      /api/auth/login      /api/auth/logout      /api/auth/status
```

Unauthenticated callers receive `401` with a `WWW-Authenticate` header and learn
nothing about whether any workspace, document or conversation exists.

### Local development mode

Relaxed behaviour is **explicit and development-only**. It requires *both*
`APP_ENV=development` *and* the matching opt-in flag:

```bash
APP_ENV=development
AUTH_ENABLED=false
ALLOW_UNRESTRICTED_WORKSPACE_ROOTS=true
```

In production (`APP_ENV=production`, which is the **default**) either of those
two settings is a startup error. Nothing is implicit: an unset configuration
produces the strict behaviour, never the relaxed one.

### Workspace path restrictions

`ALLOWED_WORKSPACE_ROOTS` lists the directories that may be registered as
workspace repositories.

- **Production requires at least one root.** An empty list is a startup error.
- An empty list now means **"nothing can be registered"**, not "any directory".
  Previously it permitted any existing path, which turned a documentation tool
  into a filesystem browser.
- Candidate paths are **resolved before the containment check**, so `..`
  segments are collapsed first and a symlink pointing out of the tree is
  followed to its real target and rejected there.
- Containment is checked **per path component**, so `/srv/docs` does not
  authorize `/srv/docs-private`.
- The check is re-applied on **every use**, not only at registration, so a path
  that stops being authorized stops being served immediately.

### CORS

`allow_origins` comes from configuration and is never a wildcard:

- **Development** with an empty list defaults to the Vite dev server
  (`http://localhost:5273`, `http://127.0.0.1:5273`).
- **Production** requires an explicit list; an empty list is a startup error.
- `"*"` is **rejected in production**. With credentials enabled it would let
  any website on the internet make authenticated requests against this API.
- Methods and headers are enumerated rather than `"*"`.

The CORS middleware is installed *outermost*, so CORS headers are present on
`401` responses too and a browser can read why a request was refused.

### Startup validation

`Settings.validate_security()` runs in the application lifespan, **before the
port is bound**. A production deployment missing credentials, a session secret,
workspace roots or CORS origins raises `SecurityConfigurationError` and the
server never starts:

```
Refusing to start with an unsafe production configuration:
  - SESSION_SECRET is not set. Generate one with `python -m app.security generate-secret`.
  - ALLOWED_WORKSPACE_ROOTS is empty. At least one root is required, ...
```

Generate the required secrets with:

```bash
python -m app.security hash-password      # -> AUTH_PASSWORD_HASH
python -m app.security generate-secret    # -> SESSION_SECRET
```

## Layout

## Principles encoded in the code

| Principle | Where it lives |
| --- | --- |
| Filesystem is the source of truth for documents | `services/documents.py` (read-only access) |
| **The AI cannot write files** | `services/tools.py` exposes six read-only tools; a test asserts no tool name implies a write |
| **A plan is not an action** | `services/inventory.py` produces a plan; `routes_inventory.py` only moves on approval |
| Classification follows content, not filenames | the inventory prompt shows document *text*; a test asserts the model sees it |
| Admitting uncertainty is a feature | ambiguous items are flagged and are **never** auto-applied |
| No writes without explicit human approval | `services/proposals.py` + `routes_proposals.py` â€” `accept_proposal` is the only writer |
| Source repositories are read-only | `_writable_repository()` returns 403 for `kind="source"` |
| No filesystem access outside authorized repos | `services/paths.py` â€” every path goes through `safe_path`; empty root list fails closed |
| **Every API request is authenticated server-side** | `AuthenticationMiddleware` in `app/main.py` â€” a 4-path public allowlist, not per-route dependencies |
| **No accidental exposure from missing config** | `Settings.validate_security()` refuses to start an unsafe production deployment |
| **Every request fits the model's context window** | `app/llm/context.py` â€” `LLM_CONTEXT_TOKENS` is enforced, not advisory |
| Original always preserved through Git | `apply_change()` refuses to touch untracked files |
| Claims are traceable | `Citation` records repo, path, lines, revision and evidence type |
| An interpretation is not a fact | evidence types are kept distinct and never silently upgraded |
| Being discussed is not being decided | `prompts.py` states this explicitly in every mode |
| No AI-generated shell commands | `services/git.py` â€” fixed command list, argument arrays, never a shell string |
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
filesystem, refuses untracked files, and **never commits** â€” the change is left
in the working tree for you to review and commit yourself.

## The agent

`Agent.run()` is a plain loop: ask the model, let it call read tools, feed the
results back, repeat (max 8 iterations, then force a final answer). There is no
agent framework and no autonomous planning.

Its six tools are all read-only: `search_documents`, `read_document`,
`read_code`, `list_documents`, `list_decisions`, `structure`. Every path
argument is untrusted model output and passes through the sandbox, so a model
that hallucinates `../../etc/passwd` gets an error string back â€” not a file.

Conversation modes (`explore` / `investigate` / `apply`) change **only the
system prompt**. Switching mode never discards the transcript, so a discussion
can move from exploring an idea to recording a decision without losing context.

## The inventory

The AI reads every document's **contents** and proposes where each one belongs
in the target structure (`foundation/`, `architecture/{components,flows,
contracts,decisions}/`, `development/`, `operations/`). It also reports
overlapping documents and anything it cannot confidently place.

```
plan   POST   /inventory/runs                      -> a plan; moves nothing
read   GET    /inventory/runs/{id}                 -> the plan with reasons
decide POST   /inventory/runs/{id}/apply           -> approve some or all
       POST   /inventory/runs/{id}/items/{i}/apply -> approve one
       POST   /inventory/runs/{id}/items/{i}/skip  -> decline one
```

Two deliberate constraints:

- **Ambiguous items are never auto-applied.** If the model says it cannot
  place a document, the application refuses to guess on your behalf. It is
  reported and left for you.
- **A hallucinated folder is discarded.** A suggested path that is not in the
  target structure is rejected and the item is marked ambiguous, so the model
  cannot invent a location.

The inventory may create a target folder that does not exist yet (that is how
the structure gets established), but a *manually* requested move into a
non-existent folder is still refused, because that is more likely a typo.

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
      routes_inventory.py
    services/
      paths.py          PATH SANDBOX â€” security boundary
      documents.py      read-only document access
      search.py         lexical search (no vector store, by design)
      git.py            read-only Git helpers
      proposals.py      planning + the single apply path
      tools.py          the AI's read-only toolbelt
      agent.py          the tool-calling loop
      inventory.py      content-based document classification
  tests/                103 tests + a mock LLM server for manual runs
frontend/
  src/
    api/client.ts       typed API client (the only place URLs are built)
    markdown.tsx        small Markdown renderer; no innerHTML, no dependencies
    App.tsx             state and orchestration
    components/
      WorkspacePanel.tsx    left: repositories + document tree
      ConversationPanel.tsx centre: messages, mode switch, citations
      ContextPanel.tsx      right: document / inventory / proposals
  scripts/smoke.mjs     browser smoke test (playwright)
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
configured â€” only chat returns `503` with an explanatory message, and
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

To run it as a desktop window instead, add the `desktop` extra and use
`scripts\desktop.py` — see [Running the whole thing](#running-the-whole-thing).

Interactive API docs at http://localhost:5274/docs.

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

On PostgreSQL the schema is applied and verified by **Alembic**, not by
`init_db()` â€” see [Database and migrations](#database-and-migrations). On
SQLite, `init_db()` derives the schema directly from the models, which is
adequate for a throwaway local file.

## Using the API

```bash
# 1. Create a workspace
curl -X POST localhost:5274/api/workspaces -H "content-type: application/json" \
  -d '{"name": "Gaia"}'

# 2. Register your EXISTING local documentation repo (nothing is cloned)
curl -X POST localhost:5274/api/workspaces/1/repositories \
  -H "content-type: application/json" \
  -d '{"name":"gaia-docs","local_path":"C:/src/gaia-docs","kind":"documentation","writable":true}'

# 3. Browse, read, search, inspect changes
curl localhost:5274/api/workspaces/1/repositories/1/tree
curl "localhost:5274/api/workspaces/1/repositories/1/document?path=architecture/overview.md"
curl "localhost:5274/api/workspaces/1/repositories/1/search?q=memory"
curl localhost:5274/api/workspaces/1/repositories/1/git

# 4. PLAN a move -- this changes nothing on disk
curl -X POST localhost:5274/api/workspaces/1/proposals/move \
  -H "content-type: application/json" \
  -d '{"repository_id":1,"source_path":"notes.md","target_dir":"architecture/components","reason":"Memory notes belong with the component."}'

# 5. Review it (GET /proposals/2 shows the diff), then ACCEPT explicitly
curl -X POST localhost:5274/api/workspaces/1/proposals/2/accept

# 6. Discuss the architecture with the AI
curl -X POST localhost:5274/api/workspaces/1/conversations \
  -H "content-type: application/json" -d '{"mode":"investigate"}'

curl -X POST localhost:5274/api/workspaces/1/conversations/1/messages \
  -H "content-type: application/json" \
  -d '{"content":"I think our memory architecture is unnecessarily complicated."}'

# 7. Inventory the documentation -- a plan, nothing is moved
curl -X POST localhost:5274/api/workspaces/1/inventory/runs \
  -H "content-type: application/json" -d '{}'

# 8. Approve the whole plan (or individual items)
curl -X POST localhost:5274/api/workspaces/1/inventory/runs/1/apply \
  -H "content-type: application/json" -d '{}'
```

After accepting, `git status` in your documentation repo will show the change
as uncommitted. Review it and commit it yourself.

## Context management

`LLM_CONTEXT_TOKENS` is the **total** context window of the configured model,
in tokens â€” the ceiling for input *and* output combined, exactly as the model
vendor states it. It is enforced in the application, not left to the provider.

### The budget

The window is not all input:

```
+--------------------------------------------------+------------------+
|  input                                           |  output reserve  |
|  system + tools + history + tool results + user  |  LLM_MAX_OUTPUT  |
+--------------------------------------------------+------------------+
```

`input_budget = context_tokens âˆ’ max_output_tokens âˆ’ safety_margin`. The margin
is 2% (minimum 64 tokens), because tokenizers differ between implementations
and over-estimating is the safe direction.

### What is admitted, in order

1. **System prompt and tool definitions** â€” always kept. They carry the evidence
   discipline the whole product depends on.
2. **The current user message** â€” never silently removed. A request that cannot
   be answered without the actual question is not answerable, so an oversized
   message is truncated with a visible marker instead of dropped.
3. **Recent conversation turns**, newest first.
4. **Tool results** â€” budgeted like any other content. A large tool result is
   truncated rather than dropped, because the model explicitly asked for that
   evidence and discarding it would also orphan the assistant message that
   requested it.

An assistant message carrying `tool_calls` is only valid immediately before its
results, so an agentic turn is atomic: kept whole or dropped whole.

### Documents and truncation

Retrieved documents participate in the budget. A document that does not fit is
handled in two steps:

1. **Relevance-based selection.** The opening is kept (title and overview live
   there) plus the lines with the strongest keyword overlap with the current
   question, which can be anywhere in the file.
2. **Truncation**, always with an explicit marker naming how many characters
   were omitted.

Truncation is never silent. The model is told it is looking at a partial
document, so it cannot report a confident claim about text it never saw.

### Failure handling

If the request cannot be made to fit â€” even after truncating â€” the application
raises `ContextBudgetError` **before** contacting the provider. The API returns
`413` with an actionable message. A raw provider error is never the primary
user experience, and no oversized request is ever sent.

Budget decisions are logged (token count, turns dropped, turns truncated) rather
than being invisible.

### Configuration

| Variable | Default | Constraint |
| --- | --- | --- |
| `LLM_CONTEXT_TOKENS` | `128000` | 1 024 â€“ 4 000 000 |
| `LLM_MAX_OUTPUT_TOKENS` | `8192` | â‰¥ 256, and < `LLM_CONTEXT_TOKENS` |

Both are validated at construction, so an unusable configuration fails at
startup rather than on the first message. A typo such as `128000000` is
rejected by the ceiling rather than silently disabling budgeting.

## Document analysis

A long architecture document is classified from its **whole structure**, not
from its first 12,000 characters.

### The problem this solves

A head-truncation classifies a document by its *introduction*. An ADR whose
`## Decision` sits at the bottom, or a runbook whose failure modes are the
last section, gets filed by its preamble â€” which is usually the least
characteristic part of it.

### The strategy

A document that fits its share of the context budget is sent **verbatim, byte
for byte**. A document that does not is replaced by a **structural digest**
(`app/services/markdown_structure.py`), allocated in fixed proportions:

| Share | Contents |
| --- | --- |
| small, fixed | Title, document statistics, front-matter metadata |
| 35% | The **heading outline** â€” the cheapest whole-document signal there is |
| small, fixed | Code-block inventory (language, size) and link inventory |
| remainder | **Section openings**, sampled at even intervals |

Three details matter:

- **Sampling is spread, not sequential.** Taking sections in order would spend
  the whole budget on the opening and reproduce the original bug.
- **The last section is always included.** Conclusions, decisions and failure
  modes are disproportionately likely to live at the end.
- **Truncation is always announced.** A truncated outline says how many
  headings it dropped; a partial section listing says how many sections fitted.
  Nothing silently reads as complete.

The digest is bounded like any excerpt â€” but unlike an excerpt it is not
biased toward the beginning.

### Honest classification

The model is asked for, and the API now returns:

| Field | Meaning |
| --- | --- |
| `confidence` | 0.0â€“1.0, explicitly encouraged to be honest rather than reassuring |
| `alternatives` | Other folders seriously weighed â€” validated against the real structure |
| `reason` | The evidence in the document that drove the decision |
| `ambiguous` | Genuinely unplaceable; never auto-applied |
| `partial` | The classification rests on a digest, not the whole document |
| `low_confidence` | Below the threshold, surfaced in the UI as a warning tag |

The system prompt tells the model that a digest covers the whole file, that it
should judge from later sections too, and that a guess should be reported as a
low confidence rather than a comfortable one.

### Content is never modified

`build_representation` returns either the original string or a separately
constructed digest. The document on disk is never rewritten, and an inventory
run does not touch it â€” asserted by
`test_run_inventory_does_not_modify_documents`, which compares the file's
contents *and* its mtime before and after a run.

### Database

`InventoryItem` gains `alternatives`, `reason` and `partial`, via migration
`0002_inventory_confidence`. Every column is nullable or defaulted, so existing
rows are preserved: `partial` is added nullable, backfilled, then constrained.

## Database and migrations

PostgreSQL is the production database. The schema is owned by **Alembic**;
`init_db()` no longer calls `create_all` on PostgreSQL, because a table that
appears without a migration record is exactly how a database and its migrations
drift apart.

### Applying migrations

```bash
cd backend
alembic upgrade head
```

The database URL comes from `DATABASE_URL` (or `TEST_DATABASE_URL`) at
runtime â€” `alembic.ini` deliberately has no `sqlalchemy.url`, so there is one
source of truth and a stale URL in a checked-in file cannot send a migration to
the wrong database.

At startup the application **verifies** the schema rather than silently
creating tables. With `DB_MIGRATE_ON_STARTUP=false` (the default) an
out-of-date database refuses to start:

```
The database schema is out of date. Missing migration(s): 0002_....
Run `alembic upgrade head`, or set DB_MIGRATE_ON_STARTUP=true.
```

Setting `DB_MIGRATE_ON_STARTUP=true` applies pending migrations on boot. That is
off by default in production on purpose: two instances starting at once would
race, and an unreviewed migration would reach production unannounced.

### Adopting an existing database (no data loss)

If the application has already been running, the tables were created by
`create_all` and there is **no** `alembic_version` table. Do **not** run
`alembic upgrade head` â€” it will fail with `relation "workspaces" already
exists`, and it is not the right operation anyway, because the tables already
match the initial migration.

**Back up first**, then *stamp* â€” record the revision without touching data:

```bash
pg_dump "$DATABASE_URL" -Fc -f gaia_docs-$(date +%F).dump   # 1. back up
cd backend
alembic stamp head                                        # 2. record revision
alembic upgrade head                                      # 3. no-op; confirms alignment
```

`stamp` writes only the `alembic_version` row. It does not create, alter or
drop any table, and it does not touch a single row of application data. This
path is covered by
`tests/test_migrations.py::test_existing_create_all_database_is_adopted_by_stamping`,
which creates a `create_all` database, inserts a row, and asserts the row
survives the stamp.

> **Note on the initial revision.** The first migration already reflects the
> PostgreSQL-appropriate schema (JSONB, native UUID, `timestamptz`, CHECK
> constraints). Existing SQLite data needs no conversion; existing PostgreSQL
> data created by the old `create_all` code will differ in *column types only*
> (`VARCHAR(40)` instead of `UUID`, `JSON` instead of `JSONB`) â€” see
> [Remaining compatibility concerns](#remaining-database-concerns). Nothing in
> this task performs that conversion, because it would rewrite existing rows.

### Rolling back

```bash
alembic downgrade -1     # one revision
alembic downgrade base   # drop everything
```

`downgrade` on the initial revision drops all tables. It is only appropriate
for a disposable database â€” the test suite's migration database, for instance.

### SQLite

SQLite remains supported for local development and the default test suite. The
same initial revision runs on both: `JSONB` renders as `JSON`, and `UUID` as
`CHAR(32)`. The application only uses `create_all` for SQLite; there is no
migration history to manage for a throwaway local file.

### Testing against PostgreSQL

```bash
docker compose -f docker-compose.test.yml up -d
cd backend
set TEST_DATABASE_URL=postgresql+psycopg://gaia:gaia@127.0.0.1:55433/gaia_docs_test
python -m pytest -m postgres
docker compose -f docker-compose.test.yml down -v
```

Without `TEST_DATABASE_URL` the PostgreSQL-marked tests skip, so the default
suite still runs with no database server.

### Remaining database concerns

- **SQLite does not return timezone-aware datetimes.** SQLAlchemy's SQLite
  dialect stores `DateTime(timezone=True)` as a string and returns **naive**
  values, discarding the offset. PostgreSQL round-trips a true `timestamptz`.
  Code must not rely on a tz-aware value coming back on SQLite.
- **JSONB is not indexable in practice here.** The columns are JSONB, so an
  index is *possible*, but no GIN index is created â€” citations are read whole
  rather than queried by content. If a query ever needs to search inside
  `citations`, add `USING gin (citations)`.
- **CHECK constraints assume the data already conforms.** They were added
  knowing the application only ever writes the listed values. A pre-existing
  row outside those values would make the constraint fail to apply; the
  migration would need a repair step first.
- **No migration for the old `create_all` PostgreSQL types.** See the note
  above â€” deliberately not done, because it rewrites existing rows.

## Environment variables

All security variables are documented in `backend/.env.example`, split into
development and production guidance. `.env` is git-ignored; **never commit a
filled-in copy**.

| Variable | Default | Production requirement |
| --- | --- | --- |
| `APP_ENV` | `production` | Must be `production` or `development` |
| `AUTH_ENABLED` | `true` | Cannot be `false` |
| `AUTH_USERNAME` | *(none)* | Required |
| `AUTH_PASSWORD_HASH` | *(none)* | Required (or `AUTH_PASSWORD`) |
| `AUTH_PASSWORD` | *(none)* | Alternative to the hash |
| `AUTH_API_TOKEN` | *(none)* | Optional; empty disables bearer tokens |
| `SESSION_SECRET` | *(none)* | Required, min. 32 characters |
| `SESSION_MAX_AGE_SECONDS` | `43200` | â€” |
| `AUTH_COOKIE_NAME` | `gaia_session` | â€” |
| `AUTH_COOKIE_SECURE` | *(auto)* | `Secure` in production |
| `CORS_ORIGINS` | `[]` | Required, no `"*"` |
| `CORS_ALLOW_CREDENTIALS` | `true` | â€” |
| `ALLOWED_WORKSPACE_ROOTS` | `[]` | At least one root required |
| `ALLOW_UNRESTRICTED_WORKSPACE_ROOTS` | `false` | Cannot be `true` |

## Safety configuration

`ALLOWED_WORKSPACE_ROOTS` restricts which directories may be registered as
repositories. In production it is **required** and must not be empty:

```
ALLOWED_WORKSPACE_ROOTS=["C:/src/gaia-docs","C:/src/gaia"]
```

An empty list used to mean "any existing local path", which allowed the API to
read arbitrary directories on the host. It now means "no repository can be
registered" â€” and in production it is a startup error. Only in development, and
only when `ALLOW_UNRESTRICTED_WORKSPACE_ROOTS=true` alongside
`APP_ENV=development`, does an empty list regain its permissive meaning.

## Known security limitations

These are deliberate scope decisions for this phase, not oversights:

- **No rate limiting.** The login endpoint is brute-forceable in principle. For
  a self-hosted app on a private network that is a low risk; put the app behind
  a reverse proxy with rate limiting if it is internet-facing.
- **No multi-user support.** One account, by design. There is no per-user data
  isolation because there is only ever one user.
- **Session revocation is client-side.** Tokens are stateless, so `logout` clears
  the cookie but cannot invalidate a token that was already copied. The
  practical mitigations are the short expiry and TLS.
- **No CSRF token.** `SameSite=Strict` cookies are the mitigation. Revisit this
  if the app is ever deployed across multiple origins.
- **TLS is assumed to be handled by a reverse proxy.** The app itself serves
  plain HTTP; terminate TLS in front of it in any real deployment.

## Roadmap

- **Milestone 1 (done)** â€” workspaces, repositories, path sandbox, document
  tree/read/search, Git inspection.
- **Milestone 2 (done)** â€” approval-gated move/rename/edit/create proposals with
  readable diffs, untracked-file protection, no auto-commit.
- **Milestone 3 (done)** â€” LLM provider abstraction + architecture chat
  (explore / investigate / apply) with citable references and read-only tools.
- **Milestone 4 (done)** â€” AI document inventory: content-based classification,
  overlap and ambiguity detection, and an approval-gated organisation plan.
- **Milestone 5 (done)** â€” React three-panel UI: document explorer, architecture
  chat with citations, and the approval-gated inventory plan.
- **Later** â€” open questions, decision records, reconciliation, streaming.

## Notes and known limitations

- **Chat is synchronous.** A long tool loop holds the request open. Streaming
  would be the natural next improvement.
- **The inventory runs in one request** with no progress feedback, and
  documents are truncated at 12,000 characters when classifying.
- **The Markdown renderer is intentionally small** (headings, lists, tables,
  code, quotes). It renders into React elements rather than `innerHTML`, so
  document content cannot inject markup.
- **The frontend has no unit tests.** It is verified by a TypeScript build plus
  the Playwright smoke scripts. Component tests would be worth adding.
- **Registering a repository is only offered during first-run setup.** Adding a
  second documentation repository to an existing workspace has no UI, and the
  backend rejects it with 409 in any case: a workspace has at most one
  documentation repository.
