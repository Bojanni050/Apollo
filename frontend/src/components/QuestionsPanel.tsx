import { useState } from 'react'
import {
  ApiError,
  api,
  type Decision,
  type OpenQuestion,
  type QuestionStatus,
  type Workspace,
} from '../api/client'
import { renderMarkdown } from '../markdown'

interface Props {
  workspace: Workspace
  questions: OpenQuestion[]
  decisions?: Decision[]
  onRefreshQuestions: () => Promise<void>
  onRefreshDecisions?: () => Promise<void>
  onOpenDecision?: (decisionId: number) => void
}

export function QuestionsPanel({
  workspace,
  questions,
  decisions = [],
  onRefreshQuestions,
  onRefreshDecisions,
  onOpenDecision,
}: Props) {
  const [selectedId, setSelectedId] = useState<number | null>(null)
  const [filter, setFilter] = useState<'all' | QuestionStatus>('all')
  const [search, setSearch] = useState('')
  const [isCreating, setIsCreating] = useState(false)
  const [isEditing, setIsEditing] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [linkDecisionId, setLinkDecisionId] = useState<string>('')

  // Form states
  const [title, setTitle] = useState('')
  const [description, setDescription] = useState('')
  const [status, setStatus] = useState<QuestionStatus>('open')
  const [source, setSource] = useState('manual')
  const [affected, setAffected] = useState('')
  const [resolution, setResolution] = useState('')

  const selectedQuestion = questions.find((q) => q.id === selectedId) ?? null


  const filteredQuestions = questions.filter((q) => {
    if (filter !== 'all' && q.status !== filter) return false
    if (search.trim()) {
      const s = search.toLowerCase()
      return (
        q.title.toLowerCase().includes(s) ||
        q.description.toLowerCase().includes(s) ||
        (q.resolution && q.resolution.toLowerCase().includes(s))
      )
    }
    return true
  })

  const resetForm = () => {
    setTitle('')
    setDescription('')
    setStatus('open')
    setSource('manual')
    setAffected('')
    setResolution('')
    setError(null)
  }

  const startCreate = () => {
    resetForm()
    setIsEditing(false)
    setIsCreating(true)
  }

  const startEdit = (q: OpenQuestion) => {
    setTitle(q.title)
    setDescription(q.description || '')
    setStatus(q.status)
    setSource(q.source || 'manual')
    setAffected(q.affected ? q.affected.join(', ') : '')
    setResolution(q.resolution || '')
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
      const affectedList = affected
        .split(',')
        .map((s) => s.trim())
        .filter(Boolean)
      const created = await api.createQuestion(workspace.id, {
        title: title.trim(),
        description: description.trim(),
        status,
        source: source.trim() || 'manual',
        affected: affectedList,
      })
      await onRefreshQuestions()
      setIsCreating(false)
      setSelectedId(created.id)
      resetForm()
    } catch (err) {
      setError(err instanceof ApiError ? err.detail : 'Failed to create question.')
    } finally {
      setBusy(false)
    }
  }

  const handleUpdate = async (e: React.FormEvent) => {
    e.preventDefault()
    if (!selectedQuestion) return
    if (!title.trim()) {
      setError('Title cannot be empty.')
      return
    }
    setBusy(true)
    setError(null)
    try {
      const affectedList = affected
        .split(',')
        .map((s) => s.trim())
        .filter(Boolean)
      await api.updateQuestion(workspace.id, selectedQuestion.id, {
        title: title.trim(),
        description: description.trim(),
        status,
        source: source.trim(),
        affected: affectedList,
        resolution: resolution.trim() || null,
        resolved_at: status === 'resolved' || status === 'answered' ? new Date().toISOString() : null,
      })
      await onRefreshQuestions()
      setIsEditing(false)
    } catch (err) {
      setError(err instanceof ApiError ? err.detail : 'Failed to update question.')
    } finally {
      setBusy(false)
    }
  }

  const handleDelete = async (q: OpenQuestion) => {
    if (!window.confirm(`Delete question "${q.title}"?`)) return
    setBusy(true)
    setError(null)
    try {
      await api.deleteQuestion(workspace.id, q.id)
      await onRefreshQuestions()
      if (selectedId === q.id) {
        setSelectedId(null)
        setIsEditing(false)
      }
    } catch (err) {
      setError(err instanceof ApiError ? err.detail : 'Failed to delete question.')
    } finally {
      setBusy(false)
    }
  }

  const addressedDecisions = selectedQuestion
    ? decisions.filter((d) => {
        if (selectedQuestion.addressed_by && selectedQuestion.addressed_by.includes(d.id)) {
          return true
        }
        const qRefs = [String(selectedQuestion.id), selectedQuestion.uid]
        return (d.related_questions || []).some((r) => qRefs.includes(String(r)))
      })
    : []

  const availableDecisionsToLink = decisions.filter(
    (d) => !addressedDecisions.some((ad) => ad.id === d.id),
  )

  const handleLinkDecision = async () => {
    if (!selectedQuestion || !linkDecisionId) return
    setBusy(true)
    setError(null)
    try {
      await api.linkQuestionDecision(workspace.id, selectedQuestion.id, Number(linkDecisionId))
      await Promise.all([onRefreshQuestions(), onRefreshDecisions?.()])
      setLinkDecisionId('')
    } catch (e) {
      setError(e instanceof ApiError ? e.message : 'Failed to link decision.')
    } finally {
      setBusy(false)
    }
  }

  const handleUnlinkDecision = async (decisionId: number) => {
    if (!selectedQuestion) return
    setBusy(true)
    setError(null)
    try {
      await api.unlinkQuestionDecision(workspace.id, selectedQuestion.id, decisionId)
      await Promise.all([onRefreshQuestions(), onRefreshDecisions?.()])
    } catch (e) {
      setError(e instanceof ApiError ? e.message : 'Failed to unlink decision.')
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="split-layout">
      {/* Sidebar: list of questions */}
      <div className="split-sidebar">
        <div className="panel-header" style={{ justifyContent: 'space-between' }}>
          <span>Open Questions ({questions.length})</span>
          <button className="btn primary" onClick={startCreate} disabled={busy}>
            + New
          </button>
        </div>

        <div style={{ padding: '8px 10px', borderBottom: '1px solid var(--border)' }}>
          <input
            type="search"
            placeholder="Search questions..."
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            style={{ fontSize: 12, padding: '4px 8px' }}
          />
        </div>

        <div className="subtabs" style={{ padding: '0 8px' }}>
          {(['all', 'open', 'answered', 'resolved'] as const).map((tab) => (
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
          {filteredQuestions.length === 0 ? (
            <div className="empty">
              {questions.length === 0 ? 'No questions recorded yet.' : 'No questions match the filter.'}
            </div>
          ) : (
            filteredQuestions.map((q) => {
              const isSelected = q.id === selectedId
              return (
                <div
                  key={q.id}
                  className={`list-item ${isSelected ? 'active' : ''}`}
                  onClick={() => {
                    setSelectedId(q.id)
                    setIsCreating(false)
                    setIsEditing(false)
                    setError(null)
                  }}
                  style={{ borderBottom: '1px solid var(--border)', borderRadius: 0, padding: '10px 12px' }}
                >
                  <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginBottom: 4 }}>
                    <span
                      className={`tag ${
                        q.status === 'open' ? 'warn' : q.status === 'answered' ? 'ok' : 'dim'
                      }`}
                    >
                      {q.status}
                    </span>
                    <span className="mono faint" style={{ fontSize: 10 }}>
                      #{q.id}
                    </span>
                    <span className="spacer" style={{ flex: 1 }} />
                    <span className="faint" style={{ fontSize: 10 }}>
                      {q.source}
                    </span>
                  </div>
                  <div className="list-title" style={{ whiteSpace: 'normal', fontWeight: 500, lineHeight: 1.4 }}>
                    {q.title}
                  </div>
                  {q.affected && q.affected.length > 0 && (
                    <div style={{ marginTop: 4, display: 'flex', gap: 4, flexWrap: 'wrap' }}>
                      {q.affected.map((aff, i) => (
                        <span key={i} className="tag mono" style={{ fontSize: 9 }}>
                          {String(aff)}
                        </span>
                      ))}
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

        {isCreating ? (
          <div className="section" style={{ maxWidth: 760 }}>
            <div className="btn-row" style={{ marginBottom: 12, alignItems: 'center' }}>
              <div className="badge-distinction">
                <span>❓ Open Question</span>
                <span className="faint">· Unresolved issue</span>
              </div>
              <span className="spacer" style={{ flex: 1 }} />
              <button className="btn" onClick={() => setIsCreating(false)} disabled={busy}>
                Cancel
              </button>
            </div>

            <h2 style={{ fontSize: 16, margin: '0 0 16px' }}>New Open Question</h2>

            <form onSubmit={handleCreate}>
              <div className="form-group">
                <label className="form-label">Question Title *</label>
                <input
                  type="text"
                  placeholder="e.g. Which relational database engine should we adopt?"
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
                  onChange={(e) => setStatus(e.target.value as QuestionStatus)}
                  disabled={busy}
                >
                  <option value="open">Open (Unresolved)</option>
                  <option value="answered">Answered</option>
                  <option value="resolved">Resolved</option>
                </select>
              </div>

              <div className="form-group">
                <label className="form-label">Description / Background</label>
                <textarea
                  rows={5}
                  placeholder="Describe the question, background, architectural trade-offs..."
                  value={description}
                  onChange={(e) => setDescription(e.target.value)}
                  disabled={busy}
                />
              </div>

              <div className="form-group">
                <label className="form-label">Affected Components / Files</label>
                <input
                  type="text"
                  placeholder="Comma-separated e.g. backend/db, storage, architecture"
                  value={affected}
                  onChange={(e) => setAffected(e.target.value)}
                  disabled={busy}
                />
              </div>

              <div className="form-group">
                <label className="form-label">Source</label>
                <input
                  type="text"
                  placeholder="manual, architecture-review, etc."
                  value={source}
                  onChange={(e) => setSource(e.target.value)}
                  disabled={busy}
                />
              </div>

              <div className="btn-row" style={{ marginTop: 16 }}>
                <button type="submit" className="btn primary" disabled={busy || !title.trim()}>
                  {busy ? 'Saving...' : 'Create Question'}
                </button>
                <button type="button" className="btn" onClick={() => setIsCreating(false)} disabled={busy}>
                  Cancel
                </button>
              </div>
            </form>
          </div>
        ) : isEditing && selectedQuestion ? (
          <div className="section" style={{ maxWidth: 760 }}>
            <div className="btn-row" style={{ marginBottom: 12, alignItems: 'center' }}>
              <div className="badge-distinction">
                <span>❓ Edit Question #{selectedQuestion.id}</span>
              </div>
              <span className="spacer" style={{ flex: 1 }} />
              <button className="btn" onClick={() => setIsEditing(false)} disabled={busy}>
                Cancel
              </button>
            </div>

            <form onSubmit={handleUpdate}>
              <div className="form-group">
                <label className="form-label">Question Title *</label>
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
                  onChange={(e) => setStatus(e.target.value as QuestionStatus)}
                  disabled={busy}
                >
                  <option value="open">Open (Unresolved)</option>
                  <option value="answered">Answered</option>
                  <option value="resolved">Resolved</option>
                </select>
              </div>

              <div className="form-group">
                <label className="form-label">Description</label>
                <textarea
                  rows={5}
                  value={description}
                  onChange={(e) => setDescription(e.target.value)}
                  disabled={busy}
                />
              </div>

              <div className="form-group">
                <label className="form-label">Resolution / Outcome</label>
                <textarea
                  rows={3}
                  placeholder="Record resolution notes, chosen decision reference, or answer..."
                  value={resolution}
                  onChange={(e) => setResolution(e.target.value)}
                  disabled={busy}
                />
              </div>

              <div className="form-group">
                <label className="form-label">Affected Components (comma-separated)</label>
                <input
                  type="text"
                  value={affected}
                  onChange={(e) => setAffected(e.target.value)}
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
        ) : selectedQuestion ? (
          <div className="section" style={{ maxWidth: 840 }}>
            {/* Inspector header */}
            <div className="btn-row" style={{ alignItems: 'center', marginBottom: 12 }}>
              <div className="badge-distinction">
                <span>❓ Open Question</span>
                <span className="faint">· Unresolved issue</span>
              </div>
              <span
                className={`tag ${
                  selectedQuestion.status === 'open'
                    ? 'warn'
                    : selectedQuestion.status === 'answered'
                    ? 'ok'
                    : 'dim'
                }`}
                style={{ fontSize: 11 }}
              >
                {selectedQuestion.status}
              </span>
              <span className="spacer" style={{ flex: 1 }} />
              <button className="btn" onClick={() => startEdit(selectedQuestion)} disabled={busy}>
                Edit
              </button>
              <button className="btn danger" onClick={() => handleDelete(selectedQuestion)} disabled={busy}>
                Delete
              </button>
            </div>

            <h1 style={{ fontSize: 20, margin: '8px 0 12px', lineHeight: 1.3 }}>
              {selectedQuestion.title}
            </h1>

            <div className="btn-row" style={{ marginBottom: 16, alignItems: 'center' }}>
              <span className="mono faint" style={{ fontSize: 11 }}>
                ID: {selectedQuestion.id} · UID: {selectedQuestion.uid}
              </span>
              <span className="faint">·</span>
              <span className="faint" style={{ fontSize: 11 }}>
                Source: {selectedQuestion.source}
              </span>
              <span className="faint">·</span>
              <span className="faint" style={{ fontSize: 11 }}>
                Created: {new Date(selectedQuestion.created_at).toLocaleDateString()}
              </span>
            </div>

            {/* Description */}
            <div style={{ marginBottom: 20 }}>
              <h3 style={{ fontSize: 12, textTransform: 'uppercase', color: 'var(--text-faint)' }}>
                Description & Context
              </h3>
              {selectedQuestion.description ? (
                renderMarkdown(selectedQuestion.description)
              ) : (
                <p className="faint" style={{ fontStyle: 'italic', margin: '4px 0' }}>
                  No description provided.
                </p>
              )}
            </div>

            {/* Resolution */}
            {selectedQuestion.resolution && (
              <div
                style={{
                  marginBottom: 20,
                  background: 'var(--panel-2)',
                  padding: 12,
                  borderRadius: 'var(--radius)',
                  borderLeft: '3px solid var(--ok)',
                }}
              >
                <h3 style={{ fontSize: 12, textTransform: 'uppercase', color: 'var(--ok)', margin: '0 0 6px' }}>
                  Resolution / Outcome
                </h3>
                {renderMarkdown(selectedQuestion.resolution)}
                {selectedQuestion.resolved_at && (
                  <div className="faint" style={{ fontSize: 11, marginTop: 6 }}>
                    Resolved at: {new Date(selectedQuestion.resolved_at).toLocaleString()}
                  </div>
                )}
              </div>
            )}

            {/* Affected components */}
            {selectedQuestion.affected && selectedQuestion.affected.length > 0 && (
              <div style={{ marginBottom: 20 }}>
                <h3 style={{ fontSize: 12, textTransform: 'uppercase', color: 'var(--text-faint)', margin: '0 0 6px' }}>
                  Affected Areas & Components
                </h3>
                <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
                  {selectedQuestion.affected.map((aff, i) => (
                    <span key={i} className="tag mono">
                      {String(aff)}
                    </span>
                  ))}
                </div>
              </div>
            )}

            {/* Addressed by Decisions */}
            <div style={{ marginBottom: 20 }}>
              <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 6 }}>
                <h3 style={{ fontSize: 12, textTransform: 'uppercase', color: 'var(--text-faint)', margin: 0 }}>
                  Addressed by Decisions ({addressedDecisions.length})
                </h3>
              </div>

              {addressedDecisions.length > 0 ? (
                <div style={{ display: 'flex', flexDirection: 'column', gap: 6, marginBottom: 10 }}>
                  {addressedDecisions.map((d) => (
                    <div
                      key={d.id}
                      className="inventory-item"
                      style={{ margin: 0, padding: '8px 12px', display: 'flex', alignItems: 'center', gap: 8 }}
                    >
                      <span className="tag accent">Decision #{d.id}</span>
                      <span style={{ fontSize: 13, fontWeight: 500 }}>{d.title}</span>
                      <span className={`tag ${d.status === 'approved' ? 'ok' : 'dim'}`} style={{ fontSize: 11 }}>
                        {d.status}
                      </span>
                      <span className="spacer" style={{ flex: 1 }} />
                      {onOpenDecision && (
                        <button
                          type="button"
                          className="btn small"
                          onClick={() => onOpenDecision(d.id)}
                          title="Open decision details"
                        >
                          View
                        </button>
                      )}
                      <button
                        type="button"
                        className="btn small danger"
                        onClick={() => handleUnlinkDecision(d.id)}
                        disabled={busy}
                        title="Remove link to this decision"
                      >
                        Unlink
                      </button>
                    </div>
                  ))}
                </div>
              ) : (
                <p className="faint" style={{ fontStyle: 'italic', margin: '4px 0 10px', fontSize: 12 }}>
                  Not yet addressed by any architectural decision.
                </p>
              )}

              {/* Link new decision selector */}
              {availableDecisionsToLink.length > 0 && (
                <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
                  <select
                    className="select small"
                    value={linkDecisionId}
                    onChange={(e) => setLinkDecisionId(e.target.value)}
                    style={{ flex: 1, maxWidth: 360 }}
                  >
                    <option value="">-- Select Decision to Link --</option>
                    {availableDecisionsToLink.map((d) => (
                      <option key={d.id} value={d.id}>
                        #{d.id} [{d.status}] {d.title}
                      </option>
                    ))}
                  </select>
                  <button
                    type="button"
                    className="btn small primary"
                    disabled={!linkDecisionId || busy}
                    onClick={handleLinkDecision}
                  >
                    + Link Decision
                  </button>
                </div>
              )}
            </div>
          </div>

        ) : (
          <div className="empty" style={{ paddingTop: 80 }}>
            <p>Select a question from the list to inspect details, or create a new one.</p>
            <button className="btn primary" onClick={startCreate} style={{ marginTop: 12 }}>
              + Create Question
            </button>
          </div>
        )}
      </div>
    </div>
  )
}
