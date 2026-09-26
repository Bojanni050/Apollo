import { useState } from 'react'
import type { Conversation, InventoryRun, Proposal, Repository } from '../api/client'
import { renderDiff } from '../markdown'
import { EVIDENCE_LABELS } from './WorkspacePanel'

type Tab = 'proposals' | 'inventory' | 'related'

export function ContextPanel({
  repository: _repository,
  documentPath: _documentPath,
  inventoryRun,
  proposals,
  conversations = [],
  onSelectConversation,
  busy,
  onRunInventory,
  onApplyInventoryAll,
  onApplyInventoryItem,
  onSkipInventoryItem,
  onAcceptProposal,
  onRejectProposal,
  onClose,
}: {
  repository: Repository | null
  documentPath: string | null
  documentMarkdown: string | null
  inventoryRun: InventoryRun | null
  proposals: Proposal[]
  conversations?: Conversation[]
  onSelectConversation?: (id: number) => void
  busy: boolean
  onRunInventory: () => void
  onApplyInventoryAll: () => void
  onApplyInventoryItem: (id: number) => void
  onSkipInventoryItem: (id: number) => void
  onAcceptProposal: (id: number) => void
  onRejectProposal: (id: number) => void
  onToggleRaw?: () => void
  showRaw?: boolean
  onClose?: () => void
}) {
  const [tab, setTab] = useState<Tab>(proposals.length > 0 ? 'proposals' : 'related')

  return (
    <>
      {/* Top Header matching Chronicle aesthetic */}
      <div className="right-panel-header">
        <h3 className="right-panel-title">
          <span>🕒</span>
          <span>Related Context</span>
        </h3>
        {onClose && (
          <button
            type="button"
            className="btn text-sm"
            onClick={onClose}
            title="Collapse context panel"
            style={{ padding: '2px 6px', fontSize: 13 }}
          >
            ✕
          </button>
        )}
      </div>

      {/* Active context bar */}
      <div className="active-filter-bar">
        <span className="active-filter-label">ACTIVE VIEW</span>
        <span className="active-filter-chip">
          {tab === 'related' ? `chats (${conversations.length})` : tab === 'proposals' ? `proposals (${proposals.length})` : 'inventory'}
        </span>
      </div>

      {/* Tab selection pills */}
      <div style={{ display: 'flex', gap: 6, padding: '10px 14px 4px' }}>
        <button
          type="button"
          className={`filter-chip ${tab === 'proposals' ? 'active' : ''}`}
          onClick={() => setTab('proposals')}
        >
          Proposals {proposals.length > 0 ? `(${proposals.length})` : ''}
        </button>
        <button
          type="button"
          className={`filter-chip ${tab === 'related' ? 'active' : ''}`}
          onClick={() => setTab('related')}
        >
          Chats {conversations.length > 0 ? `(${conversations.length})` : ''}
        </button>
        <button
          type="button"
          className={`filter-chip ${tab === 'inventory' ? 'active' : ''}`}
          onClick={() => setTab('inventory')}
        >
          Inventory
        </button>
      </div>

      <div className="panel-body">
        {/* Tab 1: Proposals in dashed warm amber cards */}
        {tab === 'proposals' && (
          <div>
            {proposals.length === 0 ? (
              <div className="faint" style={{ textAlign: 'center', padding: 24, fontSize: 13 }}>
                No active change proposals. Ask the assistant to prepare moves or edits.
              </div>
            ) : (
              proposals.map((p) => (
                <div key={p.id} className="dashed-card">
                  <div className="dashed-card-meta">
                    <span className="dashed-card-badge">
                      ✏️ {p.kind.toUpperCase()}
                    </span>
                    <span className={`tag ${p.status === 'accepted' ? 'ok' : p.status === 'rejected' ? 'danger' : 'accent'}`}>
                      {p.status}
                    </span>
                  </div>

                  <h4 className="dashed-card-title">{p.title}</h4>

                  {p.reason && <p className="dashed-card-desc">{p.reason}</p>}

                  {p.diff && (
                    <div style={{ marginTop: 6, maxHeight: 160, overflowY: 'auto' }}>
                      {renderDiff(p.diff)}
                    </div>
                  )}

                  {p.status === 'pending' && (
                    <div className="btn-row" style={{ marginTop: 8 }}>
                      <button className="btn primary" onClick={() => onAcceptProposal(p.id)} disabled={busy}>
                        Accept
                      </button>
                      <button className="btn" onClick={() => onRejectProposal(p.id)} disabled={busy}>
                        Reject
                      </button>
                    </div>
                  )}

                  <div className="dashed-card-tags">
                    <span className="dashed-tag">PROPOSAL</span>
                    <span className="dashed-tag">{p.kind.toUpperCase()}</span>
                    <span className="dashed-tag">DOCS</span>
                  </div>
                </div>
              ))
            )}
          </div>
        )}

        {/* Tab 2: Real Conversations in dashed cards matching Chronicle style */}
        {tab === 'related' && (
          <div>
            {conversations.length === 0 ? (
              <div className="faint" style={{ textAlign: 'center', padding: 24, fontSize: 13 }}>
                No active conversations yet. Start a discussion with the architect in the conversation panel.
              </div>
            ) : (
              conversations.map((c) => (
                <div
                  key={c.id}
                  className="dashed-card"
                  onClick={() => onSelectConversation && onSelectConversation(c.id)}
                  title="Open this conversation thread"
                >
                  <div className="dashed-card-meta">
                    <span className="dashed-card-badge">💬 CONVERSATION</span>
                    <span>{c.mode.toUpperCase()}</span>
                  </div>
                  <h4 className="dashed-card-title">{c.title}</h4>
                  <p className="dashed-card-desc">
                    Conversation thread with AI architect ({c.mode} mode)
                  </p>
                  <div className="dashed-card-tags">
                    <span className="dashed-tag">CHAT</span>
                    <span className="dashed-tag">{c.mode.toUpperCase()}</span>
                    <span className="dashed-tag">WORKSPACE</span>
                  </div>
                </div>
              ))
            )}
          </div>
        )}

        {/* Tab 3: Inventory */}
        {tab === 'inventory' && (
          <div>
            {!inventoryRun ? (
              <div style={{ textAlign: 'center', padding: '24px 10px' }}>
                <p className="faint" style={{ fontSize: 12, marginBottom: 12 }}>
                  The AI reads every document in the repository and suggests where it belongs.
                </p>
                <button className="btn primary" onClick={onRunInventory} disabled={busy}>
                  {busy ? 'Scanning…' : 'Run Inventory'}
                </button>
              </div>
            ) : (
              <div>
                <div className="btn-row" style={{ marginBottom: 10, justifyContent: 'space-between' }}>
                  <button className="btn text-sm" onClick={onRunInventory} disabled={busy}>
                    Re-run
                  </button>
                  <button
                    className="btn primary text-sm"
                    onClick={onApplyInventoryAll}
                    disabled={busy || inventoryRun.items.filter((i) => i.decision === 'pending' && !i.ambiguous).length === 0}
                  >
                    Approve All
                  </button>
                </div>

                {inventoryRun.items.map((item) => {
                  const evidence = EVIDENCE_LABELS[item.ambiguous ? 'uncertainty' : 'documented_intention']
                  return (
                    <div key={item.id} className="dashed-card">
                      <div className="dashed-card-meta">
                        <span className="dashed-card-badge">
                          {item.needs_move ? '📦 MOVE' : '✓ OK'}
                        </span>
                        <span className={`tag ${evidence.cls}`}>
                          {item.ambiguous ? 'needs review' : `conf ${(item.confidence ?? 0).toFixed(2)}`}
                        </span>
                      </div>

                      <h4 className="dashed-card-title">{item.source_path}</h4>

                      {item.needs_move && item.suggested_path && (
                        <div style={{ fontSize: 11, color: 'var(--amber)', fontWeight: 600 }}>
                          ➔ move to {item.target_path}
                        </div>
                      )}

                      <p className="dashed-card-desc">{item.purpose}</p>

                      {item.decision === 'pending' && (
                        <div className="btn-row" style={{ marginTop: 6 }}>
                          <button
                            className="btn primary text-sm"
                            onClick={() => onApplyInventoryItem(item.id)}
                            disabled={busy || item.ambiguous}
                          >
                            Apply
                          </button>
                          <button
                            className="btn text-sm"
                            onClick={() => onSkipInventoryItem(item.id)}
                            disabled={busy}
                          >
                            Skip
                          </button>
                        </div>
                      )}

                      <div className="dashed-card-tags">
                        <span className="dashed-tag">INVENTORY</span>
                        {item.ambiguous && <span className="dashed-tag">AMBIGUOUS</span>}
                      </div>
                    </div>
                  )
                })}
              </div>
            )}
          </div>
        )}
      </div>
    </>
  )
}
