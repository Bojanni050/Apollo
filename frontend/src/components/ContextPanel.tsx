import { useState } from 'react'
import type { InventoryRun, Proposal, Repository } from '../api/client'
import { renderDiff, renderMarkdown } from '../markdown'
import { EVIDENCE_LABELS } from './WorkspacePanel'

type Tab = 'document' | 'inventory' | 'proposals'

function DocumentView({
  repository,
  path,
  markdown,
  onToggleRaw,
  showRaw,
}: {
  repository: Repository | null
  path: string | null
  markdown: string | null
  onToggleRaw: () => void
  showRaw: boolean
}) {
  if (!path) return <div className="empty">Select a document to read it here.</div>
  if (markdown === null) return <div className="empty">Loading&hellip;</div>
  return (
    <div className="section">
      <div className="btn-row" style={{ marginBottom: 8 }}>
        <span className="mono faint" style={{ flex: 1, wordBreak: 'break-all' }}>
          {repository?.name}/{path}
        </span>
        <button className="btn" onClick={onToggleRaw}>
          {showRaw ? 'Rendered' : 'Raw'}
        </button>
      </div>
      {showRaw ? <pre className="code">{markdown}</pre> : renderMarkdown(markdown)}
    </div>
  )
}

function InventoryView({
  run,
  busy,
  onRun,
  onApplyAll,
  onApplyItem,
  onSkipItem,
}: {
  run: InventoryRun | null
  busy: boolean
  onRun: () => void
  onApplyAll: () => void
  onApplyItem: (id: number) => void
  onSkipItem: (id: number) => void
}) {
  if (!run) {
    return (
      <div className="section">
        <p className="faint" style={{ fontSize: 12, marginTop: 0 }}>
          The AI reads every document and suggests where it belongs. Nothing moves
          until you approve it.
        </p>
        <button className="btn primary" onClick={onRun} disabled={busy}>
          {busy ? 'Reading documents...' : 'Run inventory'}
        </button>
      </div>
    )
  }

  if (!run) {
    return (
      <div className="section">
        <p className="faint" style={{ fontSize: 12, marginTop: 0 }}>
          The AI reads every document and suggests where it belongs. Nothing moves
          until you approve it.
        </p>
        <button className="btn primary" onClick={onRun} disabled={busy}>
          {busy ? 'Reading documents...' : 'Run inventory'}
        </button>
      </div>
    )
  }

  const pending = run.items.filter((i) => i.decision === 'pending')
  const actionable = pending.filter((i) => !i.ambiguous)
  const ambiguousCount = run.items.filter((i) => i.ambiguous).length

  return (
    <>
      <div className="section">
        <div className="btn-row" style={{ marginBottom: 8 }}>
          <button className="btn" onClick={onRun} disabled={busy}>
            Re-run
          </button>
          <button className="btn primary" onClick={onApplyAll} disabled={busy || actionable.length === 0}>
            Approve all ({actionable.length})
          </button>
        </div>
        {ambiguousCount > 0 && (
          <p className="faint" style={{ fontSize: 12, margin: 0 }}>
            {ambiguousCount} document{ambiguousCount === 1 ? '' : 's'} could not be placed
            confidently. These are never moved automatically &mdash; they are left for you.
          </p>
        )}
        {run.summary && <p className="dim" style={{ fontSize: 12 }}>{run.summary}</p>}
      </div>

      <div className="section">
        {run.items.map((item) => {
          const evidence = EVIDENCE_LABELS[item.ambiguous ? 'uncertainty' : 'documented_intention']
          return (
            <div
              key={item.id}
              className={`inventory-item ${item.ambiguous ? 'ambiguous' : ''} ${
                item.decision === 'applied' ? 'applied' : ''
              }`}
            >
              <div className="inventory-path">{item.source_path}</div>
              {item.needs_move && item.suggested_path ? (
                <div className="inventory-move">
                  <span className="faint">move to</span>
                  <span style={{ color: 'var(--accent)' }}>{item.target_path}</span>
                </div>
              ) : (
                <div className="list-sub">already in place</div>
              )}
              <p className="dim" style={{ fontSize: 12, margin: '6px 0 4px' }}>
                {item.purpose}
              </p>
              {item.note && <p className="faint" style={{ fontSize: 11, margin: '0 0 4px' }}>{item.note}</p>}
              <div className="btn-row" style={{ marginTop: 6, alignItems: 'center' }}>
                <span className={`tag ${evidence.cls}`}>
                  {item.ambiguous ? 'needs a human' : `conf ${(item.confidence ?? 0).toFixed(2)}`}
                </span>
                {item.decision === 'pending' ? (
                  <>
                    <button
                      className="btn"
                      onClick={() => onApplyItem(item.id)}
                      disabled={busy || item.ambiguous}
                      title={item.ambiguous ? 'Cannot be applied automatically' : 'Move this document'}
                    >
                      Apply
                    </button>
                    <button className="btn danger" onClick={() => onSkipItem(item.id)} disabled={busy}>
                      Skip
                    </button>
                  </>
                ) : (
                  <span className={`tag ${item.decision === 'applied' ? 'ok' : ''}`}>
                    {item.decision}
                  </span>
                )}
              </div>
            </div>
          )
        })}
      </div>
    </>
  )
}

