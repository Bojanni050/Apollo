import { useEffect, useRef, useState } from 'react'
import {
  ApiError,
  api,
  type ChatStatus,
  type ConversationDetail,
  type Message,
  type Mode,
} from '../api/client'

const MODES: { id: Mode; label: string; note: string }[] = [
  { id: 'explore', label: 'Explore', note: 'Free discussion. The AI may read, but not change anything.' },
  { id: 'investigate', label: 'Investigate', note: 'The AI searches the workspace and cites the files it used.' },
  { id: 'apply', label: 'Apply', note: 'Prepare documentation changes for your approval. Nothing is written without you.' },
]

function Citations({
  message,
  onOpen,
  onOpenQuestion,
  onOpenDecision,
}: {
  message: Message
  onOpen: (path: string) => void
  onOpenQuestion?: (id: number) => void
  onOpenDecision?: (id: number) => void
}) {
  if (!message.citations || message.citations.length === 0) return null
  return (
    <div style={{ marginTop: 12 }}>
      <div className="faint" style={{ fontSize: 11, marginBottom: 6, letterSpacing: '0.05em' }}>
        SOURCES
      </div>
      {message.citations.map((c, i) => {
        if (c.decision_id) {
          return (
            <button
              key={`dec-${c.decision_id}-${i}`}
              className="citation"
              onClick={() => (onOpenDecision ? onOpenDecision(c.decision_id!) : onOpen(c.path))}
              title={`Open Decision #${c.decision_id}`}
            >
              <div className="path">
                ⚖️ Decision #{c.decision_id}{c.note ? ` · ${c.note}` : ''}
              </div>
              <div className="meta">
                <span className="tag accent">explicit decision</span>
              </div>
            </button>
          )
        }
        if (c.question_id) {
          return (
            <button
              key={`q-${c.question_id}-${i}`}
              className="citation"
              onClick={() => (onOpenQuestion ? onOpenQuestion(c.question_id!) : onOpen(c.path))}
              title={`Open Question #${c.question_id}`}
            >
              <div className="path">
                ❓ Question #{c.question_id}{c.note ? ` · ${c.note}` : ''}
              </div>
              <div className="meta">
                <span className="tag warn">unresolved</span>
              </div>
            </button>
          )
        }
        return (
          <button
            key={`${c.repository}-${c.path}-${i}`}
            className="citation"
            onClick={() => onOpen(c.path)}
            title="Open this file"
          >
            <div className="path">
              {c.repository}/{c.path}
            </div>
            <div className="meta">
              <span className="tag">{c.evidence_type.replace(/_/g, ' ')}</span>
              {c.revision ? <span className="mono faint">{c.revision.slice(0, 8)}</span> : null}
            </div>
          </button>
        )
      })}
    </div>
  )
}

