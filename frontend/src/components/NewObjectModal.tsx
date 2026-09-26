import React, { useState } from 'react'

export type NewObjectType = 'document' | 'decision' | 'question' | 'conversation'

interface Props {
  isOpen: boolean
  onClose: () => void
  onCreateDocument: (title: string, path: string) => Promise<void>
  onCreateDecision: (title: string, context: string, decision: string) => Promise<void>
  onCreateQuestion: (question: string, context: string) => Promise<void>
  onCreateConversation: () => Promise<void>
  onAddRepo: () => void
}

export function NewObjectModal({
  isOpen,
  onClose,
  onCreateDocument,
  onCreateDecision,
  onCreateQuestion,
  onCreateConversation,
  onAddRepo,
}: Props) {
  const [objectType, setObjectType] = useState<NewObjectType>('document')
  const [title, setTitle] = useState('')
  const [path, setPath] = useState('')
  const [context, setContext] = useState('')
  const [decisionText, setDecisionText] = useState('')
  const [busy, setBusy] = useState(false)

  if (!isOpen) return null

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    setBusy(true)
    try {
      if (objectType === 'document') {
        const cleanPath = path.trim() || `docs/${title.toLowerCase().replace(/[^a-z0-9]+/g, '-') || 'untitled'}.md`
        await onCreateDocument(title.trim() || 'Untitled', cleanPath)
      } else if (objectType === 'decision') {
        await onCreateDecision(title.trim() || 'New Decision', context.trim(), decisionText.trim())
      } else if (objectType === 'question') {
        await onCreateQuestion(title.trim() || 'New Architectural Question', context.trim())
      } else if (objectType === 'conversation') {
        await onCreateConversation()
      }
      onClose()
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="modal-backdrop" onClick={onClose}>
      <div className="modal-dialog" style={{ width: 480 }} onClick={(e) => e.stopPropagation()}>
        <div className="modal-header">
          <div className="modal-title-row">
            <h3 style={{ margin: 0, fontSize: 16, fontWeight: 700 }}>+ New Object</h3>
            <button type="button" className="btn text-sm" onClick={onClose}>
              ✕
            </button>
          </div>
        </div>

        <form onSubmit={handleSubmit} style={{ padding: '16px 20px' }}>
          {/* Object Type Selector */}
          <div style={{ display: 'flex', gap: 6, marginBottom: 16 }}>
            <button
              type="button"
              className={`filter-chip ${objectType === 'document' ? 'active' : ''}`}
              onClick={() => setObjectType('document')}
            >
              📝 Note / Doc
            </button>
            <button
              type="button"
              className={`filter-chip ${objectType === 'decision' ? 'active' : ''}`}
              onClick={() => setObjectType('decision')}
            >
              ⚖️ ADR
            </button>
            <button
              type="button"
              className={`filter-chip ${objectType === 'question' ? 'active' : ''}`}
              onClick={() => setObjectType('question')}
            >
              ❓ Question
            </button>
            <button
              type="button"
              className={`filter-chip ${objectType === 'conversation' ? 'active' : ''}`}
              onClick={() => setObjectType('conversation')}
            >
              💬 Chat
            </button>
          </div>

          {objectType !== 'conversation' && (
            <div style={{ marginBottom: 14 }}>
              <label style={{ display: 'block', fontSize: 12, fontWeight: 600, color: 'var(--text-dim)', marginBottom: 4 }}>
                {objectType === 'question' ? 'Question Prompt' : 'Title'}
              </label>
              <input
                type="text"
                value={title}
                onChange={(e) => {
                  setTitle(e.target.value)
                  if (objectType === 'document' && !path) {
                    setPath(`docs/${e.target.value.toLowerCase().replace(/[^a-z0-9]+/g, '-')}.md`)
                  }
                }}
                placeholder={objectType === 'decision' ? 'e.g. Use SQLite for local storage' : 'Title of object...'}
                autoFocus
                required
              />
            </div>
          )}

          {objectType === 'document' && (
            <div style={{ marginBottom: 14 }}>
              <label style={{ display: 'block', fontSize: 12, fontWeight: 600, color: 'var(--text-dim)', marginBottom: 4 }}>
                Relative File Path
              </label>
              <input
                type="text"
                value={path}
                onChange={(e) => setPath(e.target.value)}
                placeholder="docs/architecture-overview.md"
              />
            </div>
          )}

          {objectType === 'decision' && (
            <>
              <div style={{ marginBottom: 14 }}>
                <label style={{ display: 'block', fontSize: 12, fontWeight: 600, color: 'var(--text-dim)', marginBottom: 4 }}>
                  Context / Problem
                </label>
                <textarea
                  rows={2}
                  value={context}
                  onChange={(e) => setContext(e.target.value)}
                  placeholder="Why is this decision needed?"
                />
              </div>
              <div style={{ marginBottom: 14 }}>
                <label style={{ display: 'block', fontSize: 12, fontWeight: 600, color: 'var(--text-dim)', marginBottom: 4 }}>
                  Decision / Solution
                </label>
                <textarea
                  rows={3}
                  value={decisionText}
                  onChange={(e) => setDecisionText(e.target.value)}
                  placeholder="What is the chosen design or architecture?"
                />
              </div>
            </>
          )}

          {objectType === 'question' && (
            <div style={{ marginBottom: 14 }}>
              <label style={{ display: 'block', fontSize: 12, fontWeight: 600, color: 'var(--text-dim)', marginBottom: 4 }}>
                Context / Notes
              </label>
              <textarea
                rows={3}
                value={context}
                onChange={(e) => setContext(e.target.value)}
                placeholder="Additional background context for this architectural question..."
              />
            </div>
          )}

          {objectType === 'conversation' && (
            <p style={{ fontSize: 13, color: 'var(--text-dim)', margin: '12px 0 20px' }}>
              Create a new AI conversation session to explore architectural questions, discover inconsistencies, or draft proposals.
            </p>
          )}

          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginTop: 20 }}>
            <button
              type="button"
              className="btn text-sm"
              onClick={() => {
                onClose()
                onAddRepo()
              }}
            >
              📁 Connect Folder / Repo
            </button>

            <div style={{ display: 'flex', gap: 8 }}>
              <button type="button" className="btn text-sm" onClick={onClose}>
                Cancel
              </button>
              <button type="submit" className="btn primary text-sm" disabled={busy}>
                {busy ? 'Creating…' : 'Create Object'}
              </button>
            </div>
          </div>
        </form>
      </div>
    </div>
  )
}