function ProposalsView({
  proposals,
  busy,
  onAccept,
  onReject,
}: {
  proposals: Proposal[]
  busy: boolean
  onAccept: (id: number) => void
  onReject: (id: number) => void
}) {
  if (proposals.length === 0) return <div className="empty">No proposals yet.</div>
  return (
    <div className="section">
      {proposals.map((p) => (
        <div key={p.id} className="inventory-item">
          <div className="btn-row" style={{ marginBottom: 4 }}>
            <span className="tag">{p.kind}</span>
            <span
              className={`tag ${p.status === 'accepted' ? 'ok' : p.status === 'rejected' ? 'danger' : 'warn'}`}
            >
              {p.status}
            </span>
          </div>
          <div className="list-title">{p.title}</div>
          {p.reason && <p className="dim" style={{ fontSize: 12 }}>{p.reason}</p>}
          {p.diff && renderDiff(p.diff)}
          {p.expected_consequences && (
            <p className="faint" style={{ fontSize: 11 }}>{p.expected_consequences}</p>
          )}
          {p.status === 'pending' && (
            <div className="btn-row" style={{ marginTop: 6 }}>
              <button className="btn primary" onClick={() => onAccept(p.id)} disabled={busy}>
                Accept
              </button>
              <button className="btn danger" onClick={() => onReject(p.id)} disabled={busy}>
                Reject
              </button>
            </div>
          )}
        </div>
      ))}
    </div>
  )
}

export function ContextPanel({
  repository,
  documentPath,
  documentMarkdown,
  inventoryRun,
  proposals,
  busy,
  onRunInventory,
  onApplyInventoryAll,
  onApplyInventoryItem,
  onSkipInventoryItem,
  onAcceptProposal,
  onRejectProposal,
  onToggleRaw,
  showRaw,
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
  onToggleRaw: () => void
  showRaw: boolean
}) {
  const [tab, setTab] = useState<Tab>('document')

  return (
    <>
      <div className="panel-header">
        {(['document', 'inventory', 'proposals'] as Tab[]).map((t) => (
          <button
            key={t}
            className={tab === t ? 'btn primary' : 'btn'}
            onClick={() => setTab(t)}
            style={{ textTransform: 'capitalize' }}
          >
            {t}
          </button>
        ))}
      </div>

      <div className="panel-body tight">
        {tab === 'document' && (
          <DocumentView
            repository={repository}
            path={documentPath}
            markdown={documentMarkdown}
            onToggleRaw={onToggleRaw}
            showRaw={showRaw}
          />
        )}
        {tab === 'inventory' && (
          <InventoryView
            run={inventoryRun}
            busy={busy}
            onRun={onRunInventory}
            onApplyAll={onApplyInventoryAll}
            onApplyItem={onApplyInventoryItem}
            onSkipItem={onSkipInventoryItem}
          />
        )}
        {tab === 'proposals' && (
          <ProposalsView
            proposals={proposals}
            busy={busy}
            onAccept={onAcceptProposal}
            onReject={onRejectProposal}
          />
        )}
      </div>
    </>
  )
}
