/**
 * Typed client for the Apollo API.
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

export interface IndexCounts {
  files_discovered: number
  files_processed: number
  code_units_indexed: number
  documents_indexed: number
  document_chunks_indexed: number
  embeddings_generated: number
  failed_files: string[]
  errors: string[]
}

export interface IndexStatus {
  status: 'idle' | 'indexing' | 'completed' | 'failed'
  message: string | null
  started_at: string | null
  finished_at: string | null
  counts: IndexCounts
  code_embedding_model: string
  document_embedding_model: string
  reindex_required: boolean
  reindex_reasons: string[]
}

export interface EmbeddingModel {
  name: string
  label: string
  role: string | null
  dimension: number | null
  note: string | null
  runtime: string
  /** The name this runtime resolves the model by; null when it cannot serve it. */
  identifier: string | null
  downloadable: boolean
  installed: boolean
  in_use: boolean
  /** True for a vendor build, false for a community conversion. */
  official: boolean | null
  source: string | null
}

export interface LocalRuntime {
  id: string
  label: string
  available: boolean
  message: string | null
  address: string
  active: boolean
}

export interface EmbeddingModelsResult {
  runtime: string
  runtime_label: string
  runtime_available: boolean
  runtime_message: string | null
  runtime_address: string
  runtimes: LocalRuntime[]
  models: EmbeddingModel[]
}

export interface ModelPullStatus {
  model: string
  runtime: string
  status: 'idle' | 'starting' | 'downloading' | 'completed' | 'failed'
  message: string | null
  total_bytes: number | null
  completed_bytes: number | null
  percent: number | null
  started_at: string | null
  finished_at: string | null
  reindex_recommended: boolean
}

export interface SemanticSearchHit {
  kind: 'code' | 'document'
  repository_id: number
  repository: string
  file_path: string
  content: string
  score: number
  symbol: string | null
  node_type: string | null
  start_line: number | null
  end_line: number | null
  section: string | null
  source: string
}

