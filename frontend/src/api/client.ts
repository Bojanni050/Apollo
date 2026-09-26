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
export type SourceType = 'local' | 'github'
export type SourceStatus = 'pending' | 'ready' | 'error' | 'missing'
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
  // Source-repository metadata (absent for documentation repositories).
  source_type?: SourceType | null
  source_url?: string | null
  status?: SourceStatus | null
  status_message?: string | null
  last_synced_at?: string | null
}

export interface SourceCreatePayload {
  name: string
  location: string
  source_type?: SourceType | null
  branch?: string | null
  description?: string | null
}

export interface SourceSyncResult {
  repository: Repository
  action: 'cloned' | 'updated' | 'refreshed'
  status: SourceStatus
  message: string | null
  branch: string | null
  revision: string | null
}

export interface ManifestEntry {
  index: number
  repo: string
  path: string
  branch: string | null
  source_type: SourceType
  valid: boolean
  error: string | null
  action: 'add' | 'duplicate'
  existing_name: string | null
}

export interface ManifestPreview {
  total: number
  valid_count: number
  invalid_count: number
  new_count: number
  duplicate_count: number
  entries: ManifestEntry[]
}

export interface ManifestImportResult {
  imported: Repository[]
  duplicates: string[]
  invalid: { repo: string; path: string; error: string }[]
  manifest_path: string | null
}

export interface SourceFile {
  path: string
  size: number
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
  decision_id?: number | null
  question_id?: number | null
}

export interface ToolCall {
  tool: string
  arguments: Record<string, any>
  result_preview?: string
}

export interface Message {
  id: number
  role: 'user' | 'assistant' | 'system'
  content: string
  mode: Mode | null
  citations: Citation[] | null
  tool_calls?: ToolCall[] | null
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

export type QuestionStatus = 'open' | 'answered' | 'resolved'
export type DecisionStatus = 'proposed' | 'approved' | 'rejected' | 'superseded'

export interface OpenQuestion {
  id: number
  workspace_id: number
  uid: string
  title: string
  description: string
  evidence: unknown[] | null
  affected: string[] | null
  status: QuestionStatus
  source: string
  conversation_id: number | null
  resolution: string | null
  resolved_at: string | null
  addressed_by?: number[]
  created_at: string
  updated_at: string
}


export interface Decision {
  id: number
  workspace_id: number
  title: string
  context: string
  decision: string
  rationale: string
  consequences: string
  status: DecisionStatus
  decided_on: string | null
  approved_at: string | null
  markdown_path: string | null
  related_documents: string[] | null
  related_questions: (string | number)[] | null
  superseded_by_id?: number | null
  supersedes_ids?: number[]
  created_at: string
  updated_at: string
}

export interface GitStatusEntry {
  path: string
  status: string
}

export interface DecisionApproveResult {
  decision: Decision
  approved: boolean
  sync_status: 'created' | 'updated' | 'unchanged' | 'requires_review' | 'skipped'
  markdown_path?: string | null
  diff?: string | null
  git_status: GitStatusEntry[]
  message?: string | null
}

export interface DecisionSupersedeResult {
  decision: Decision
  superseded_by: Decision
  sync_status: 'created' | 'updated' | 'unchanged' | 'requires_review' | 'skipped'
  markdown_path?: string | null
  diff?: string | null
  git_status: GitStatusEntry[]
  message?: string | null
}

export interface ConsistencyFinding {
  type: 'conflict' | 'overlap' | 'compatible'
  decision_id: number
  title: string
  reason: string
  proposed_claim?: string
  existing_claim?: string
  markdown_path?: string | null
}

export interface ConsistencyCheckResult {
  status: 'No apparent conflict' | 'Potential conflict' | 'Potential overlap' | 'Insufficient evidence'
  summary: string
  candidates_evaluated: Array<{ id: number; title: string; status?: string; markdown_path?: string | null }>
  findings: ConsistencyFinding[]
  evidence: Array<{ decision_id: number; title: string; path?: string | null }>
}

export interface FolderItem {
  name: string
  path: string
  is_dir: boolean
}

export interface QuickAccessItem {
  name: string
  path: string
}

export interface FolderBrowseResult {
  current_path: string
  parent_path: string | null
  folders: FolderItem[]
  drives: string[]
  quick_access: QuickAccessItem[]
}

export interface NativePickResult {
  path?: string
  cancelled?: boolean
  error?: string
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
  // The backend takes a description as well as a name (WorkspaceCreate); it is
  // optional and stored verbatim, so an empty string is sent as null.
  createWorkspace: (name: string, description?: string | null) =>
    request<Workspace>('/workspaces', {
      method: 'POST',
      body: JSON.stringify({ name, description: description ?? null }),
    }),
  getWorkspace: (id: number) => request<Workspace>(`/workspaces/${id}`),

