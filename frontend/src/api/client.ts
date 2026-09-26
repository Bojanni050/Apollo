/**
 * Typed client for the Gaia Docs Architect API.
 *
 * The UI never constructs URLs inline; everything the backend exposes is
 * declared here once, so a contract change surfaces as a type error rather
 * than a runtime surprise.
 *
 * Authentication: the backend issues an HttpOnly session cookie from
 * /api/auth/login, so `credentials: 'include'` is what carries the session.
 * The token is never stored in JS, which keeps it unreadable by page scripts.
 */

const BASE = '/api'

export class ApiError extends Error {
  constructor(
    public status: number,
    public detail: string,
  ) {
    super(detail)
    this.name = 'ApiError'
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${BASE}${path}`, {
    // Send the session cookie; without this the browser drops it.
    credentials: 'include',
    headers: { 'Content-Type': 'application/json' },
    ...init,
  })
  if (!response.ok) {
    // FastAPI reports errors as { detail: string }.
    let detail = `Request failed (${response.status})`
    try {
      const body = await response.json()
      if (typeof body?.detail === 'string') detail = body.detail
    } catch {
      /* keep the default message */
    }
    throw new ApiError(response.status, detail)
  }
  if (response.status === 204) return undefined as T
  return (await response.json()) as T
}

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export type RepoKind = 'documentation' | 'source'
export type Mode = 'explore' | 'investigate' | 'apply'
export type EvidenceType =
  | 'verified_implementation'
  | 'explicit_decision'
  | 'documented_intention'
  | 'ai_interpretation'
  | 'uncertainty'

export interface Repository {
  id: number
  name: string
  local_path: string
  branch: string
  kind: RepoKind
  writable: boolean
  description: string | null
  is_git_repo: boolean
  current_branch: string | null
  head_revision: string | null
}

export interface Workspace {
  id: number
  name: string
  description: string | null
  created_at: string
  repositories: Repository[]
}

export interface DocNode {
  name: string
  path: string
  is_dir: boolean
  size: number | null
  children: DocNode[]
}

export interface DocumentTree {
  repository_id: number
  repository: string
  revision: string | null
  root: DocNode
}

export interface Document {
  repository_id: number
  repository: string
  path: string
  title: string
  raw_markdown: string
  revision: string | null
  size: number
}

export interface SearchHit {
  path: string
  score: number
  title: string
  snippet: string
  line: number | null
}

export interface Citation {
  repository: string
  path: string
  start_line: number | null
  end_line: number | null
  revision: string | null
  evidence_type: EvidenceType
  note: string | null
}

export interface Message {
  id: number
  role: 'user' | 'assistant' | 'system'
  content: string
  mode: Mode | null
  citations: Citation[] | null
  created_at: string
}

export interface Conversation {
  id: number
  workspace_id: number
  title: string
  mode: Mode
  question_id: number | null
  archived: boolean
  created_at: string
  updated_at: string
  message_count: number
}

export interface ConversationDetail extends Conversation {
  messages: Message[]
}



// ---------------------------------------------------------------------------
// Endpoints
// ---------------------------------------------------------------------------

export interface AuthStatus {
  auth_required: boolean
  authenticated: boolean
  username: string | null
}

export interface ChatStatus {
  llm_configured: boolean
  providers: string[]
  model: string | null
  base_url: string | null
}

export interface Proposal {
  id: number
  kind: string
  title: string
  reason: string
  evidence: unknown[] | null
  changes: { action: string; source_path: string | null; target_path: string }[] | null
  expected_consequences: string
  diff: string | null
  status: 'pending' | 'accepted' | 'rejected'
  created_at: string
  repository_id: number | null
}

export interface InventoryItem {
  id: number
  source_path: string
  purpose: string
  suggested_path: string | null
  confidence: number | null
  overlaps: string[] | null
  ambiguous: boolean
  note: string | null
  decision: 'pending' | 'applied' | 'skipped'
  target_path: string | null
  needs_move: boolean
  /** Other categories the model weighed, so a contested call is visible. */
  alternatives: string[]
  /** The model's stated evidence for the classification. */
  reason: string | null
  /** The classification rests on a structural digest, not the whole document. */
  partial: boolean
  /** The model reported low confidence; treat the suggestion as a suggestion. */
  low_confidence: boolean
}

export interface InventoryRun {
  id: number
  workspace_id: number
  status: string
  summary: string | null
  created_at: string
  items: InventoryItem[]
}

export interface GitStatus {
  repository_id: number
  repository: string
  branch: string | null
  revision: string | null
  entries: { path: string; status: string }[]
  diff: string
}

// ---------------------------------------------------------------------------
// Endpoints
// ---------------------------------------------------------------------------

export const api = {
  health: () => request<{ status: string; app: string; version: string }>('/health'),

  // -- authentication ------------------------------------------------------
  authStatus: () => request<AuthStatus>('/auth/status'),
  login: (username: string, password: string) =>
    request<{ authenticated: boolean; username: string | null }>('/auth/login', {
      method: 'POST',
      body: JSON.stringify({ username, password }),
    }),
  logout: () => request<void>('/auth/logout', { method: 'POST' }),

  listWorkspaces: () => request<Workspace[]>('/workspaces'),
  createWorkspace: (name: string) =>
    request<Workspace>('/workspaces', { method: 'POST', body: JSON.stringify({ name }) }),
  getWorkspace: (id: number) => request<Workspace>(`/workspaces/${id}`),

  addRepository: (
    workspaceId: number,
    payload: { name: string; local_path: string; branch?: string; kind: RepoKind },
  ) =>
    request<Repository>(`/workspaces/${workspaceId}/repositories`, {
      method: 'POST',
      body: JSON.stringify(payload),
    }),
  removeRepository: (workspaceId: number, repositoryId: number) =>
    request<void>(`/workspaces/${workspaceId}/repositories/${repositoryId}`, {
      method: 'DELETE',
    }),

  tree: (workspaceId: number, repositoryId: number, path = '.') =>
    request<DocumentTree>(
      `/workspaces/${workspaceId}/repositories/${repositoryId}/tree?path=${encodeURIComponent(path)}`,
    ),
  document: (workspaceId: number, repositoryId: number, path: string) =>
    request<Document>(
      `/workspaces/${workspaceId}/repositories/${repositoryId}/document?path=${encodeURIComponent(path)}`,
    ),
  search: (workspaceId: number, repositoryId: number, q: string) =>
    request<{ hits: SearchHit[] }>(
      `/workspaces/${workspaceId}/repositories/${repositoryId}/search?q=${encodeURIComponent(q)}`,
    ),
  git: (workspaceId: number, repositoryId: number) =>
    request<GitStatus>(`/workspaces/${workspaceId}/repositories/${repositoryId}/git`),

  chatStatus: (workspaceId: number) =>
    request<ChatStatus>(`/workspaces/${workspaceId}/chat/status`),

  listConversations: (workspaceId: number) =>
    request<Conversation[]>(`/workspaces/${workspaceId}/conversations`),
  createConversation: (workspaceId: number, mode: Mode = 'explore') =>
    request<Conversation>(`/workspaces/${workspaceId}/conversations`, {
      method: 'POST',
      body: JSON.stringify({ mode }),
    }),
  getConversation: (workspaceId: number, conversationId: number) =>
    request<ConversationDetail>(`/workspaces/${workspaceId}/conversations/${conversationId}`),
  setMode: (workspaceId: number, conversationId: number, mode: Mode) =>
    request<Conversation>(`/workspaces/${workspaceId}/conversations/${conversationId}`, {
      method: 'PATCH',
      body: JSON.stringify({ mode }),
    }),
  deleteConversation: (workspaceId: number, conversationId: number) =>
    request<void>(`/workspaces/${workspaceId}/conversations/${conversationId}`, {
      method: 'DELETE',
    }),
  sendMessage: (workspaceId: number, conversationId: number, content: string) =>
    request<{ user_message: Message; assistant_message: Message }>(
      `/workspaces/${workspaceId}/conversations/${conversationId}/messages`,
      { method: 'POST', body: JSON.stringify({ content }) },
    ),

  listProposals: (workspaceId: number) =>
    request<Proposal[]>(`/workspaces/${workspaceId}/proposals`),
  acceptProposal: (workspaceId: number, proposalId: number) =>
    request<{ proposal: Proposal; requires_manual_commit: boolean }>(
      `/workspaces/${workspaceId}/proposals/${proposalId}/accept`,
      { method: 'POST' },
    ),
  rejectProposal: (workspaceId: number, proposalId: number) =>
    request<Proposal>(`/workspaces/${workspaceId}/proposals/${proposalId}/reject`, {
      method: 'POST',
    }),

  createInventoryRun: (workspaceId: number) =>
    request<InventoryRun>(`/workspaces/${workspaceId}/inventory/runs`, {
      method: 'POST',
      body: JSON.stringify({}),
    }),
  listInventoryRuns: (workspaceId: number) =>
    request<InventoryRun[]>(`/workspaces/${workspaceId}/inventory/runs`),
  getInventoryRun: (workspaceId: number, runId: number) =>
    request<InventoryRun>(`/workspaces/${workspaceId}/inventory/runs/${runId}`),
  applyInventoryRun: (workspaceId: number, runId: number, itemIds: number[] = []) =>
    request<{ applied: string[]; skipped: { path: string; reason: string }[] }>(
      `/workspaces/${workspaceId}/inventory/runs/${runId}/apply`,
      { method: 'POST', body: JSON.stringify({ item_ids: itemIds }) },
    ),
  applyInventoryItem: (workspaceId: number, runId: number, itemId: number) =>
    request<{ item: InventoryItem; applied_path: string | null }>(
      `/workspaces/${workspaceId}/inventory/runs/${runId}/items/${itemId}/apply`,
      { method: 'POST' },
    ),
  skipInventoryItem: (workspaceId: number, runId: number, itemId: number) =>
    request<{ item: InventoryItem }>(
      `/workspaces/${workspaceId}/inventory/runs/${runId}/items/${itemId}/skip`,
      { method: 'POST' },
    ),
}

