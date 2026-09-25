import { useEffect, useRef, useState } from 'react'
import type { ChatStatus, ConversationDetail, Message, Mode } from '../api/client'

const MODES: { id: Mode; label: string; note: string }[] = [
  { id: 'explore', label: 'Explore', note: 'Free discussion. The AI may read, but not change anything.' },
  { id: 'investigate', label: 'Investigate', note: 'The AI searches the workspace and cites the files it used.' },
  { id: 'apply', label: 'Apply', note: 'Prepare documentation changes for your approval. Nothing is written without you.' },
]

function Citations({ message, onOpen }: { message: Message; onOpen: (path: string) => void }) {
  if (!message.citations || message.citations.length === 0) return null
  return (
    <div style={{ marginTop: 12 }}>
      <div className="faint" style={{ fontSize: 11, marginBottom: 6, letterSpacing: '0.05em' }}>
        SOURCES
      </div>
      {message.citations.map((c, i) => (
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
      ))}
    </div>
  )
}

export function ConversationPanel({
  conversation,
  chatStatus,
  sending,
  error,
  onSend,
  onModeChange,
  onOpenFile,
}: {
  conversation: ConversationDetail | null
  chatStatus: ChatStatus | null
  sending: boolean
  error: string | null
  onSend: (text: string) => void
  onModeChange: (mode: Mode) => void
  onOpenFile: (path: string) => void
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
              {m.role === 'assistant' && <Citations message={m} onOpen={onOpenFile} />}
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