  // `writable` only has an effect for kind='documentation': the backend forces
  // source repositories to read-only, and rejects a second documentation
  // repository with 409. Those rules stay on the server.
  addRepository: (
    workspaceId: number,
    payload: {
      name: string
      local_path: string
      branch?: string
      kind: RepoKind
      writable?: boolean
      description?: string | null
    },
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

  // -- questions -----------------------------------------------------------
  listQuestions: (workspaceId: number, status?: QuestionStatus, conversationId?: number) => {
    const params = new URLSearchParams()
    if (status) params.set('status', status)
    if (conversationId) params.set('conversation_id', String(conversationId))
    const qs = params.toString() ? `?${params.toString()}` : ''
    return request<OpenQuestion[]>(`/workspaces/${workspaceId}/questions${qs}`)
  },
  createQuestion: (
    workspaceId: number,
    payload: {
      title: string
      description?: string
      status?: QuestionStatus
      source?: string
      conversation_id?: number | null
      evidence?: unknown[]
      affected?: unknown[]
    },
  ) =>
    request<OpenQuestion>(`/workspaces/${workspaceId}/questions`, {
      method: 'POST',
      body: JSON.stringify(payload),
    }),
  getQuestion: (workspaceId: number, questionId: number) =>
    request<OpenQuestion>(`/workspaces/${workspaceId}/questions/${questionId}`),
  updateQuestion: (
    workspaceId: number,
    questionId: number,
    payload: Partial<{
      title: string
      description: string
      status: QuestionStatus
      source: string
      resolution: string | null
      conversation_id: number | null
      evidence: unknown[]
      affected: unknown[]
      resolved_at: string | null
    }>,
  ) =>
    request<OpenQuestion>(`/workspaces/${workspaceId}/questions/${questionId}`, {
      method: 'PATCH',
      body: JSON.stringify(payload),
    }),
  deleteQuestion: (workspaceId: number, questionId: number) =>
    request<void>(`/workspaces/${workspaceId}/questions/${questionId}`, {
      method: 'DELETE',
    }),

  // -- decisions -----------------------------------------------------------
  listDecisions: (workspaceId: number, status?: DecisionStatus) => {
    const qs = status ? `?status=${encodeURIComponent(status)}` : ''
    return request<Decision[]>(`/workspaces/${workspaceId}/decisions${qs}`)
  },
  createDecision: (
    workspaceId: number,
    payload: {
      title: string
      context?: string
      decision?: string
      rationale?: string
      consequences?: string
      status?: DecisionStatus
      related_documents?: string[]
      related_questions?: (string | number)[]
      markdown_path?: string | null
    },
  ) =>
    request<Decision>(`/workspaces/${workspaceId}/decisions`, {
      method: 'POST',
      body: JSON.stringify(payload),
    }),
  getDecision: (workspaceId: number, decisionId: number) =>
    request<Decision>(`/workspaces/${workspaceId}/decisions/${decisionId}`),
  updateDecision: (
    workspaceId: number,
    decisionId: number,
    payload: Partial<{
      title: string
      context: string
      decision: string
      rationale: string
      consequences: string
      status: DecisionStatus
      markdown_path: string | null
      related_documents: string[]
      related_questions: (string | number)[]
    }>,
  ) =>
    request<Decision>(`/workspaces/${workspaceId}/decisions/${decisionId}`, {
      method: 'PATCH',
      body: JSON.stringify(payload),
    }),
  deleteDecision: (workspaceId: number, decisionId: number) =>
    request<void>(`/workspaces/${workspaceId}/decisions/${decisionId}`, {
      method: 'DELETE',
    }),
  approveDecision: (workspaceId: number, decisionId: number) =>
    request<DecisionApproveResult>(
      `/workspaces/${workspaceId}/decisions/${decisionId}/approve`,
      { method: 'POST' },
    ),
  linkQuestionDecision: (workspaceId: number, questionId: number, decisionId: number) =>
    request<Decision>(
      `/workspaces/${workspaceId}/questions/${questionId}/decisions/${decisionId}`,
      { method: 'POST' },
    ),
  unlinkQuestionDecision: (workspaceId: number, questionId: number, decisionId: number) =>
    request<Decision>(
      `/workspaces/${workspaceId}/questions/${questionId}/decisions/${decisionId}`,
      { method: 'DELETE' },
    ),
  getQuestionDecisions: (workspaceId: number, questionId: number) =>
    request<Decision[]>(`/workspaces/${workspaceId}/questions/${questionId}/decisions`),
  getDecisionQuestions: (workspaceId: number, decisionId: number) =>
    request<OpenQuestion[]>(`/workspaces/${workspaceId}/decisions/${decisionId}/questions`),
  checkDecisionConsistency: (workspaceId: number, decisionId: number) =>
    request<ConsistencyCheckResult>(
      `/workspaces/${workspaceId}/decisions/${decisionId}/consistency-check`,
      { method: 'POST' },
    ),
  checkProposalConsistency: (
    workspaceId: number,
    payload: {
      title: string
      decision?: string
      context?: string
      rationale?: string
      consequences?: string
      decision_id?: number
    },
  ) =>
    request<ConsistencyCheckResult>(
      `/workspaces/${workspaceId}/decisions/consistency-check`,
      {
        method: 'POST',
        body: JSON.stringify(payload),
      },
    ),
  supersedeDecision: (workspaceId: number, decisionId: number, supersededById: number) =>
    request<DecisionSupersedeResult>(
      `/workspaces/${workspaceId}/decisions/${decisionId}/supersede`,
      {
        method: 'POST',
        body: JSON.stringify({ superseded_by_id: supersededById }),
      },
    ),
  cancelDecisionSupersession: (workspaceId: number, decisionId: number) =>
    request<Decision>(
      `/workspaces/${workspaceId}/decisions/${decisionId}/supersede`,
      {
        method: 'DELETE',
      },
    ),
  getDecisionSupersededBy: (workspaceId: number, decisionId: number) =>
    request<Decision>(
      `/workspaces/${workspaceId}/decisions/${decisionId}/superseded-by`,
    ),
  getDecisionSupersedes: (workspaceId: number, decisionId: number) =>
    request<Decision[]>(
      `/workspaces/${workspaceId}/decisions/${decisionId}/supersedes`,
    ),
  browseFolders: (path?: string) => {
    const q = path ? `?path=${encodeURIComponent(path)}` : ''
    return request<FolderBrowseResult>(`/system/folders${q}`)
  },
  pickNativeFolder: () =>
    request<NativePickResult>('/system/pick-native-folder', { method: 'POST' }),

  // -- repository sources (architecture evidence) ------------------------
  listSources: (workspaceId: number) =>
    request<Repository[]>(`/workspaces/${workspaceId}/sources`),
  addSource: (workspaceId: number, payload: SourceCreatePayload) =>
    request<Repository>(`/workspaces/${workspaceId}/sources`, {
      method: 'POST',
      body: JSON.stringify(payload),
    }),
  removeSource: (workspaceId: number, repositoryId: number) =>
    request<void>(`/workspaces/${workspaceId}/sources/${repositoryId}`, {
      method: 'DELETE',
    }),
  syncSource: (workspaceId: number, repositoryId: number) =>
    request<SourceSyncResult>(
      `/workspaces/${workspaceId}/sources/${repositoryId}/sync`,
      { method: 'POST' },
    ),
  validateSourcesManifest: (workspaceId: number, content: string) =>
    request<ManifestPreview>(`/workspaces/${workspaceId}/sources/manifest/validate`, {
      method: 'POST',
      body: JSON.stringify({ content }),
    }),
  importSourcesManifest: (workspaceId: number, content: string, confirm: boolean) =>
    request<ManifestImportResult>(`/workspaces/${workspaceId}/sources/manifest/import`, {
      method: 'POST',
      body: JSON.stringify({ content, confirm }),
    }),
  getSourcesManifest: (workspaceId: number) =>
    request<ManifestPreview>(`/workspaces/${workspaceId}/sources/manifest`),
  listSourceFiles: (workspaceId: number, repositoryId: number, path = '.', limit = 200) =>
    request<{ repository_id: number; repository: string; path: string; files: SourceFile[] }>(
      `/workspaces/${workspaceId}/sources/${repositoryId}/files?path=${encodeURIComponent(path)}&limit=${limit}`,
    ),
  searchSourceCode: (workspaceId: number, repositoryId: number, q: string, limit = 20) =>
    request<{ repository_id: number; repository: string; query: string; hits: { path: string; line: number; snippet: string; score: number }[] }>(
      `/workspaces/${workspaceId}/sources/${repositoryId}/search?q=${encodeURIComponent(q)}&limit=${limit}`,
    ),
}