function QuestionDraftCard({
  workspaceId,
  args,
  conversationId,
  onSaved,
  onOpenQuestion,
}: {
  workspaceId: number
  args: Record<string, any>
  conversationId: number
  onSaved?: () => Promise<void>
  onOpenQuestion?: (id: number) => void
}) {
  const [savedId, setSavedId] = useState<number | null>(null)
  const [dismissed, setDismissed] = useState(false)
  const [isEditing, setIsEditing] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const [title, setTitle] = useState(String(args.title || ''))
  const [description, setDescription] = useState(String(args.description || ''))
  const [affected, setAffected] = useState(
    Array.isArray(args.affected) ? args.affected.join(', ') : String(args.affected || ''),
  )

  if (dismissed) return null

  const handleSave = async () => {
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
      const created = await api.createQuestion(workspaceId, {
        title: title.trim(),
        description: description.trim(),
        status: 'open',
        source: 'conversation',
        conversation_id: conversationId || null,
        affected: affectedList,
      })
      setSavedId(created.id)
      setIsEditing(false)
      if (onSaved) await onSaved()
    } catch (err) {
      setError(err instanceof ApiError ? err.detail : 'Failed to save question.')
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className={`draft-card ${savedId ? 'saved' : ''}`}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginBottom: 8 }}>
        <span className="badge-distinction">
          <span>❓ Proposed OpenQuestion (Draft)</span>
        </span>
        <span className="spacer" style={{ flex: 1 }} />
        {savedId ? (
          <span className="tag ok">✓ Saved as #{savedId}</span>
        ) : (
          <span className="tag warn">Pending Operator Review</span>
        )}
      </div>

      {error && <div className="banner" style={{ marginBottom: 8 }}>{error}</div>}

      {isEditing && !savedId ? (
        <div style={{ marginTop: 8 }}>
          <div className="form-group">
            <label className="form-label">Title</label>
            <input
              type="text"
              value={title}
              onChange={(e) => setTitle(e.target.value)}
              disabled={busy}
            />
          </div>
          <div className="form-group">
            <label className="form-label">Description</label>
            <textarea
              rows={3}
              value={description}
              onChange={(e) => setDescription(e.target.value)}
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
          <div className="btn-row" style={{ marginTop: 8 }}>
            <button className="btn primary" onClick={handleSave} disabled={busy || !title.trim()}>
              {busy ? 'Saving...' : 'Save Question'}
            </button>
            <button className="btn" onClick={() => setIsEditing(false)} disabled={busy}>
              Cancel
            </button>
          </div>
        </div>
      ) : (
        <div>
          <div style={{ fontWeight: 600, fontSize: 13, marginBottom: 4 }}>
            {title}
          </div>
          {description && (
            <p className="dim" style={{ fontSize: 12, margin: '0 0 6px' }}>
              {description}
            </p>
          )}
          {affected && (
            <div className="faint mono" style={{ fontSize: 11, marginBottom: 8 }}>
              Affected: {affected}
            </div>
          )}

          <div className="btn-row" style={{ alignItems: 'center', marginTop: 8 }}>
            {savedId ? (
              onOpenQuestion && (
                <button className="btn" onClick={() => onOpenQuestion(savedId)}>
                  Inspect Question #{savedId}
                </button>
              )
            ) : (
              <>
                <button className="btn primary" onClick={handleSave} disabled={busy}>
                  {busy ? 'Saving...' : 'Save Question'}
                </button>
                <button className="btn" onClick={() => setIsEditing(true)} disabled={busy}>
                  Edit
                </button>
                <button className="btn" onClick={() => setDismissed(true)} disabled={busy}>
                  Dismiss
                </button>
              </>
            )}
          </div>
        </div>
      )}
    </div>
  )
}

