import { useState } from 'react'
import type { InventoryRun, Proposal, Repository } from '../api/client'
import { renderDiff } from '../markdown'
import { EVIDENCE_LABELS } from './WorkspacePanel'

type Tab = 'proposals' | 'inventory' | 'related'

export function ContextPanel({
  repository,
  documentPath,
  inventoryRun,
  proposals,
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
  const [tab, setTab] = useState<Tab>('related')
  const [activeFilter, setActiveFilter] = useState('synthesis')

  const repoName = repository?.name || 'docs'
  const docTitle = documentPath
    ? documentPath.replace(/[\\/]/g, '/').split('/').pop()?.replace(/\.[^/.]+$/, '').replace(/[-_]/g, ' ') || 'Document'
    : `${repoName} Architecture`

  return (
    <>
      {/* Top Header matching screenshot */}
      <div className="right-panel-header">
        <h3 className="right-panel-title">
          <span>🕒</span>
          <span>Related Chats</span>
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

      {/* Active filters bar matching screenshot */}
      <div className="active-filter-bar">
        <span className="active-filter-label">ACTIVE FILTERS</span>
        {activeFilter && (
          <span className="active-filter-chip">
            {activeFilter}
            <button type="button" onClick={() => setActiveFilter('')} title="Clear filter">
              ✕
            </button>
          </span>
        )}
      </div>

      {/* Tab selection pills */}
      <div style={{ display: 'flex', gap: 6, padding: '10px 14px 4px' }}>
        <button
          type="button"
          className={`filter-chip ${tab === 'related' ? 'active' : ''}`}
          onClick={() => setTab('related')}
        >
          Related Chats
        </button>
        <button
          type="button"
          className={`filter-chip ${tab === 'proposals' ? 'active' : ''}`}
          onClick={() => setTab('proposals')}
        >
          Proposals {proposals.length > 0 ? `(${proposals.length})` : ''}
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
        {/* Tab 1: Related Chats in dashed cards matching screenshot */}
        {tab === 'related' && (
          <div>
            <div className="dashed-card">
              <div className="dashed-card-meta">
                <span className="dashed-card-badge">✏️ MANUAL</span>
                <span>25 sep</span>
              </div>
              <h4 className="dashed-card-title">Synthesis: Cybernetic Philosophy Framework</h4>
              <p className="dashed-card-desc">
                Consolidated intelligence for cybernetic philosophy strategy.
              </p>
              <div className="dashed-card-tags">
                <span className="dashed-tag">ETHICS</span>
                <span className="dashed-tag">SYNTHESIS</span>
                <span className="dashed-tag">STRATEGIC</span>
              </div>
            </div>

            <div className="dashed-card">
              <div className="dashed-card-meta">
                <span className="dashed-card-badge">✏️ MANUAL</span>
                <span>27 sep</span>
              </div>
              <h4 className="dashed-card-title">Synthesis: {docTitle ? `${docTitle} Framework` : 'Bioinformatics Framework'}</h4>
              <p className="dashed-card-desc">
                Consolidated intelligence for bioinformatics strategy.
              </p>
              <div className="dashed-card-tags">
                <span className="dashed-tag">GENETICS</span>
                <span className="dashed-tag">SYNTHESIS</span>
                <span className="dashed-tag">STRATEGIC</span>
              </div>
            </div>

            <div className="dashed-card">
              <div className="dashed-card-meta">
                <span className="dashed-card-badge">✏️ MANUAL</span>
                <span>6 oct</span>
              </div>
              <h4 className="dashed-card-title">Synthesis: Bioinformatics Framework</h4>
              <p className="dashed-card-desc">
                Consolidated intelligence for bioinformatics strategy.
              </p>
              <div className="dashed-card-tags">
                <span className="dashed-tag">GENETICS</span>
                <span className="dashed-tag">SYNTHESIS</span>
                <span className="dashed-tag">STRATEGIC</span>
              </div>
            </div>

            <div className="dashed-card">
              <div className="dashed-card-meta">
                <span className="dashed-card-badge">✏️ MANUAL</span>
                <span>25 sep</span>
              </div>
              <h4 className="dashed-card-title">Synthesis: Cybernetic Philosophy Framework</h4>
              <p className="dashed-card-desc">
                Consolidated intelligence for cybernetic philosophy strategy.
              </p>
              <div className="dashed-card-tags">
                <span className="dashed-tag">ETHICS</span>
                <span className="dashed-tag">SYNTHESIS</span>
                <span className="dashed-tag">STRATEGIC</span>
              </div>
            </div>

            <div className="dashed-card">
              <div className="dashed-card-meta">
                <span className="dashed-card-badge">✏️ MANUAL</span>
                <span>24 oct</span>
              </div>
              <h4 className="dashed-card-title">Synthesis: Modern Stoicism Framework</h4>
              <p className="dashed-card-desc">
                Consolidated intelligence for modern stoicism strategy.
              </p>
              <div className="dashed-card-tags">
                <span className="dashed-tag">MINDSET</span>
                <span className="dashed-tag">SYNTHESIS</span>
                <span className="dashed-tag">STRATEGIC</span>
              </div>
            </div>
          </div>
        )}

        {/* Tab 2: Proposals in dashed warm amber cards */}
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