export interface SemanticSearchResult {
  query: string
  mode: string
  repository_id: number | null
  lexical_hits: number
  semantic_hits: number
  hits: SemanticSearchHit[]
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

export interface DocumentLink {
  path: string
  text: string
}

export interface DocumentLinks {
  repository_id: number
  path: string
  /** Links this document makes, resolved to repository paths. */
  outbound: DocumentLink[]
  /** Documents that link here. Markdown only records the forward direction,
   *  so this is derived by scanning the corpus rather than read from the file. */
  inbound: DocumentLink[]
  /** Links that leave the repository. Shown, never fetched. */
  external: { target: string; text: string }[]
}

// --- Visual groups ---------------------------------------------------------
// A group is a *view* over documents. It does not correspond to a folder, and
// no operation here moves a file: that separation is what lets the reader
// rearrange freely while the folders on disk stay stable.

export type GroupLayout = 'grid' | 'list'

export interface Group {
  id: number
  name: string
  description: string | null
  /** 'ai' when Delphi proposed it, 'user' when the reader made it. */
  source: 'ai' | 'user'
  /** The archive. Kept, never deleted. */
  is_archive: boolean
  position: number
  layout: GroupLayout
  document_count: number
}

export interface GroupCreate {
  name: string
  description?: string
  source?: 'ai' | 'user'
  is_archive?: boolean
  layout?: GroupLayout
}

export interface GroupDocument {
  /**
   * Travels with the path because that pair is the document's identity: two
   * repositories can both hold `architecture.md`.
   */
  repository_id: number
  path: string
  position: number
  placed_by: string | null
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

export interface DatabaseResetResult {
  deleted_workspaces: number
}

export interface LlmSettings {
  base_url: string | null
  model: string | null
  context_tokens: number
  max_output_tokens: number
  temperature: number
  api_key_configured: boolean
  background_base_url?: string | null
  background_model?: string | null
  background_context_tokens?: number | null
  background_max_output_tokens?: number | null
  background_api_key_configured?: boolean
}

export interface LlmSettingsUpdatePayload {
  base_url?: string
  model?: string
  api_key?: string
  context_tokens?: number
  max_output_tokens?: number
  temperature?: number
  background_base_url?: string
  background_model?: string
  background_api_key?: string
  background_context_tokens?: number
  background_max_output_tokens?: number
}

export interface LlmModelInfo {
  id: string
  context_window?: number | null
  owned_by?: string | null
  input_price_per_m?: number | null
  output_price_per_m?: number | null
  capabilities: string[]
}

export interface LlmModelsResult {
  models: LlmModelInfo[]
  error?: string | null
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

export interface PulseConnection {
  path: string
  relation: 'relates-to' | 'supports' | 'contradicts' | 'extends' | string
  why: string
}

/**
 * Which halves of a Pulse suggestion to write. Tags and connections are
 * separable: the tags usually describe a document fairly, while the inferred
 * connections are the part worth reviewing on its own.
 */
export type PulseItemPart = 'tags' | 'connections'

export interface PulseItem {
  id: number
  file_path: string
  summary: string | null
  tags: string[]
  connections: PulseConnection[]
  confidence: number | null
  decision: 'pending' | 'applied' | 'skipped'
  /**
   * Which halves the reader has already accepted.
   *
   * Server-side, so a half-accepted item still shows the right choices after a
   * reload. Empty means nothing has been written yet.
   */
  applied_parts?: PulseItemPart[]
}

export interface PulseRun {
  id: number
  workspace_id: number
  status: string
  summary: string | null
  mode: 'suggest' | 'apply' | string
  created_at: string
  items: PulseItem[]
}

export interface PulseSettings {
  mode: 'suggest' | 'apply' | string
  schedule_enabled: boolean
  schedule_kind: 'interval' | 'weekly' | string
  interval_hours: number
  weekly_day: number
  weekly_hour: number
}

export interface PulseSettingsUpdatePayload {
  mode: 'suggest' | 'apply'
  schedule_enabled: boolean
  schedule_kind: 'interval' | 'weekly'
  interval_hours: number
  weekly_day: number
  weekly_hour: number
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
  deleteWorkspace: (id: number) =>
    request<void>(`/workspaces/${id}`, { method: 'DELETE' }),

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

  /**
   * Record untracked documents in Git, which is what the write guard requires
   * before it will touch a document. Omitting `paths` commits everything
   * untracked; passing them commits only those.
   */
  commitRepository: (
    workspaceId: number,
    payload: { message?: string; paths?: string[] } = {},
  ) =>
    request<{
      committed: string[]
      revision: string | null
      untracked_remaining: string[]
    }>(`/workspaces/${workspaceId}/repositories/commit`, {
      method: 'POST',
      body: JSON.stringify(payload),
    }),

  tree: (workspaceId: number, repositoryId: number, path = '.') =>
    request<DocumentTree>(
      `/workspaces/${workspaceId}/repositories/${repositoryId}/tree?path=${encodeURIComponent(path)}`,
    ),
  document: (workspaceId: number, repositoryId: number, path: string) =>
    request<Document>(
      `/workspaces/${workspaceId}/repositories/${repositoryId}/document?path=${encodeURIComponent(path)}`,
    ),
  documentLinks: (workspaceId: number, repositoryId: number, path: string) =>
    request<DocumentLinks>(
      `/workspaces/${workspaceId}/repositories/${repositoryId}/document/links?path=${encodeURIComponent(path)}`,
    ),
  search: (workspaceId: number, repositoryId: number, q: string) =>
    request<{ hits: SearchHit[] }>(
      `/workspaces/${workspaceId}/repositories/${repositoryId}/search?q=${encodeURIComponent(q)}`,
    ),
  git: (workspaceId: number, repositoryId: number) =>
    request<GitStatus>(`/workspaces/${workspaceId}/repositories/${repositoryId}/git`),