function DecisionDraftCard({
  workspaceId,
  args,
  onSaved,
  onOpenDecision,
}: {
  workspaceId: number
  args: Record<string, any>
  onSaved?: () => Promise<void>
  onOpenDecision?: (id: number) => void
}) {
  const [savedId, setSavedId] = useState<number | null>(null)
  const [dismissed, setDismissed] = useState(false)
  const [isEditing, setIsEditing] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const [title, setTitle] = useState(String(args.title || ''))
  const [context, setContext] = useState(String(args.context || ''))
  const [decisionText, setDecisionText] = useState(String(args.decision || ''))
  const [rationale, setRationale] = useState(String(args.rationale || ''))
  const [consequences, setConsequences] = useState(String(args.consequences || ''))
  const [relatedQuestions, setRelatedQuestions] = useState(
    Array.isArray(args.related_questions) ? args.related_questions.join(', ') : String(args.related_questions || ''),
  )
  const [relatedDocuments, setRelatedDocuments] = useState(
    Array.isArray(args.related_documents) ? args.related_documents.join(', ') : String(args.related_documents || ''),
  )

  if (dismissed) return null

  const handleSave = async () => {
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
      const created = await api.createDecision(workspaceId, {
        title: title.trim(),
        status: 'proposed',
        context: context.trim(),
        decision: decisionText.trim(),
        rationale: rationale.trim(),
        consequences: consequences.trim(),
        related_questions: qList,
        related_documents: docList,
      })
      setSavedId(created.id)
      setIsEditing(false)
      if (onSaved) await onSaved()
    } catch (err) {
      setError(err instanceof ApiError ? err.detail : 'Failed to save decision.')
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className={`draft-card ${savedId ? 'saved' : ''}`}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginBottom: 8 }}>
        <span className="badge-distinction">
          <span>⚖️ Proposed Decision (Draft)</span>
        </span>
        <span className="spacer" style={{ flex: 1 }} />
        {savedId ? (
          <span className="tag ok">✓ Saved as #{savedId} (Proposed)</span>
        ) : (
          <span className="tag warn">Pending Operator Review</span>
        )}
      </div>

      <p className="faint" style={{ fontSize: 11, margin: '0 0 8px' }}>
        Saving creates a <strong>proposed</strong> decision in the database. Formal approval and ADR document generation remain an explicit operator action in the Decisions view.
      </p>

      {error && <div className="banner" style={{ marginBottom: 8 }}>{error}</div>}

      {isEditing && !savedId ? (
        <div style={{ marginTop: 8 }}>
          <div className="form-group">
            <label className="form-label">Title</label>
            <input
              type="text"
              value={title}
              onChange={(e) => setTitle(e.target.value)}
              disabled={busy}
            />
          </div>
          <div className="form-group">
            <label className="form-label">Context</label>
            <textarea
              rows={2}
              value={context}
              onChange={(e) => setContext(e.target.value)}
              disabled={busy}
            />
          </div>
          <div className="form-group">
            <label className="form-label">Decision</label>
            <textarea
              rows={2}
              value={decisionText}
              onChange={(e) => setDecisionText(e.target.value)}
              disabled={busy}
            />
          </div>
          <div className="form-group">
            <label className="form-label">Rationale</label>
            <textarea
              rows={2}
              value={rationale}
              onChange={(e) => setRationale(e.target.value)}
              disabled={busy}
            />
          </div>
          <div className="form-group">
            <label className="form-label">Consequences</label>
            <textarea
              rows={2}
              value={consequences}
              onChange={(e) => setConsequences(e.target.value)}
              disabled={busy}
            />
          </div>
          <div className="form-group">
            <label className="form-label">Related Questions (comma-separated IDs)</label>
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
          <div className="btn-row" style={{ marginTop: 8 }}>
            <button className="btn primary" onClick={handleSave} disabled={busy || !title.trim()}>
              {busy ? 'Saving...' : 'Save Decision (as Proposed)'}
            </button>
            <button className="btn" onClick={() => setIsEditing(false)} disabled={busy}>
              Cancel
            </button>
          </div>
        </div>
      ) : (
        <div>
          <div style={{ fontWeight: 600, fontSize: 13, marginBottom: 4 }}>
            {title}
          </div>
          {context && (
            <p className="dim" style={{ fontSize: 12, margin: '0 0 4px' }}>
              <strong>Context:</strong> {context}
            </p>
          )}
          {decisionText && (
            <p className="dim" style={{ fontSize: 12, margin: '0 0 4px' }}>
              <strong>Decision:</strong> {decisionText}
            </p>
          )}
          {rationale && (
            <p className="dim" style={{ fontSize: 12, margin: '0 0 4px' }}>
              <strong>Rationale:</strong> {rationale}
            </p>
          )}
          {consequences && (
            <p className="dim" style={{ fontSize: 12, margin: '0 0 4px' }}>
              <strong>Consequences:</strong> {consequences}
            </p>
          )}

          <div className="btn-row" style={{ alignItems: 'center', marginTop: 8 }}>
            {savedId ? (
              onOpenDecision && (
                <button className="btn primary" onClick={() => onOpenDecision(savedId)}>
                  Open in Decisions View to Review & Approve
                </button>
              )
            ) : (
              <>
                <button className="btn primary" onClick={handleSave} disabled={busy}>
                  {busy ? 'Saving...' : 'Save Decision (as Proposed)'}
                </button>
                <button className="btn" onClick={() => setIsEditing(true)} disabled={busy}>
                  Edit
                </button>
                <button className="btn" onClick={() => setDismissed(true)} disabled={busy}>
                  Dismiss
                </button>
              </>
            )}
          </div>
        </div>
      )}
    </div>
  )
}

