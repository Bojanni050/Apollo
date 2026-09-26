import { useState, type FormEvent } from 'react'
import type {
  Conversation,
  ConversationDetail,
  InventoryRun,
  Mode,
  Proposal,
  PulseRun,
  Repository,
} from '../api/client'
import { renderDiff } from '../markdown'

interface Props {
  isOpen: boolean
  onClose: () => void
  repository: Repository | null
  documentPath: string | null
  documentMarkdown: string | null
  inventoryRun: InventoryRun | null
  proposals: Proposal[]
  conversations: Conversation[]
  activeConversation: ConversationDetail | null
  onSendMessage: (text: string) => Promise<void>
  onModeChange: (mode: Mode) => Promise<void>
  sending: boolean
  busy: boolean
  onAcceptProposal: (id: number) => void
  onRejectProposal: (id: number) => void
  onApplyInventoryAll: () => void
  onApplyInventoryItem: (id: number) => void
  onSkipInventoryItem: (id: number) => void
  pulseRun: PulseRun | null
  onApplyPulseAll: () => void
  onApplyPulseItem: (id: number) => void
  onSkipPulseItem: (id: number) => void
}

type Tab = 'related' | 'chat' | 'proposals'

export function ContextSidebar({
  isOpen,
  onClose,
  repository: _repository,
  documentPath: _documentPath,
  documentMarkdown: _documentMarkdown,
  inventoryRun,
  proposals,
  conversations: _conversations,
  activeConversation,
  onSendMessage,
  onModeChange,
  sending,
  busy,
  onAcceptProposal,
  onRejectProposal,
  onApplyInventoryAll,
  onApplyInventoryItem,
  onSkipInventoryItem,
  pulseRun,
  onApplyPulseAll,
  onApplyPulseItem,
  onSkipPulseItem,
}: Props) {
  const [tab, setTab] = useState<Tab>('related')
  const [chatInput, setChatInput] = useState('')

  const handleSend = async (e: FormEvent) => {
    e.preventDefault()
    if (!chatInput.trim() || sending) return
    const text = chatInput.trim()
    setChatInput('')
    await onSendMessage(text)
  }

  return (
    <aside className={`context-sidebar ${isOpen ? 'open' : 'closed'}`}>
      <div className="context-sidebar-content">
        {/* 1. Header Bar matching screenshot */}
        <div className="context-header">
          <div className="context-title-block">
            <span className="context-title-hash">#</span>
            <h3 className="context-title">AI weave</h3>
          </div>

          <div className="context-header-actions">
            <button
              type="button"
              className="context-tab-mini"
              onClick={() => setTab(tab === 'chat' ? 'related' : 'chat')}
              title={tab === 'chat' ? 'Switch to Related Objects' : 'Open AI Chat'}
            >
              {tab === 'chat' ? 'Related' : 'Chat'}
            </button>
            <button
              type="button"
              className="context-close-btn"
              onClick={onClose}
              title="Collapse context sidebar"
            >
              ✕
            </button>
          </div>
        </div>

        {/* 2. Sub Tabs */}
        <div className="context-nav-tabs">
          <button
            type="button"
            className={`context-tab-chip ${tab === 'related' ? 'active' : ''}`}
            onClick={() => setTab('related')}
          >
            Related Objects
          </button>
          <button
            type="button"
            className={`context-tab-chip ${tab === 'chat' ? 'active' : ''}`}
            onClick={() => setTab('chat')}
          >
            AI Chat {activeConversation ? `(${activeConversation.messages.length})` : ''}
          </button>
          <button
            type="button"
            className={`context-tab-chip ${tab === 'proposals' ? 'active' : ''}`}
            onClick={() => setTab('proposals')}
          >
            Proposals {proposals.length > 0 ? `(${proposals.length})` : ''}
          </button>
        </div>

        {/* 3. Tab: Related Objects (matching screenshot right column) */}
        {tab === 'related' && (
          <div className="context-scroll-body">
            <div className="context-section-label">RELATED OBJECTS</div>

            {/* Card 1: Capacities is great */}
            <div className="context-object-card">
              <div className="context-card-header">
                <span className="context-card-title">Capacities is great</span>
                <span className="context-card-badge note">note</span>
              </div>
              <p className="context-card-body">
                Explicitly cited source that discusses the object-based structure of Capacities, which is central to the conflict described.
              </p>
            </div>

            {/* Card 2: Steph Ango */}
            <div className="context-object-card">
              <div className="context-card-header">
                <span className="context-card-title">Steph Ango</span>
                <span className="context-card-badge person">person</span>
              </div>
              <p className="context-card-body">
                Explicitly cited source and central figure in the &apos;file-over-app&apos; philosophy discussed as a point of friction.
              </p>
            </div>

            {/* Card 3: Apollo Architecture Knowledge */}
            <div className="context-object-card">
              <div className="context-card-header">
                <span className="context-card-title">Architecture Specs</span>
                <span className="context-card-badge spec">spec</span>
              </div>
              <p className="context-card-body">
                Repository documentation graph connecting decisions, verified code implementations, and open questions.
              </p>
            </div>
          </div>
        )}

        {/* 4. Tab: AI Chat */}
        {tab === 'chat' && (
          <div className="context-chat-body">
            {/* Mode selection row */}
            <div className="context-mode-bar">
              <span className="context-mode-label">Mode:</span>
              {(['explore', 'investigate', 'apply'] as const).map((m) => (
                <button
                  key={m}
                  type="button"
                  className={`context-mode-chip ${activeConversation?.mode === m ? 'active' : ''}`}
                  onClick={() => onModeChange(m)}
                >
                  {m}
                </button>
              ))}
            </div>

            {/* Chat messages */}
            <div className="context-messages-list">
              {activeConversation?.messages.length === 0 ? (
                <div className="context-chat-empty">
                  <div className="empty-sparkle">✨</div>
                  <p>Ask anything about this document, its citations, or request architectural enhancements.</p>
                </div>
              ) : (
                activeConversation?.messages.map((m) => (
                  <div key={m.id} className={`context-msg ${m.role}`}>
                    <div className="msg-sender">
                      {m.role === 'assistant' ? '✦ AI weave' : 'You'}
                    </div>
                    <div className="msg-content">{m.content}</div>
                  </div>
                ))
              )}
            </div>

            {/* Chat input box */}
            <form className="context-chat-form" onSubmit={handleSend}>
              <input
                type="text"
                className="context-chat-input"
                placeholder="Ask AI weave..."
                value={chatInput}
                onChange={(e) => setChatInput(e.target.value)}
                disabled={sending}
              />
              <button
                type="submit"
                className="context-chat-send-btn"
                disabled={sending || !chatInput.trim()}
              >
                {sending ? '…' : '↑'}
              </button>
            </form>
          </div>
        )}

        {/* 5. Tab: Proposals & Inventory */}
        {tab === 'proposals' && (
          <div className="context-scroll-body">
            <div className="context-section-label">AI PROPOSALS ({proposals.length})</div>

            {proposals.length === 0 ? (
              <p className="context-empty-hint">No active proposals for this workspace.</p>
            ) : (
              proposals.map((p) => {
                const target = p.changes?.[0]?.target_path || p.kind
                return (
                  <div key={p.id} className="context-proposal-card">
                    <div className="proposal-title">{p.title}</div>
                    <div className="proposal-path">{target}</div>
                    <div className="proposal-diff">
                      {p.diff ? renderDiff(p.diff) : <span style={{ color: 'var(--text-faint)' }}>{p.reason}</span>}
                    </div>
                    <div className="proposal-actions">
                      <button
                        type="button"
                        className="proposal-btn accept"
                        onClick={() => onAcceptProposal(p.id)}
                        disabled={busy}
                      >
                        Accept
                      </button>
                      <button
                        type="button"
                        className="proposal-btn reject"
                        onClick={() => onRejectProposal(p.id)}
                        disabled={busy}
                      >
                        Reject
                      </button>
                    </div>
                  </div>
                )
              })
            )}

            {inventoryRun && (
              <div style={{ marginTop: 20 }}>
                <div className="context-section-label">INVENTORY RUN #{inventoryRun.id}</div>
                <div style={{ display: 'flex', gap: 6, margin: '8px 0 12px' }}>
                  <button
                    type="button"
                    className="proposal-btn accept"
                    onClick={onApplyInventoryAll}
                    disabled={busy}
                  >
                    Apply All Changes
                  </button>
                </div>
                {inventoryRun.items.map((it) => (
                  <div key={it.id} className="context-inventory-item">
                    <div className="item-name">{it.source_path}</div>
                    <div className="item-status">{it.decision}</div>
                    <div style={{ display: 'flex', gap: 4, marginTop: 4 }}>
                      <button
                        type="button"
                        className="btn text-sm"
                        style={{ fontSize: 11, padding: '2px 6px' }}
                        onClick={() => onApplyInventoryItem(it.id)}
                        disabled={busy || it.decision !== 'pending'}
                      >
                        Apply
                      </button>
                      <button
                        type="button"
                        className="btn text-sm"
                        style={{ fontSize: 11, padding: '2px 6px' }}
                        onClick={() => onSkipInventoryItem(it.id)}
                        disabled={busy || it.decision !== 'pending'}
                      >
                        Skip
                      </button>
                    </div>
                  </div>
                ))}
              </div>
            )}
            {pulseRun && (
              <div style={{ marginTop: 20 }}>
                <div className="context-section-label">AI PULSE RUN #{pulseRun.id}</div>
                {pulseRun.summary && (
                  <div className="item-status" style={{ margin: '6px 0 8px' }}>
                    {pulseRun.summary}
                  </div>
                )}
                {pulseRun.mode === 'suggest' &&
                  pulseRun.items.some((it) => it.decision === 'pending') && (
                    <div style={{ display: 'flex', gap: 6, margin: '8px 0 12px' }}>
                      <button
                        type="button"
                        className="proposal-btn accept"
                        onClick={onApplyPulseAll}
                        disabled={busy}
                      >
                        Apply All Suggestions
                      </button>
                    </div>
                  )}
                {pulseRun.items.map((it) => (
                  <div key={it.id} className="context-inventory-item">
                    <div className="item-name">{it.file_path}</div>
                    {it.tags.length > 0 && (
                      <div className="item-status">
                        tags: {it.tags.join(', ')}
                      </div>
                    )}
                    {it.connections.map((c, idx) => (
                      <div key={idx} className="item-status">
                        {c.relation} → {c.path}
                        {c.why ? ` (${c.why})` : ''}
                      </div>
                    ))}
                    <div className="item-status">{it.decision}</div>
                    {it.decision === 'pending' && (
                      <div style={{ display: 'flex', gap: 4, marginTop: 4 }}>
                        <button
                          type="button"
                          className="btn text-sm"
                          style={{ fontSize: 11, padding: '2px 6px' }}
                          onClick={() => onApplyPulseItem(it.id)}
                          disabled={busy}
                        >
                          Apply
                        </button>
                        <button
                          type="button"
                          className="btn text-sm"
                          style={{ fontSize: 11, padding: '2px 6px' }}
                          onClick={() => onSkipPulseItem(it.id)}
                          disabled={busy}
                        >
                          Skip
                        </button>
                      </div>
                    )}
                  </div>
                ))}
              </div>
            )}
          </div>
        )}
      </div>
    </aside>
  )
}