  // --- Visual groups ------------------------------------------------------
  // The arrangement, not the filesystem. Every call here writes a database row
  // and no file, which is what makes dragging safe to do freely.
  groups: (workspaceId: number) => request<Group[]>(`/workspaces/${workspaceId}/groups`),
  createGroup: (workspaceId: number, payload: GroupCreate) =>
    request<Group>(`/workspaces/${workspaceId}/groups`, {
      method: 'POST',
      body: JSON.stringify(payload),
    }),
  updateGroup: (
    workspaceId: number,
    groupId: number,
    payload: { name?: string; description?: string; layout?: GroupLayout },
  ) =>
    request<Group>(`/workspaces/${workspaceId}/groups/${groupId}`, {
      method: 'PATCH',
      body: JSON.stringify(payload),
    }),
  // Removes the group only. There is deliberately no method here that deletes a
  // document, because the application does not delete documents.
  deleteGroup: (workspaceId: number, groupId: number) =>
    request<void>(`/workspaces/${workspaceId}/groups/${groupId}`, { method: 'DELETE' }),
  groupDocuments: (workspaceId: number, groupId: number) =>
    request<GroupDocument[]>(`/workspaces/${workspaceId}/groups/${groupId}/documents`),
  addToGroup: (
    workspaceId: number,
    groupId: number,
    payload: { repository_id: number; path: string; placed_by?: string },
  ) =>
    request<GroupDocument>(`/workspaces/${workspaceId}/groups/${groupId}/documents`, {
      method: 'POST',
      body: JSON.stringify(payload),
    }),
  removeFromGroup: (
    workspaceId: number,
    groupId: number,
    repositoryId: number,
    path: string,
  ) =>
    request<void>(
      `/workspaces/${workspaceId}/groups/${groupId}/documents?repository_id=${repositoryId}&path=${encodeURIComponent(path)}`,
      { method: 'DELETE' },
    ),
  /**
   * Move a document between groups, in one request.
   *
   * One request rather than a remove followed by an add, so a failure cannot
   * leave the document in both groups or in neither. `from_group_id` is omitted
   * when the document is not currently in a group.
   */
  moveInGroup: (
    workspaceId: number,
    payload: {
      repository_id: number
      path: string
      to_group_id: number
      from_group_id?: number
    },
  ) =>
    request<void>(`/workspaces/${workspaceId}/groups/move`, {
      method: 'POST',
      body: JSON.stringify(payload),
    }),
  /** Which groups a document is in -- the sidebar's "where does this sit?". */
  groupsOfDocument: (workspaceId: number, repositoryId: number, path: string) =>
    request<Group[]>(
      `/workspaces/${workspaceId}/documents/groups?repository_id=${repositoryId}&path=${encodeURIComponent(path)}`,
    ),

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
  /**
   * `documentPath` names the file the reader had open, so the model resolves
   * "this document" to one file instead of guessing between several. Omitted
   * when nothing is open.
   */
  sendMessage: (
    workspaceId: number,
    conversationId: number,
    content: string,
    documentPath?: string | null,
  ) =>
    request<{ user_message: Message; assistant_message: Message }>(
      `/workspaces/${workspaceId}/conversations/${conversationId}/messages`,
      { method: 'POST', body: JSON.stringify({ content, document_path: documentPath ?? null }) },
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
  // -- Delphi Pulse -----------------------------------------------------------
  getPulseSettings: (workspaceId: number) =>
    request<PulseSettings>(`/workspaces/${workspaceId}/pulse/settings`),
  updatePulseSettings: (workspaceId: number, payload: PulseSettingsUpdatePayload) =>
    request<PulseSettings>(`/workspaces/${workspaceId}/pulse/settings`, {
      method: 'PUT',
      body: JSON.stringify(payload),
    }),
  createPulseRun: (workspaceId: number) =>
    request<PulseRun>(`/workspaces/${workspaceId}/pulse/runs`, {
      method: 'POST',
      body: JSON.stringify({}),
    }),
  listPulseRuns: (workspaceId: number) =>
    request<PulseRun[]>(`/workspaces/${workspaceId}/pulse/runs`),
  getPulseRun: (workspaceId: number, runId: number) =>
    request<PulseRun>(`/workspaces/${workspaceId}/pulse/runs/${runId}`),
  applyPulseRun: (workspaceId: number, runId: number, itemIds: number[] = []) =>
    request<{ applied: string[]; skipped: { path: string; reason: string }[] }>(
      `/workspaces/${workspaceId}/pulse/runs/${runId}/apply`,
      { method: 'POST', body: JSON.stringify({ item_ids: itemIds }) },
    ),
  applyPulseItem: (
    workspaceId: number,
    runId: number,
    itemId: number,
    parts?: PulseItemPart[],
  ) =>
    request<{ item: PulseItem; applied_path: string | null }>(
      `/workspaces/${workspaceId}/pulse/runs/${runId}/items/${itemId}/apply`,
      // Omitted entirely when no parts are given, so the backend keeps its
      // "both halves" default and the request shape stays as it was.
      parts && parts.length
        ? { method: 'POST', body: JSON.stringify({ parts }) }
        : { method: 'POST' },
    ),
  skipPulseItem: (workspaceId: number, runId: number, itemId: number) =>
    request<{ item: PulseItem }>(
      `/workspaces/${workspaceId}/pulse/runs/${runId}/items/${itemId}/skip`,
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
  resetDatabase: () =>
    request<DatabaseResetResult>('/system/database/reset', {
      method: 'POST',
      body: JSON.stringify({ confirm: 'RESET' }),
    }),
  getLlmSettings: () =>
    request<LlmSettings>('/system/settings/llm'),
  updateLlmSettings: (payload: LlmSettingsUpdatePayload) =>
    request<LlmSettings>('/system/settings/llm', {
      method: 'PUT',
      body: JSON.stringify(payload),
    }),
  fetchLlmModels: (baseUrl?: string, apiKey?: string) =>
    request<LlmModelsResult>('/system/settings/llm/models', {
      method: 'POST',
      body: JSON.stringify({ base_url: baseUrl || undefined, api_key: apiKey || undefined }),
    }),

  // -- embedding models (local runtimes) -----------------------------------
  getEmbeddingModels: (runtime?: string) =>
    request<EmbeddingModelsResult>(
      `/system/embedding-models${runtime ? `?runtime=${encodeURIComponent(runtime)}` : ''}`,
    ),
  pullEmbeddingModel: (model: string, runtime: string) =>
    request<ModelPullStatus>('/system/embedding-models/pull', {
      method: 'POST',
      body: JSON.stringify({ model, runtime }),
    }),
  getModelPullStatus: (model: string, runtime: string) =>
    request<ModelPullStatus>(
      `/system/embedding-models/pull?model=${encodeURIComponent(model)}&runtime=${encodeURIComponent(runtime)}`,
    ),

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

  // -- semantic indexing (embeddings + pgvector) ---------------------------
  getIndexStatus: (workspaceId: number) =>
    request<IndexStatus>(`/workspaces/${workspaceId}/sources/index`),
  triggerIndexing: (workspaceId: number, reindex = false) =>
    request<IndexStatus>(`/workspaces/${workspaceId}/sources/index`, {
      method: 'POST',
      body: JSON.stringify({ background: true, reindex }),
    }),
  reindexWorkspace: (workspaceId: number) =>
    request<IndexStatus>(`/workspaces/${workspaceId}/sources/reindex`, {
      method: 'POST',
      body: JSON.stringify({ background: true }),
    }),
  semanticSearch: (
    workspaceId: number,
    q: string,
    opts: { mode?: 'semantic' | 'hybrid' | 'lexical'; kind?: 'all' | 'code' | 'document'; repositoryId?: number; limit?: number } = {},
  ) => {
    const params = new URLSearchParams({ q, mode: opts.mode || 'hybrid', kind: opts.kind || 'all' })
    if (opts.repositoryId) params.set('repository_id', String(opts.repositoryId))
    if (opts.limit) params.set('limit', String(opts.limit))
    return request<SemanticSearchResult>(
      `/workspaces/${workspaceId}/sources/search?${params.toString()}`,
    )
  },
}


