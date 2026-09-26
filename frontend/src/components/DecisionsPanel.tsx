import { useEffect, useState } from 'react'
import {
  ApiError,
  api,
  type ConsistencyCheckResult,
  type Decision,
  type DecisionApproveResult,
  type DecisionStatus,
  type DecisionSupersedeResult,
  type OpenQuestion,
  type Repository,
  type Workspace,
} from '../api/client'
import { renderDiff, renderMarkdown } from '../markdown'

interface Props {
  workspace: Workspace
  repository: Repository | null
  decisions: Decision[]
  questions: OpenQuestion[]
  onRefreshDecisions: () => Promise<void>
  onRefreshQuestions?: () => Promise<void>
  onRefreshTree: () => Promise<void>
  onSelectDocument: (path: string) => void
  onOpenQuestion?: (questionId: number) => void
}

type SubTab = 'details' | 'adr' | 'diff' | 'status'

export function DecisionsPanel({
  workspace,
  repository,
  decisions,
  questions,
  onRefreshDecisions,
  onRefreshQuestions = async () => {},
  onRefreshTree,
  onSelectDocument,
  onOpenQuestion,
}: Props) {
  const [selectedId, setSelectedId] = useState<number | null>(null)

  const [filter, setFilter] = useState<'all' | DecisionStatus>('all')
  const [search, setSearch] = useState('')
  const [subTab, setSubTab] = useState<SubTab>('details')

  const [isCreating, setIsCreating] = useState(false)
  const [isEditing, setIsEditing] = useState(false)
  const [showApproveModal, setShowApproveModal] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  // Approval result
  const [approvalResult, setApprovalResult] = useState<DecisionApproveResult | null>(null)

  // Supersede modal states
  const [showSupersedeModal, setShowSupersedeModal] = useState(false)
  const [supersedeTargetId, setSupersedeTargetId] = useState<number | null>(null)
  const [supersedeResult, setSupersedeResult] = useState<DecisionSupersedeResult | null>(null)

  // Loaded ADR markdown for inspection
  const [adrMarkdown, setAdrMarkdown] = useState<string | null>(null)
  const [loadingAdr, setLoadingAdr] = useState(false)

  // Working tree git diff for the repository
  const [repoDiff, setRepoDiff] = useState<string | null>(null)
  const [loadingDiff, setLoadingDiff] = useState(false)

  // Form states
  const [title, setTitle] = useState('')
  const [status, setStatus] = useState<DecisionStatus>('proposed')
  const [context, setContext] = useState('')
  const [decisionText, setDecisionText] = useState('')
  const [rationale, setRationale] = useState('')
  const [consequences, setConsequences] = useState('')
  const [relatedQuestions, setRelatedQuestions] = useState('')
  const [relatedDocuments, setRelatedDocuments] = useState('')
  const [markdownPath, setMarkdownPath] = useState('')

  // Consistency check states
  const [consistencyResult, setConsistencyResult] = useState<ConsistencyCheckResult | null>(null)
  const [checkingConsistency, setCheckingConsistency] = useState(false)
  const [consistencyError, setConsistencyError] = useState<string | null>(null)
  const [linkQuestionId, setLinkQuestionId] = useState<string>('')

  const selectedDecision = decisions.find((d) => d.id === selectedId) ?? null

  const eligibleSupersedingDecisions = decisions.filter(
    (d) => d.id !== selectedDecision?.id && d.status === 'approved' && !d.superseded_by_id,
  )

  const linkedQuestionIds = new Set(
    (selectedDecision?.related_questions || []).map((r) => String(r)),
  )
  const availableQuestionsToLink = questions.filter(
    (q) => !linkedQuestionIds.has(String(q.id)) && !linkedQuestionIds.has(q.uid),
  )

  const handleCheckConsistency = async () => {
    if (!selectedDecision) return
    setCheckingConsistency(true)
    setConsistencyError(null)
    try {
      const res = await api.checkDecisionConsistency(workspace.id, selectedDecision.id)
      setConsistencyResult(res)
    } catch (e) {
      setConsistencyError(e instanceof ApiError ? e.message : 'Failed to perform consistency check.')
    } finally {
      setCheckingConsistency(false)
    }
  }

  const handleLinkQuestion = async () => {
    if (!selectedDecision || !linkQuestionId) return
    setBusy(true)
    setError(null)
    try {
      await api.linkQuestionDecision(workspace.id, Number(linkQuestionId), selectedDecision.id)
      await Promise.all([onRefreshDecisions(), onRefreshQuestions()])
      setLinkQuestionId('')
    } catch (e) {
      setError(e instanceof ApiError ? e.message : 'Failed to link question.')
    } finally {
      setBusy(false)
    }
  }

  const handleUnlinkQuestion = async (qRef: string | number) => {
    if (!selectedDecision) return
    const matched = questions.find((q) => String(q.id) === String(qRef) || q.uid === String(qRef))
    const qId = matched ? matched.id : Number(qRef)
    if (isNaN(qId)) return
    setBusy(true)
    setError(null)
    try {
      await api.unlinkQuestionDecision(workspace.id, qId, selectedDecision.id)
      await Promise.all([onRefreshDecisions(), onRefreshQuestions()])
    } catch (e) {
      setError(e instanceof ApiError ? e.message : 'Failed to unlink question.')
    } finally {
      setBusy(false)
    }
  }


  const filteredDecisions = decisions.filter((d) => {
    if (filter !== 'all' && d.status !== filter) return false
    if (search.trim()) {
      const s = search.toLowerCase()
      return (
        d.title.toLowerCase().includes(s) ||
        d.context.toLowerCase().includes(s) ||
        d.decision.toLowerCase().includes(s) ||
        d.rationale.toLowerCase().includes(s) ||
        (d.markdown_path && d.markdown_path.toLowerCase().includes(s))
      )
    }
    return true
  })

  // Load ADR document when subTab is 'adr' or when selected decision changes
  useEffect(() => {
    if (!selectedDecision?.markdown_path || !repository) {
      setAdrMarkdown(null)
      return
    }
    let cancelled = false
    setLoadingAdr(true)
    api
      .document(workspace.id, repository.id, selectedDecision.markdown_path)
      .then((doc) => {
        if (!cancelled) setAdrMarkdown(doc.raw_markdown)
      })
      .catch(() => {
        if (!cancelled) setAdrMarkdown(null)
      })
      .finally(() => {
        if (!cancelled) setLoadingAdr(false)
      })
    return () => {
      cancelled = true
    }
  }, [workspace.id, repository, selectedDecision?.markdown_path, approvalResult])

  // Load working tree git diff when subTab is 'diff'
  useEffect(() => {
    if (subTab !== 'diff' || !repository) return
    let cancelled = false
    setLoadingDiff(true)
    api
      .git(workspace.id, repository.id)
      .then((gitStatus) => {
        if (!cancelled) setRepoDiff(gitStatus.diff)
      })
      .catch(() => {
        if (!cancelled) setRepoDiff(null)
      })
      .finally(() => {
        if (!cancelled) setLoadingDiff(false)
      })
    return () => {
      cancelled = true
    }
  }, [workspace.id, repository, subTab, approvalResult])

  const resetForm = () => {
    setTitle('')
    setStatus('proposed')
    setContext('')
    setDecisionText('')
    setRationale('')
    setConsequences('')
    setRelatedQuestions('')
    setRelatedDocuments('')
    setMarkdownPath('')
    setError(null)
  }

  const startCreate = () => {
    resetForm()
    setIsEditing(false)
    setIsCreating(true)
    setApprovalResult(null)
  }

  const startEdit = (d: Decision) => {
    setTitle(d.title)
    setStatus(d.status)
    setContext(d.context || '')
    setDecisionText(d.decision || '')
    setRationale(d.rationale || '')
    setConsequences(d.consequences || '')
    setRelatedQuestions(d.related_questions ? d.related_questions.join(', ') : '')
    setRelatedDocuments(d.related_documents ? d.related_documents.join(', ') : '')
    setMarkdownPath(d.markdown_path || '')
    setError(null)
    setIsCreating(false)
    setIsEditing(true)
  }

  const handleCreate = async (e: React.FormEvent) => {
    e.preventDefault()
    if (!title.trim()) {
      setError('Title cannot be empty.')
      return
    }
    setBusy(true)
    setError(null)
    try {
      const qList = relatedQuestions
        .split(',')
        .map((s) => s.trim())
        .filter(Boolean)
      const docList = relatedDocuments
        .split(',')
        .map((s) => s.trim())
        .filter(Boolean)
      const created = await api.createDecision(workspace.id, {
        title: title.trim(),
        status,
        context: context.trim(),
        decision: decisionText.trim(),
        rationale: rationale.trim(),
        consequences: consequences.trim(),
        related_questions: qList,
        related_documents: docList,
        markdown_path: markdownPath.trim() || null,
      })
      await onRefreshDecisions()
      setIsCreating(false)
      setSelectedId(created.id)
      setSubTab('details')
      resetForm()
    } catch (err) {
      setError(err instanceof ApiError ? err.detail : 'Failed to create decision.')
    } finally {
      setBusy(false)
    }
  }

  const handleUpdate = async (e: React.FormEvent) => {
    e.preventDefault()
    if (!selectedDecision) return
    if (!title.trim()) {
      setError('Title cannot be empty.')
      return
    }
    setBusy(true)
    setError(null)
    try {
      const qList = relatedQuestions
        .split(',')
        .map((s) => s.trim())
        .filter(Boolean)
      const docList = relatedDocuments
        .split(',')
        .map((s) => s.trim())
        .filter(Boolean)
      await api.updateDecision(workspace.id, selectedDecision.id, {
        title: title.trim(),
        status,
        context: context.trim(),
        decision: decisionText.trim(),
        rationale: rationale.trim(),
        consequences: consequences.trim(),
        related_questions: qList,
        related_documents: docList,
        markdown_path: markdownPath.trim() || null,
      })
      await onRefreshDecisions()
      setIsEditing(false)
    } catch (err) {
      setError(err instanceof ApiError ? err.detail : 'Failed to update decision.')
    } finally {
      setBusy(false)
    }
  }

  const handleDelete = async (d: Decision) => {
    if (!window.confirm(`Delete decision "${d.title}"?`)) return
    setBusy(true)
    setError(null)
    try {
      await api.deleteDecision(workspace.id, d.id)
      await onRefreshDecisions()
      if (selectedId === d.id) {
        setSelectedId(null)
        setIsEditing(false)
        setApprovalResult(null)
      }
    } catch (err) {
      setError(err instanceof ApiError ? err.detail : 'Failed to delete decision.')
    } finally {
      setBusy(false)
    }
  }

  const handleApprove = async () => {
    if (!selectedDecision) return
    setBusy(true)
    setError(null)
    try {
      const res = await api.approveDecision(workspace.id, selectedDecision.id)
      setApprovalResult(res)
      setShowApproveModal(false)
      await onRefreshDecisions()
      await onRefreshTree()
      // Switch to ADR preview or diff tab so operator can inspect the result
      setSubTab(res.diff ? 'diff' : 'adr')
    } catch (err) {
      setShowApproveModal(false)
      setError(err instanceof ApiError ? err.detail : 'Failed to approve decision.')
    } finally {
      setBusy(false)
    }
  }

  const handleSupersede = async () => {
    if (!selectedDecision || !supersedeTargetId) return
    setBusy(true)
    setError(null)
    try {
      const res = await api.supersedeDecision(workspace.id, selectedDecision.id, supersedeTargetId)
      setSupersedeResult(res)
      setShowSupersedeModal(false)
      await onRefreshDecisions()
      await onRefreshTree()
      if (res.diff) {
        setSubTab('diff')
      }
    } catch (err) {
      setShowSupersedeModal(false)
      setError(err instanceof ApiError ? err.detail : 'Failed to supersede decision.')
    } finally {
      setBusy(false)
    }
  }

  const handleCancelSupersede = async (decisionId: number) => {
    if (!window.confirm('Restore this decision back to approved status and remove the supersession notice?')) return
    setBusy(true)
    setError(null)
    try {
      await api.cancelDecisionSupersession(workspace.id, decisionId)
      await onRefreshDecisions()
      await onRefreshTree()
    } catch (err) {
      setError(err instanceof ApiError ? err.detail : 'Failed to cancel supersession.')
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="split-layout">
      {/* Sidebar: list of decisions */}
      <div className="split-sidebar">
        <div className="panel-header" style={{ justifyContent: 'space-between' }}>
          <span>Decisions ({decisions.length})</span>
          <button className="btn primary" onClick={startCreate} disabled={busy}>
            + New
          </button>
        </div>

        <div style={{ padding: '8px 10px', borderBottom: '1px solid var(--border)' }}>
          <input
            type="search"
            placeholder="Search decisions..."
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            style={{ fontSize: 12, padding: '4px 8px' }}
          />
        </div>

        <div className="subtabs" style={{ padding: '0 8px' }}>
          {(['all', 'proposed', 'approved', 'rejected', 'superseded'] as const).map((tab) => (
            <button
              key={tab}
              className={`subtab-btn ${filter === tab ? 'active' : ''}`}
              onClick={() => setFilter(tab)}
              style={{ fontSize: 11, padding: '6px 8px', textTransform: 'capitalize' }}
            >
              {tab}
            </button>
          ))}
        </div>

        <div className="panel-body tight" style={{ overflowY: 'auto' }}>
          {filteredDecisions.length === 0 ? (
            <div className="empty">
              {decisions.length === 0 ? 'No decisions recorded yet.' : 'No decisions match the filter.'}
            </div>
          ) : (
            filteredDecisions.map((d) => {
              const isSelected = d.id === selectedId
              return (
                <div
                  key={d.id}
                  className={`list-item ${isSelected ? 'active' : ''}`}
                  onClick={() => {
                    setSelectedId(d.id)
                    setIsCreating(false)
                    setIsEditing(false)
                    setError(null)
                    setApprovalResult(null)
                  }}
                  style={{ borderBottom: '1px solid var(--border)', borderRadius: 0, padding: '10px 12px' }}
                >
                  <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginBottom: 4 }}>
                    <span
                      className={`tag ${
                        d.status === 'approved'
                          ? 'ok'
                          : d.status === 'proposed'
                          ? 'warn'
                          : d.status === 'rejected'
                          ? 'danger'
                          : 'dim'
                      }`}
                    >
                      {d.status}
                    </span>
                    <span className="mono faint" style={{ fontSize: 10 }}>
                      #{d.id}
                    </span>
                    {d.superseded_by_id ? (
                      <span className="tag dim mono" style={{ fontSize: 9 }} title={`Superseded by #${d.superseded_by_id}`}>
                        ↳ #{d.superseded_by_id}
                      </span>
                    ) : null}
                    <span className="spacer" style={{ flex: 1 }} />
                    {d.markdown_path ? (
                      <span className="tag accent" style={{ fontSize: 9 }}>
                        ADR Synced
                      </span>
                    ) : (
                      <span className="faint" style={{ fontSize: 10 }}>
                        No ADR
                      </span>
                    )}
                  </div>
                  <div className="list-title" style={{ whiteSpace: 'normal', fontWeight: 500, lineHeight: 1.4 }}>
                    {d.title}
                  </div>
                  {d.markdown_path && (
                    <div className="mono faint" style={{ fontSize: 10, marginTop: 4, overflow: 'hidden', textOverflow: 'ellipsis' }}>
                      {d.markdown_path}
                    </div>
                  )}
                </div>
              )
            })
          )}
        </div>
      </div>

      {/* Main content: Inspector / Form */}
      <div className="split-content">
        {error && <div className="banner">{error}</div>}

        {/* Approval Result Banner */}
        {approvalResult && (
          <div
            style={{
              padding: '10px 14px',
              background: 'rgba(143, 191, 127, 0.12)',
              borderBottom: '1px solid rgba(143, 191, 127, 0.35)',
              color: 'var(--text)',
              fontSize: 12,
            }}
          >
            <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
              <span className="tag ok">
                ADR {approvalResult.sync_status.toUpperCase()}
              </span>
              <span style={{ fontWeight: 500 }}>
                {approvalResult.message || 'Decision approved and ADR synchronized.'}
              </span>
              <span className="spacer" style={{ flex: 1 }} />
              {approvalResult.markdown_path && (
                <button
                  className="btn"
                  onClick={() => onSelectDocument(approvalResult.markdown_path!)}
                  title="Open in Workspace Document Viewer"
                >
                  View in Document Tab
                </button>
              )}
            </div>
            {approvalResult.markdown_path && (
              <div className="mono faint" style={{ marginTop: 4 }}>
                Target: {approvalResult.markdown_path}
              </div>
            )}
          </div>
        )}

        {isCreating ? (
          <div className="section" style={{ maxWidth: 840 }}>
            <div className="btn-row" style={{ marginBottom: 12, alignItems: 'center' }}>
              <div className="badge-distinction">
                <span>⚖️ Architectural Decision</span>
                <span className="faint">· Database Record</span>
              </div>
              <span className="spacer" style={{ flex: 1 }} />
              <button className="btn" onClick={() => setIsCreating(false)} disabled={busy}>
                Cancel
              </button>
            </div>

            <h2 style={{ fontSize: 16, margin: '0 0 16px' }}>New Architectural Decision</h2>

            <form onSubmit={handleCreate}>
              <div className="form-group">
                <label className="form-label">Decision Title *</label>
                <input
                  type="text"
                  placeholder="e.g. Use PostgreSQL for metadata storage"
                  value={title}
                  onChange={(e) => setTitle(e.target.value)}
                  disabled={busy}
                  required
                />
              </div>

              <div className="form-group">
                <label className="form-label">Status</label>
                <select
                  value={status}
                  onChange={(e) => setStatus(e.target.value as DecisionStatus)}
                  disabled={busy}
                >
                  <option value="proposed">Proposed</option>
                  <option value="approved">Approved</option>
                  <option value="rejected">Rejected</option>
                  <option value="superseded">Superseded</option>
                </select>
              </div>

              <div className="form-group">
                <label className="form-label">Context / Background</label>
                <textarea
                  rows={4}
                  placeholder="The context and problem statement driving this decision..."
                  value={context}
                  onChange={(e) => setContext(e.target.value)}
                  disabled={busy}
                />
              </div>

              <div className="form-group">
                <label className="form-label">Decision</label>
                <textarea
                  rows={4}
                  placeholder="The chosen architectural solution or policy..."
                  value={decisionText}
                  onChange={(e) => setDecisionText(e.target.value)}
                  disabled={busy}
                />
              </div>

              <div className="form-group">
                <label className="form-label">Rationale</label>
                <textarea
                  rows={3}
                  placeholder="Why this option was chosen over alternatives..."
                  value={rationale}
                  onChange={(e) => setRationale(e.target.value)}
                  disabled={busy}
                />
              </div>

              <div className="form-group">
                <label className="form-label">Consequences</label>
                <textarea
                  rows={3}
                  placeholder="What becomes easier or harder as a result..."
                  value={consequences}
                  onChange={(e) => setConsequences(e.target.value)}
                  disabled={busy}
                />
              </div>

              <div className="form-group">
                <label className="form-label">Related Questions (comma-separated question IDs or UUIDs)</label>
                <input
                  type="text"
                  placeholder="e.g. 1, 2"
                  value={relatedQuestions}
                  onChange={(e) => setRelatedQuestions(e.target.value)}
                  disabled={busy}
                />
              </div>

              <div className="form-group">
                <label className="form-label">Related Documents (comma-separated relative paths)</label>
                <input
                  type="text"
                  placeholder="e.g. architecture/overview.md"
                  value={relatedDocuments}
                  onChange={(e) => setRelatedDocuments(e.target.value)}
                  disabled={busy}
                />
              </div>

              <div className="form-group">
                <label className="form-label">Custom ADR Markdown Path (optional)</label>
                <input
                  type="text"
                  placeholder="Leave empty to use default (architecture/decisions/adr-XXX.md)"
                  value={markdownPath}
                  onChange={(e) => setMarkdownPath(e.target.value)}
                  disabled={busy}
                />
              </div>

              <div className="btn-row" style={{ marginTop: 16 }}>
                <button type="submit" className="btn primary" disabled={busy || !title.trim()}>
                  {busy ? 'Saving...' : 'Create Decision'}
                </button>
                <button type="button" className="btn" onClick={() => setIsCreating(false)} disabled={busy}>
                  Cancel
                </button>
              </div>
            </form>
          </div>
        ) : isEditing && selectedDecision ? (
          <div className="section" style={{ maxWidth: 840 }}>
            <div className="btn-row" style={{ marginBottom: 12, alignItems: 'center' }}>
              <div className="badge-distinction">
                <span>⚖️ Edit Decision #{selectedDecision.id}</span>
              </div>
              <span className="spacer" style={{ flex: 1 }} />
              <button className="btn" onClick={() => setIsEditing(false)} disabled={busy}>
                Cancel
              </button>
            </div>

            <form onSubmit={handleUpdate}>
              <div className="form-group">
                <label className="form-label">Decision Title *</label>
                <input
                  type="text"
                  value={title}
                  onChange={(e) => setTitle(e.target.value)}
                  disabled={busy}
                  required
                />
              </div>

              <div className="form-group">
                <label className="form-label">Status</label>
                <select
                  value={status}
                  onChange={(e) => setStatus(e.target.value as DecisionStatus)}
                  disabled={busy}
                >
                  <option value="proposed">Proposed</option>
                  <option value="approved">Approved</option>
                  <option value="rejected">Rejected</option>
                  <option value="superseded">Superseded</option>
                </select>
              </div>

              <div className="form-group">
                <label className="form-label">Context / Background</label>
                <textarea
                  rows={4}
                  value={context}
                  onChange={(e) => setContext(e.target.value)}
                  disabled={busy}
                />
              </div>

              <div className="form-group">
                <label className="form-label">Decision</label>
                <textarea
                  rows={4}
                  value={decisionText}
                  onChange={(e) => setDecisionText(e.target.value)}
                  disabled={busy}
                />
              </div>

              <div className="form-group">
                <label className="form-label">Rationale</label>
                <textarea
                  rows={3}
                  value={rationale}
                  onChange={(e) => setRationale(e.target.value)}
                  disabled={busy}
                />
              </div>

              <div className="form-group">
                <label className="form-label">Consequences</label>
                <textarea
                  rows={3}
                  value={consequences}
                  onChange={(e) => setConsequences(e.target.value)}
                  disabled={busy}
                />
              </div>

              <div className="form-group">
                <label className="form-label">Related Questions (comma-separated question IDs)</label>
                <input
                  type="text"
                  value={relatedQuestions}
                  onChange={(e) => setRelatedQuestions(e.target.value)}
                  disabled={busy}
                />
              </div>

              <div className="form-group">
                <label className="form-label">Related Documents (comma-separated paths)</label>
                <input
                  type="text"
                  value={relatedDocuments}
                  onChange={(e) => setRelatedDocuments(e.target.value)}
                  disabled={busy}
                />
              </div>

              <div className="form-group">
                <label className="form-label">ADR Markdown Path</label>
                <input
                  type="text"
                  value={markdownPath}
                  onChange={(e) => setMarkdownPath(e.target.value)}
                  disabled={busy}
                />
              </div>

              <div className="btn-row" style={{ marginTop: 16 }}>
                <button type="submit" className="btn primary" disabled={busy || !title.trim()}>
                  {busy ? 'Saving...' : 'Save Changes'}
                </button>
                <button type="button" className="btn" onClick={() => setIsEditing(false)} disabled={busy}>
                  Cancel
                </button>
              </div>
            </form>
          </div>
        ) : selectedDecision ? (
          <div>
            {/* Top Action Bar */}
            <div className="section" style={{ borderBottom: '1px solid var(--border)' }}>
              <div className="btn-row" style={{ alignItems: 'center', marginBottom: 8 }}>
                <div className="badge-distinction">
                  <span>⚖️ Architectural Decision</span>
                  <span className="faint">· DB Source of Truth</span>
                </div>
                <span
                  className={`tag ${
                    selectedDecision.status === 'approved'
                      ? 'ok'
                      : selectedDecision.status === 'proposed'
                      ? 'warn'
                      : selectedDecision.status === 'rejected'
                      ? 'danger'
                      : 'dim'
                  }`}
                  style={{ fontSize: 11 }}
                >
                  {selectedDecision.status}
                </span>

                <span className="spacer" style={{ flex: 1 }} />

                {/* Consistency Check Button */}
                <button
                  className="btn"
                  onClick={handleCheckConsistency}
                  disabled={checkingConsistency || busy}
                  title="Check this proposed decision against existing approved decisions and ADRs"
                  style={{ display: 'inline-flex', alignItems: 'center', gap: 6 }}
                >
                  {checkingConsistency ? '🔍 Checking...' : '🔍 Check Consistency'}
                </button>

                {/* Explicit Approve Button */}
                <button
                  className={`btn ${selectedDecision.status === 'approved' ? '' : 'primary'}`}
                  onClick={() => setShowApproveModal(true)}
                  disabled={busy}
                  style={{ fontWeight: 600 }}
                  title="Explicitly approve this decision and synchronize its ADR Markdown file"
                >
                  {selectedDecision.status === 'approved' ? '✓ Re-sync ADR Documentation' : '✓ Approve Decision'}
                </button>

                {/* Explicit Supersede Button (available for approved decisions that are not yet superseded) */}
                {selectedDecision.status === 'approved' && !selectedDecision.superseded_by_id && (
                  <button
                    className="btn"
                    onClick={() => {
                      setSupersedeTargetId(eligibleSupersedingDecisions[0]?.id ?? null)
                      setShowSupersedeModal(true)
                    }}
                    disabled={busy || eligibleSupersedingDecisions.length === 0}
                    title={
                      eligibleSupersedingDecisions.length === 0
                        ? 'No other approved decisions available to supersede this one'
                        : 'Mark this decision as superseded by a newer approved decision'
                    }
                    style={{ fontWeight: 600, color: 'var(--accent)' }}
                  >
                    ⚡ Supersede Decision
                  </button>
                )}

                <button className="btn" onClick={() => startEdit(selectedDecision)} disabled={busy}>
                  Edit
                </button>
                <button className="btn danger" onClick={() => handleDelete(selectedDecision)} disabled={busy}>
                  Delete
                </button>
              </div>

              <h1 style={{ fontSize: 20, margin: '8px 0 6px', lineHeight: 1.3 }}>
                {selectedDecision.title}
              </h1>

              <div className="btn-row" style={{ alignItems: 'center' }}>
                <span className="mono faint" style={{ fontSize: 11 }}>
                  ID: #{selectedDecision.id}
                </span>
                <span className="faint">·</span>
                {selectedDecision.decided_on && (
                  <>
                    <span className="faint" style={{ fontSize: 11 }}>
                      Decided: {new Date(selectedDecision.decided_on).toLocaleDateString()}
                    </span>
                    <span className="faint">·</span>
                  </>
                )}
                {selectedDecision.approved_at ? (
                  <span className="tag ok" style={{ fontSize: 10 }}>
                    Approved: {new Date(selectedDecision.approved_at).toLocaleString()}
                  </span>
                ) : (
                  <span className="tag warn" style={{ fontSize: 10 }}>
                    Not Yet Approved
                  </span>
                )}
                {selectedDecision.markdown_path ? (
                  <>
                    <span className="faint">·</span>
                    <span className="mono accent" style={{ fontSize: 11 }}>
                      📄 {selectedDecision.markdown_path}
                    </span>
                    <button
                      className="btn"
                      style={{ fontSize: 11, padding: '1px 6px' }}
                      onClick={() => onSelectDocument(selectedDecision.markdown_path!)}
                      title="Open in Document Viewer"
                    >
                      Open in Doc Tab
                    </button>
                  </>
                ) : null}
              </div>
            </div>

            {/* Navigable Lineage Banner: If this decision was superseded */}
            {selectedDecision.superseded_by_id && (
              <div
                className="section"
                style={{
                  background: 'rgba(235, 87, 87, 0.08)',
                  border: '1px solid var(--danger, #eb5757)',
                  borderRadius: 'var(--radius)',
                  padding: '12px 16px',
                  margin: '12px 0',
                }}
              >
                <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
                  <span style={{ fontSize: 18 }}>⚠️</span>
                  <div style={{ flex: 1 }}>
                    <div style={{ fontWeight: 600, fontSize: 13, color: 'var(--danger, #eb5757)' }}>
                      Decision #{selectedDecision.id} is historically Superseded
                    </div>
                    <div style={{ fontSize: 12, color: 'var(--text)', marginTop: 3 }}>
                      Superseded by:{' '}
                      <button
                        type="button"
                        style={{
                          cursor: 'pointer',
                          background: 'none',
                          border: 'none',
                          color: 'var(--accent)',
                          fontWeight: 600,
                          textDecoration: 'underline',
                          fontSize: 12,
                          padding: 0,
                        }}
                        onClick={() => setSelectedId(selectedDecision.superseded_by_id!)}
                      >
                        Decision #{selectedDecision.superseded_by_id} — {decisions.find(d => d.id === selectedDecision.superseded_by_id)?.title || 'Newer Decision'}
                      </button>
                    </div>
                  </div>
                  <button
                    className="btn small"
                    onClick={() => handleCancelSupersede(selectedDecision.id)}
                    disabled={busy}
                    title="Revert supersession back to approved status"
                  >
                    Cancel Supersession
                  </button>
                </div>
              </div>
            )}

            {/* Navigable Lineage Banner: If this decision supersedes older decisions */}
            {decisions.some(d => d.superseded_by_id === selectedDecision.id) && (
              <div
                className="section"
                style={{
                  background: 'rgba(152, 195, 121, 0.08)',
                  border: '1px solid var(--ok, #98c379)',
                  borderRadius: 'var(--radius)',
                  padding: '10px 14px',
                  margin: '12px 0',
                }}
              >
                <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
                  <span style={{ fontSize: 14 }}>🔄</span>
                  <span style={{ fontWeight: 600, fontSize: 12, color: 'var(--ok, #98c379)' }}>
                    Supersedes Historical Decision(s):
                  </span>
                  {decisions
                    .filter(d => d.superseded_by_id === selectedDecision.id)
                    .map(old => (
                      <button
                        key={old.id}
                        type="button"
                        className="tag"
                        style={{
                          cursor: 'pointer',
                          background: 'var(--bg)',
                          border: '1px solid var(--border)',
                          fontSize: 11,
                          padding: '2px 8px',
                        }}
                        onClick={() => setSelectedId(old.id)}
                        title="Inspect historical superseded decision"
                      >
                        #{old.id} {old.title}
                      </button>
                    ))}
                </div>
              </div>
            )}

            {/* Consistency Check Result Card */}
            {checkingConsistency && (
              <div className="section" style={{ background: 'var(--panel-2)', borderRadius: 'var(--radius)', padding: 14, margin: '14px 0', border: '1px solid var(--border)' }}>
                <span className="faint" style={{ fontSize: 12 }}>
                  Analyzing proposed decision against approved architecture and ADRs...
                </span>
              </div>
            )}

            {consistencyError && (
              <div className="error-banner" style={{ margin: '14px 0' }}>
                <span>{consistencyError}</span>
                <button className="btn small" onClick={() => setConsistencyError(null)} style={{ marginLeft: 'auto' }}>
                  Dismiss
                </button>
              </div>
            )}

            {consistencyResult && (
              <div
                className="section consistency-report-card"
                style={{
                  background: 'var(--panel-2)',
                  borderRadius: 'var(--radius)',
                  padding: 16,
                  margin: '14px 0',
                  border: `1px solid ${
                    consistencyResult.status === 'Potential conflict'
                      ? 'var(--danger, #e06c75)'
                      : consistencyResult.status === 'Potential overlap'
                      ? 'var(--accent, #61afef)'
                      : consistencyResult.status === 'No apparent conflict'
                      ? 'var(--ok, #98c379)'
                      : 'var(--border)'
                  }`,
                }}
              >
                <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 8 }}>
                  <span style={{ fontSize: 13, fontWeight: 600, textTransform: 'uppercase', letterSpacing: '0.05em' }}>
                    Architectural Consistency Check
                  </span>
                  <span
                    className={`tag ${
                      consistencyResult.status === 'Potential conflict'
                        ? 'danger'
                        : consistencyResult.status === 'Potential overlap'
                        ? 'accent'
                        : consistencyResult.status === 'No apparent conflict'
                        ? 'ok'
                        : 'dim'
                    }`}
                    style={{ fontSize: 11, fontWeight: 600 }}
                  >
                    {consistencyResult.status}
                  </span>
                  <span className="spacer" style={{ flex: 1 }} />
                  <button
                    className="btn small"
                    onClick={() => setConsistencyResult(null)}
                    title="Dismiss consistency findings"
                  >
                    ✕ Dismiss
                  </button>
                </div>

                <p style={{ fontSize: 13, margin: '4px 0 10px', color: 'var(--text)' }}>
                  {consistencyResult.summary}
                </p>

                {consistencyResult.findings.length > 0 && (
                  <div style={{ display: 'flex', flexDirection: 'column', gap: 8, marginTop: 10 }}>
                    {consistencyResult.findings.map((f, i) => (
                      <div
                        key={i}
                        style={{
                          background: 'var(--bg)',
                          border: '1px solid var(--border)',
                          borderRadius: 'var(--radius)',
                          padding: '10px 12px',
                        }}
                      >
                        <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 4 }}>
                          <span
                            className={`tag ${
                              f.type === 'conflict' ? 'danger' : f.type === 'overlap' ? 'accent' : 'ok'
                            }`}
                            style={{ fontSize: 10, textTransform: 'uppercase' }}
                          >
                            {f.type}
                          </span>
                          <span style={{ fontSize: 12, fontWeight: 600 }}>
                            Decision #{f.decision_id}: {f.title}
                          </span>
                          {f.markdown_path && (
                            <span className="mono faint" style={{ fontSize: 10 }}>
                              📄 {f.markdown_path}
                            </span>
                          )}
                          <span className="spacer" style={{ flex: 1 }} />
                          <button
                            type="button"
                            className="btn small"
                            onClick={() => setSelectedId(f.decision_id)}
                            title="Inspect this existing decision"
                          >
                            Inspect Decision
                          </button>
                        </div>
                        <p style={{ fontSize: 12, margin: '4px 0', color: 'var(--text-faint)' }}>
                          {f.reason}
                        </p>
                        {f.proposed_claim && f.existing_claim && (
                          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 8, marginTop: 6, fontSize: 11 }}>
                            <div style={{ padding: '4px 8px', background: 'var(--panel)', borderRadius: 4 }}>
                              <span className="faint" style={{ display: 'block', fontWeight: 600 }}>Proposed:</span>
                              <span>{f.proposed_claim}</span>
                            </div>
                            <div style={{ padding: '4px 8px', background: 'var(--panel)', borderRadius: 4 }}>
                              <span className="faint" style={{ display: 'block', fontWeight: 600 }}>Existing Approved:</span>
                              <span>{f.existing_claim}</span>
                            </div>
                          </div>
                        )}
                      </div>
                    ))}
                  </div>
                )}

                <div className="faint" style={{ fontSize: 11, marginTop: 10 }}>
                  Evaluated against {consistencyResult.candidates_evaluated.length} relevant approved decision(s). This check reports observations only and does not change approval status.
                </div>
              </div>
            )}


            {/* Sub-Navigation Tabs: Details vs ADR Preview vs Git Diff */}
            <div className="subtabs">
              <button
                className={`subtab-btn ${subTab === 'details' ? 'active' : ''}`}
                onClick={() => setSubTab('details')}
              >
                Decision Details
              </button>
              <button
                className={`subtab-btn ${subTab === 'adr' ? 'active' : ''}`}
                onClick={() => setSubTab('adr')}
              >
                📄 ADR Preview {selectedDecision.markdown_path ? '✓' : ''}
              </button>
              <button
                className={`subtab-btn ${subTab === 'diff' ? 'active' : ''}`}
                onClick={() => setSubTab('diff')}
              >
                🔀 Git Diff {approvalResult?.diff ? '(New)' : ''}
              </button>
              <button
                className={`subtab-btn ${subTab === 'status' ? 'active' : ''}`}
                onClick={() => setSubTab('status')}
              >
                Repository Status
              </button>
            </div>

            {/* Sub-Panel 1: Details */}
            {subTab === 'details' && (
              <div className="section" style={{ maxWidth: 840 }}>
                {/* Context */}
                <div style={{ marginBottom: 20 }}>
                  <h3 style={{ fontSize: 12, textTransform: 'uppercase', color: 'var(--text-faint)' }}>
                    Context & Problem Statement
                  </h3>
                  {selectedDecision.context ? (
                    renderMarkdown(selectedDecision.context)
                  ) : (
                    <p className="faint" style={{ fontStyle: 'italic', margin: '4px 0' }}>
                      No context provided.
                    </p>
                  )}
                </div>

                {/* Decision */}
                <div style={{ marginBottom: 20 }}>
                  <h3 style={{ fontSize: 12, textTransform: 'uppercase', color: 'var(--accent)' }}>
                    Architectural Decision
                  </h3>
                  {selectedDecision.decision ? (
                    renderMarkdown(selectedDecision.decision)
                  ) : (
                    <p className="faint" style={{ fontStyle: 'italic', margin: '4px 0' }}>
                      No decision text recorded.
                    </p>
                  )}
                </div>

                {/* Rationale */}
                <div style={{ marginBottom: 20 }}>
                  <h3 style={{ fontSize: 12, textTransform: 'uppercase', color: 'var(--text-faint)' }}>
                    Rationale & Considered Alternatives
                  </h3>
                  {selectedDecision.rationale ? (
                    renderMarkdown(selectedDecision.rationale)
                  ) : (
                    <p className="faint" style={{ fontStyle: 'italic', margin: '4px 0' }}>
                      No rationale provided.
                    </p>
                  )}
                </div>

                {/* Consequences */}
                <div style={{ marginBottom: 20 }}>
                  <h3 style={{ fontSize: 12, textTransform: 'uppercase', color: 'var(--text-faint)' }}>
                    Consequences & Trade-offs
                  </h3>
                  {selectedDecision.consequences ? (
                    renderMarkdown(selectedDecision.consequences)
                  ) : (
                    <p className="faint" style={{ fontStyle: 'italic', margin: '4px 0' }}>
                      No consequences provided.
                    </p>
                  )}
                </div>

                {/* Addresses Open Questions */}
                <div style={{ marginBottom: 20 }}>
                  <h3 style={{ fontSize: 12, textTransform: 'uppercase', color: 'var(--text-faint)', margin: '0 0 6px' }}>
                    Addresses Open Questions ({selectedDecision.related_questions?.length || 0})
                  </h3>

                  {selectedDecision.related_questions && selectedDecision.related_questions.length > 0 ? (
                    <div style={{ display: 'flex', flexDirection: 'column', gap: 6, marginBottom: 8 }}>
                      {selectedDecision.related_questions.map((qRef, i) => {
                        const matched = questions.find(
                          (q) => String(q.id) === String(qRef) || q.uid === String(qRef),
                        )
                        return (
                          <div
                            key={i}
                            className="inventory-item"
                            style={{ margin: 0, padding: '6px 10px', display: 'flex', alignItems: 'center', gap: 8 }}
                          >
                            <span className="tag warn">Question</span>
                            <span className="mono faint">#{String(qRef)}</span>
                            <span style={{ fontSize: 12, fontWeight: 500 }}>
                              {matched ? matched.title : `Question reference #${qRef}`}
                            </span>
                            {matched && (
                              <span className={`tag ${matched.status === 'open' ? 'warn' : 'ok'}`} style={{ fontSize: 10 }}>
                                {matched.status}
                              </span>
                            )}
                            <span className="spacer" style={{ flex: 1 }} />
                            {matched && onOpenQuestion && (
                              <button
                                type="button"
                                className="btn small"
                                onClick={() => onOpenQuestion(matched.id)}
                                title="Inspect this question"
                              >
                                View
                              </button>
                            )}
                            <button
                              type="button"
                              className="btn small danger"
                              onClick={() => handleUnlinkQuestion(qRef)}
                              disabled={busy}
                              title="Unlink question from this decision"
                            >
                              Unlink
                            </button>
                          </div>
                        )
                      })}
                    </div>
                  ) : (
                    <p className="faint" style={{ fontStyle: 'italic', margin: '4px 0 8px', fontSize: 12 }}>
                      Does not currently address any open questions.
                    </p>
                  )}

                  {/* Link Question Selector */}
                  {availableQuestionsToLink.length > 0 && (
                    <div style={{ display: 'flex', gap: 8, alignItems: 'center', marginTop: 8 }}>
                      <select
                        className="select small"
                        value={linkQuestionId}
                        onChange={(e) => setLinkQuestionId(e.target.value)}
                        style={{ flex: 1, maxWidth: 360 }}
                      >
                        <option value="">-- Select Open Question to Link --</option>
                        {availableQuestionsToLink.map((q) => (
                          <option key={q.id} value={q.id}>
                            #{q.id} [{q.status}] {q.title}
                          </option>
                        ))}
                      </select>
                      <button
                        type="button"
                        className="btn small primary"
                        disabled={!linkQuestionId || busy}
                        onClick={handleLinkQuestion}
                      >
                        + Link Question
                      </button>
                    </div>
                  )}
                </div>


                {/* Related Documents */}
                {selectedDecision.related_documents && selectedDecision.related_documents.length > 0 && (
                  <div style={{ marginBottom: 20 }}>
                    <h3 style={{ fontSize: 12, textTransform: 'uppercase', color: 'var(--text-faint)', margin: '0 0 6px' }}>
                      Related Architecture Documents
                    </h3>
                    <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
                      {selectedDecision.related_documents.map((docPath, i) => (
                        <button
                          key={i}
                          className="btn"
                          style={{ fontFamily: 'var(--mono)', fontSize: 11 }}
                          onClick={() => onSelectDocument(String(docPath))}
                          title="Open document"
                        >
                          📄 {String(docPath)}
                        </button>
                      ))}
                    </div>
                  </div>
                )}
              </div>
            )}

            {/* Sub-Panel 2: ADR Preview */}
            {subTab === 'adr' && (
              <div className="section" style={{ maxWidth: 840 }}>
                <div className="btn-row" style={{ marginBottom: 12, alignItems: 'center' }}>
                  <div className="badge-distinction">
                    <span>📄 ADR Markdown Document</span>
                    <span className="faint">· Durable File on Disk</span>
                  </div>
                  <span className="spacer" style={{ flex: 1 }} />
                  {selectedDecision.markdown_path && (
                    <button
                      className="btn"
                      onClick={() => onSelectDocument(selectedDecision.markdown_path!)}
                    >
                      Open in Document Viewer
                    </button>
                  )}
                </div>

                {selectedDecision.markdown_path ? (
                  <div>
                    <div className="mono faint" style={{ marginBottom: 8, fontSize: 11 }}>
                      Path: {selectedDecision.markdown_path}
                    </div>
                    {loadingAdr ? (
                      <div className="empty">Loading ADR document from repository...</div>
                    ) : adrMarkdown ? (
                      <div
                        style={{
                          background: 'var(--panel)',
                          border: '1px solid var(--border)',
                          borderRadius: 'var(--radius)',
                          padding: 16,
                        }}
                      >
                        {renderMarkdown(adrMarkdown)}
                      </div>
                    ) : (
                      <div className="empty">
                        <p>ADR file is registered at <span className="mono">{selectedDecision.markdown_path}</span> but could not be read.</p>
                        <p className="faint">Ensure the documentation repository is configured and accessible.</p>
                      </div>
                    )}
                  </div>
                ) : (
                  <div className="empty" style={{ padding: '40px 16px' }}>
                    <p style={{ fontSize: 14, fontWeight: 500, marginBottom: 6 }}>
                      No ADR document has been generated yet for this Decision.
                    </p>
                    <p className="faint" style={{ maxWidth: 480, margin: '0 auto 16px' }}>
                      Decisions remain in the database until explicitly approved. When you click &ldquo;Approve Decision&rdquo;, DocArchitect generates the corresponding Architecture Decision Record Markdown document.
                    </p>
                    <button
                      className="btn primary"
                      onClick={() => setShowApproveModal(true)}
                      disabled={busy}
                    >
                      Approve & Generate ADR Now
                    </button>
                  </div>
                )}
              </div>
            )}

            {/* Sub-Panel 3: Git Diff */}
            {subTab === 'diff' && (
              <div className="section" style={{ maxWidth: 840 }}>
                <div className="btn-row" style={{ marginBottom: 12, alignItems: 'center' }}>
                  <div className="badge-distinction">
                    <span>🔀 Proposed Git Diff</span>
                    <span className="faint">· Uncommitted Working Tree Change</span>
                  </div>
                  <span className="spacer" style={{ flex: 1 }} />
                  <span className="faint" style={{ fontSize: 11 }}>
                    Review uncommitted changes before committing
                  </span>
                </div>

                {approvalResult?.diff || supersedeResult?.diff ? (
                  <div>
                    <div className="faint" style={{ fontSize: 11, marginBottom: 4 }}>
                      Unified diff generated by recent {supersedeResult?.diff ? 'supersession' : 'approval'}:
                    </div>
                    {renderDiff((supersedeResult?.diff || approvalResult?.diff)!)}
                  </div>
                ) : loadingDiff ? (
                  <div className="empty">Loading repository working tree diff...</div>
                ) : repoDiff ? (
                  <div>
                    <div className="faint" style={{ fontSize: 11, marginBottom: 4 }}>
                      Repository working tree diff:
                    </div>
                    {renderDiff(repoDiff)}
                  </div>
                ) : (
                  <div className="empty" style={{ padding: '40px 16px' }}>
                    <p>No uncommitted Git diff found for this workspace repository.</p>
                    <p className="faint">The documentation repository working tree is clean.</p>
                  </div>
                )}
              </div>
            )}

            {/* Sub-Panel 4: Git Status */}
            {subTab === 'status' && (
              <div className="section" style={{ maxWidth: 840 }}>
                <div className="badge-distinction" style={{ marginBottom: 12 }}>
                  <span>📂 Repository Git Status</span>
                  <span className="faint">· Working Tree</span>
                </div>

                {(supersedeResult?.git_status || approvalResult?.git_status)?.length ? (
                  <div style={{ marginBottom: 16 }}>
                    <div className="faint" style={{ fontSize: 11, marginBottom: 6 }}>
                      Entries changed by decision {supersedeResult?.git_status ? 'supersession' : 'approval'}:
                    </div>
                    {(supersedeResult?.git_status || approvalResult?.git_status)!.map((entry, idx) => (
                      <div
                        key={idx}
                        className="inventory-item"
                        style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '6px 10px', margin: '4px 0' }}
                      >
                        <span className="tag warn mono">{entry.status}</span>
                        <span className="mono" style={{ fontSize: 11 }}>{entry.path}</span>
                      </div>
                    ))}
                  </div>
                ) : null}

                <div className="empty" style={{ textAlign: 'left', padding: 12 }}>
                  <p className="dim" style={{ fontSize: 12, margin: '0 0 6px' }}>
                    <strong>Note on Git Commit & Push:</strong>
                  </p>
                  <p className="faint" style={{ fontSize: 11, margin: 0 }}>
                    DocArchitect never automatically commits or pushes to remote repositories. Generated and updated ADRs remain in your local Git working tree for manual inspection and commit through your normal repository workflow.
                  </p>
                </div>
              </div>
            )}
          </div>
        ) : (
          <div className="empty" style={{ paddingTop: 80 }}>
            <p>Select a decision from the list to inspect details, preview its ADR, or approve it.</p>
            <button className="btn primary" onClick={startCreate} style={{ marginTop: 12 }}>
              + Create Decision
            </button>
          </div>
        )}
      </div>

      {/* Confirmation Modal for Decision Approval */}
      {showApproveModal && selectedDecision && (
        <div className="modal-overlay" onClick={() => !busy && setShowApproveModal(false)}>
          <div className="modal-dialog" onClick={(e) => e.stopPropagation()}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 12 }}>
              <span className="badge-distinction">
                <span>✓ Explicit Approval Action</span>
              </span>
            </div>

            <h3 style={{ fontSize: 16, margin: '0 0 8px' }}>
              Approve Decision & Synchronize ADR
            </h3>

            <p className="dim" style={{ fontSize: 13, lineHeight: 1.5, margin: '0 0 12px' }}>
              You are approving: <strong>{selectedDecision.title}</strong>
            </p>

            <div
              style={{
                background: 'var(--panel-2)',
                border: '1px solid var(--border)',
                borderRadius: 'var(--radius)',
                padding: '10px 12px',
                fontSize: 12,
                color: 'var(--text-dim)',
                marginBottom: 16,
                lineHeight: 1.5,
              }}
            >
              <div style={{ fontWeight: 600, color: 'var(--text)', marginBottom: 6 }}>
                What happens upon approval:
              </div>
              <ul style={{ margin: 0, paddingLeft: 18 }}>
                <li>The decision status will be set to <span className="tag ok">approved</span>.</li>
                <li>An Architecture Decision Record (ADR) Markdown document will be created or updated in the documentation repository (under <span className="mono">architecture/decisions/</span>).</li>
                <li>The database remains the source of truth for the Decision entity; the generated Markdown document is the durable repository representation.</li>
                <li>The repository changes will remain <strong>uncommitted in the working tree</strong> for your inspection.</li>
                <li><strong>No automatic Git commit or push will occur.</strong></li>
              </ul>
            </div>

            {error && <div className="banner" style={{ marginBottom: 12 }}>{error}</div>}

            <div className="btn-row" style={{ justifyContent: 'flex-end', gap: 8 }}>
              <button
                type="button"
                className="btn"
                onClick={() => setShowApproveModal(false)}
                disabled={busy}
              >
                Cancel
              </button>
              <button
                type="button"
                className="btn primary"
                onClick={handleApprove}
                disabled={busy}
                style={{ fontWeight: 600 }}
              >
                {busy ? 'Approving & Synchronizing...' : 'Confirm Approval & Generate ADR'}
              </button>
            </div>
          </div>
        </div>
      )}
      {/* Confirmation Modal for Superseding */}
      {showSupersedeModal && selectedDecision && (
        <div className="modal-overlay" onClick={() => !busy && setShowSupersedeModal(false)}>
          <div className="modal-dialog" onClick={(e) => e.stopPropagation()}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 12 }}>
              <span className="badge-distinction">
                <span>⚡ Supersede Architectural Decision</span>
              </span>
            </div>

            <h3 style={{ fontSize: 16, margin: '0 0 8px' }}>
              Mark Decision #{selectedDecision.id} as Superseded
            </h3>

            <div
              style={{
                background: 'var(--panel-2)',
                border: '1px solid var(--border)',
                borderRadius: 'var(--radius)',
                padding: '12px 14px',
                marginBottom: 16,
                fontSize: 13,
              }}
            >
              <div style={{ marginBottom: 10 }}>
                <span className="faint" style={{ display: 'block', fontSize: 11, fontWeight: 600 }}>Old Decision:</span>
                <strong>#{selectedDecision.id} — {selectedDecision.title}</strong>
              </div>

              <div>
                <label className="form-label" style={{ fontSize: 11, fontWeight: 600, marginBottom: 4 }}>
                  Superseded by (newer approved decision):
                </label>
                <select
                  value={supersedeTargetId ?? ''}
                  onChange={(e) => setSupersedeTargetId(Number(e.target.value))}
                  disabled={busy}
                  style={{ width: '100%', padding: '6px 8px' }}
                >
                  {eligibleSupersedingDecisions.map((d) => (
                    <option key={d.id} value={d.id}>
                      #{d.id} — {d.title}
                    </option>
                  ))}
                </select>
              </div>
            </div>

            <div
              style={{
                background: 'var(--panel-2)',
                border: '1px solid var(--border)',
                borderRadius: 'var(--radius)',
                padding: '10px 12px',
                fontSize: 12,
                color: 'var(--text-dim)',
                marginBottom: 16,
                lineHeight: 1.5,
              }}
            >
              <div style={{ fontWeight: 600, color: 'var(--text)', marginBottom: 6 }}>
                Lifecycle changes:
              </div>
              <ul style={{ margin: 0, paddingLeft: 18 }}>
                <li>Old Decision #{selectedDecision.id} status becomes <span className="tag dim">superseded</span>.</li>
                <li>Historical content, rationale, and references remain permanently preserved.</li>
                <li>Old ADR receives a supersession notice near the beginning: <span className="mono">&gt; Superseded by ADR-XXX.</span></li>
                <li>Changes to the ADR remain <strong>uncommitted in the Git working tree</strong> for your review.</li>
                <li><strong>No automatic commit or push will occur.</strong></li>
              </ul>
            </div>

            {error && <div className="banner" style={{ marginBottom: 12 }}>{error}</div>}

            <div className="btn-row" style={{ justifyContent: 'flex-end', gap: 8 }}>
              <button
                type="button"
                className="btn"
                onClick={() => setShowSupersedeModal(false)}
                disabled={busy}
              >
                Cancel
              </button>
              <button
                type="button"
                className="btn primary"
                onClick={handleSupersede}
                disabled={busy || !supersedeTargetId}
                style={{ fontWeight: 600 }}
              >
                {busy ? 'Superseding...' : 'Confirm Supersession'}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