export function ConversationPanel({
  workspaceId,
  conversation,
  chatStatus,
  sending,
  error,
  onSend,
  onModeChange,
  onOpenFile,
  onOpenQuestion,
  onOpenDecision,
  onQuestionSaved,
  onDecisionSaved,
}: {
  workspaceId?: number
  conversation: ConversationDetail | null
  chatStatus: ChatStatus | null
  sending: boolean
  error: string | null
  onSend: (text: string) => void
  onModeChange: (mode: Mode) => void
  onOpenFile: (path: string) => void
  onOpenQuestion?: (id: number) => void
  onOpenDecision?: (id: number) => void
  onQuestionSaved?: () => Promise<void>
  onDecisionSaved?: () => Promise<void>
}) {
  const [draft, setDraft] = useState('')
  const bottom = useRef<HTMLDivElement>(null)

  useEffect(() => {
    bottom.current?.scrollIntoView({ behavior: 'smooth' })
  }, [conversation?.messages.length, sending])

  const send = () => {
    const text = draft.trim()
    if (!text || sending) return
    setDraft('')
    onSend(text)
  }

  const llmReady = chatStatus?.llm_configured ?? false

  return (
    <>
      <div className="panel-header">
        <span>Conversation</span>
        <span className="spacer" style={{ flex: 1 }} />
        {chatStatus?.model ? <span className="faint mono">{chatStatus.model}</span> : null}
      </div>

      {error && <div className="banner">{error}</div>}

      <div className="messages">
        {!conversation || conversation.messages.length === 0 ? (
          <div className="empty">
            {llmReady ? (
              <>
                <p style={{ marginBottom: 8 }}>Ask about the architecture.</p>
                <p className="faint" style={{ fontSize: 12 }}>
                  &ldquo;I think our memory architecture is unnecessarily
                  complicated.&rdquo;
                </p>
              </>
            ) : (
              <p>
                No LLM configured. Set <span className="mono">LLM_BASE_URL</span> and{' '}
                <span className="mono">LLM_MODEL</span> in the backend&rsquo;s .env to
                enable conversation. Browsing documents still works.
              </p>
            )}
          </div>
        ) : (
          conversation.messages.map((m) => (
            <div key={m.id} className={`message ${m.role}`}>
              <div className="role">{m.role === 'user' ? 'You' : 'Architect'}</div>
              <div className="bubble">{m.content}</div>
              {m.role === 'assistant' && (
                <>
                  <Citations
                    message={m}
                    onOpen={onOpenFile}
                    onOpenQuestion={onOpenQuestion}
                    onOpenDecision={onOpenDecision}
                  />
                  {workspaceId &&
                    m.tool_calls &&
                    m.tool_calls.map((call, idx) => {
                      if (call.tool === 'draft_question') {
                        return (
                          <QuestionDraftCard
                            key={idx}
                            workspaceId={workspaceId}
                            args={call.arguments}
                            conversationId={conversation.id}
                            onSaved={onQuestionSaved}
                            onOpenQuestion={onOpenQuestion}
                          />
                        )
                      }
                      if (call.tool === 'draft_decision') {
                        return (
                          <DecisionDraftCard
                            key={idx}
                            workspaceId={workspaceId}
                            args={call.arguments}
                            onSaved={onDecisionSaved}
                            onOpenDecision={onOpenDecision}
                          />
                        )
                      }
                      return null
                    })}
                </>
              )}
            </div>
          ))
        )}
        {sending && (
          <div className="message assistant">
            <div className="role">Architect</div>
            <div className="bubble faint">Reading the workspace&hellip;</div>
          </div>
        )}
        <div ref={bottom} />
      </div>

      <div className="composer">
        <div className="btn-row" style={{ marginBottom: 8 }}>
          <div className="modes">
            {MODES.map((m) => (
              <button
                key={m.id}
                className={conversation?.mode === m.id ? 'active' : ''}
                onClick={() => onModeChange(m.id)}
                disabled={!conversation}
              >
                {m.label}
              </button>
            ))}
          </div>
          <span className="mode-note">
            {MODES.find((m) => m.id === conversation?.mode)?.note}
          </span>
        </div>

        <textarea
          value={draft}
          placeholder={
            llmReady
              ? 'Discuss the architecture...'
              : 'Conversation is unavailable until an LLM is configured.'
          }
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) {
              e.preventDefault()
              send()
            }
          }}
          disabled={!llmReady || sending}
        />
        <div className="composer-row">
          <button className="btn primary" onClick={send} disabled={!llmReady || sending || !draft.trim()}>
            {sending ? 'Thinking...' : 'Send'}
          </button>
          <span className="hint">Ctrl+Enter to send</span>
          <span className="spacer" style={{ flex: 1 }} />
          <span className="hint">
            Mode changes the AI&rsquo;s behaviour, not the conversation.
          </span>
        </div>
      </div>
    </>
  )
}
