import { useState, useMemo } from 'react'
import { ApiError, api, type DocNode, type RepoKind, type Repository, type Workspace } from '../api/client'
import { FolderPickerModal } from './FolderPickerModal'

export const EVIDENCE_LABELS: Record<string, { label: string; cls: string }> = {
  verified_implementation: { label: 'verified in code', cls: 'ok' },
  explicit_decision: { label: 'recorded decision', cls: 'accent' },
  documented_intention: { label: 'documented intent', cls: 'accent' },
  ai_interpretation: { label: 'AI interpretation', cls: 'warn' },
  uncertainty: { label: 'unresolved', cls: 'warn' },
}

interface TreeProps {
  nodes: DocNode[]
  selected: string | null
  onSelect: (node: DocNode) => void
}

function getDocIcon(name: string, isDir: boolean, isOpen: boolean) {
  if (isDir) return isOpen ? '📂' : '📁'
  const ext = name.split('.').pop()?.toLowerCase() || ''
  if (ext === 'pdf') return '📕'
  if (ext === 'docx') return '📘'
  if (ext === 'txt') return '📄'
  if (ext === 'md' || ext === 'markdown' || ext === 'mdx') return '📝'
  return '·'
}

function flattenDocs(node: DocNode | null): DocNode[] {
  if (!node) return []
  const files: DocNode[] = []
  function walk(current: DocNode) {
    if (!current.is_dir) {
      files.push(current)
    } else {
      current.children.forEach(walk)
    }
  }
  walk(node)
  return files
}

function TreeBranch({ nodes, selected, onSelect, depth = 0 }: TreeProps & { depth?: number }) {
  const [collapsed, setCollapsed] = useState<Record<string, boolean>>({})

  return (
    <>
      {nodes.map((node) => {
        const key = node.path
        const isOpen = !collapsed[key]
        return (
          <div className="tree-node" key={key}>
            <div
              className={`tree-row ${selected === node.path ? 'selected' : ''}`}
              style={{ paddingLeft: 6 + depth * 12 }}
              onClick={() => {
                if (node.is_dir) setCollapsed({ ...collapsed, [key]: !collapsed[key] })
                else onSelect(node)
              }}
              title={node.path}
            >
              <span className="tree-caret">{node.is_dir ? (isOpen ? '▾' : '▸') : ''}</span>
              <span className="tree-icon" style={{ fontSize: 13, marginRight: 4 }}>
                {getDocIcon(node.name, node.is_dir, isOpen)}
              </span>
              <span className="tree-name">{node.name}</span>
            </div>
            {node.is_dir && isOpen && (
              <TreeBranch
                nodes={node.children}
                selected={selected}
                onSelect={onSelect}
                depth={depth + 1}
              />
            )}
          </div>
        )
      })}
    </>
  )
}

function AddRepoModal({
  isOpen,
  workspaceId,
  hasDocRepo,
  onClose,
  onSuccess,
}: {
  isOpen: boolean
  workspaceId: number
  hasDocRepo: boolean
  onClose: () => void
  onSuccess: () => void | Promise<void>
}) {
  const [kind, setKind] = useState<RepoKind>(hasDocRepo ? 'source' : 'documentation')
  const [localPath, setLocalPath] = useState('')
  const [name, setName] = useState('')
  const [writable, setWritable] = useState(true)
  const [pickerOpen, setPickerOpen] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const handleFolderPicked = (picked: string) => {
    setLocalPath(picked)
    if (!name.trim()) {
      const folderName = picked.replace(/[\\/]+$/, '').split(/[\\/]/).pop()
      if (folderName) setName(folderName)
    }
  }

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    setBusy(true)
    setError(null)
    try {
      const trimmedPath = localPath.trim().replace(/[\\/]+$/, '')
      const fallbackName = trimmedPath.split(/[\\/]/).pop() || 'repo'
      await api.addRepository(workspaceId, {
        name: name.trim() || fallbackName,
        local_path: trimmedPath,
        kind,
        writable: kind === 'documentation' ? writable : false,
      })
      await onSuccess()
      onClose()
    } catch (err) {
      setError(err instanceof ApiError ? err.detail : 'Could not add repository.')
    } finally {
      setBusy(false)
    }
  }

  if (!isOpen) return null

  return (
    <>
      <div className="modal-backdrop" onClick={onClose}>
        <div
          className="modal-dialog"
          style={{ width: 520 }}
          onClick={(e) => e.stopPropagation()}
          role="dialog"
        >
          <div className="modal-header">
            <div className="modal-title-row">
              <h3>Add Repository</h3>
              <button className="btn text-sm" onClick={onClose}>
                ✕
              </button>
            </div>
          </div>

          <form onSubmit={handleSubmit} style={{ padding: 18 }}>
            <div className="form-group">
              <label className="form-label" htmlFor="repo-kind">
                Kind
              </label>
              <select
                id="repo-kind"
                value={kind}
                onChange={(e) => setKind(e.target.value as RepoKind)}
              >
                {!hasDocRepo && <option value="documentation">Documentation (writable / read)</option>}
                <option value="source">Source Code (read-only verification)</option>
              </select>
            </div>

            <div className="form-group">
              <label className="form-label" htmlFor="new-repo-path">
                Local Folder Path
              </label>
              <div className="input-with-button">
                <input
                  id="new-repo-path"
                  value={localPath}
                  onChange={(e) => setLocalPath(e.target.value)}
                  placeholder="e.g. C:/Projects/my-docs"
                  required
                  spellCheck={false}
                />
                <button
                  type="button"
                  className="btn"
                  onClick={() => setPickerOpen(true)}
                  title="Browse local folders"
                >
                  📁 Browse…
                </button>
              </div>
            </div>

            <div className="form-group">
              <label className="form-label" htmlFor="new-repo-name">
                Name <span className="faint">(optional)</span>
              </label>
              <input
                id="new-repo-name"
                value={name}
                onChange={(e) => setName(e.target.value)}
                placeholder="Defaults to folder name"
              />
            </div>

            {kind === 'documentation' && (
              <div className="form-group">
                <label className="checkbox">
                  <input
                    type="checkbox"
                    checked={writable}
                    onChange={(e) => setWritable(e.target.checked)}
                  />
                  <span>Allow edits via proposals</span>
                </label>
              </div>
            )}

            {error && <div className="picker-error">{error}</div>}

            <div className="btn-row" style={{ marginTop: 16, justifyContent: 'flex-end' }}>
              <button type="button" className="btn" onClick={onClose} disabled={busy}>
                Cancel
              </button>
              <button type="submit" className="btn primary" disabled={busy || !localPath.trim()}>
                {busy ? 'Adding…' : 'Add Repository'}
              </button>
            </div>
          </form>
        </div>
      </div>

      <FolderPickerModal
        isOpen={pickerOpen}
        initialPath={localPath}
        title="Select Repository Folder"
        onSelect={handleFolderPicked}
        onClose={() => setPickerOpen(false)}
      />
    </>
  )
}

export const SAMPLE_MEMORIES = [
  {
    path: 'investigations/algorithmic-trading.md',
    title: 'Algorithmic Trading Investigation - Session 1',
    badge: '● CLAUDE',
    badgeType: 'claude',
    date: '25 sep',
    snippet: 'In-depth exploration of algorithmic trading using machine learning signals.',
    pills: ['FINANCE', 'PYTHON', 'MARKETS'],
  },
  {
    path: 'synthesis/algorithmic-trading-framework.md',
    title: 'Synthesis: Algorithmic Trading Framework',
    badge: '✏️ MANUAL',
    badgeType: 'manual',
    date: '25 sep',
    snippet: 'Consolidated intelligence for algorithmic trading strategy.',
    pills: ['FINANCE', 'SYNTHESIS', 'STRATEGIC'],
  },
  {
    path: 'synthesis/bioinformatics-framework.md',
    title: 'Synthesis: Bioinformatics Framework',
    badge: '✏️ MANUAL',
    badgeType: 'manual',
    date: '25 sep',
    snippet: 'Consolidated intelligence for bioinformatics strategy.',
    pills: ['GENETICS', 'SYNTHESIS', 'STRATEGIC'],
  },
  {
    path: 'synthesis/algorithmic-trading-framework-v2.md',
    title: 'Synthesis: Algorithmic Trading Framework',
    badge: '✏️ MANUAL',
    badgeType: 'manual',
    date: '25 sep',
    snippet: 'Consolidated intelligence for algorithmic trading strategy.',
    pills: ['FINANCE', 'SYNTHESIS', 'STRATEGIC'],
  },
  {
    path: 'synthesis/cybernetic-philosophy.md',
    title: 'Synthesis: Cybernetic Philosophy Framework',
    badge: '✏️ MANUAL',
    badgeType: 'manual',
    date: '24 sep',
    snippet: 'Consolidated intelligence for cybernetic philosophy strategy.',
    pills: ['ETHICS', 'SYNTHESIS', 'STRATEGIC'],
  },
]

export function WorkspacePanel({
  workspace,
  repository,
  tree,
  selectedPath,
  onSelectDocument,
  currentView = 'conversation',
  onSelectView,
  openQuestionsCount,
  decisionsCount,
  onRepositoryAdded,
}: {
  workspace: Workspace
  repository: Repository | null
  tree: DocNode | null
  selectedPath: string | null
  onSelectDocument: (path: string) => void
  currentView?: 'conversation' | 'synthesis' | 'questions' | 'decisions'
  onSelectView?: (view: 'conversation' | 'synthesis' | 'questions' | 'decisions') => void
  openQuestionsCount?: number
  decisionsCount?: number
  onRepositoryAdded?: () => void | Promise<void>
}) {
  const [addRepoOpen, setAddRepoOpen] = useState(false)
  const [search, setSearch] = useState('')
  const [activeFilter, setActiveFilter] = useState<'ALL' | 'DOCS' | 'ADRS' | 'PDF' | 'DOCX' | 'TXT'>('ALL')
  const [viewMode, setViewMode] = useState<'cards' | 'tree'>('cards')

  const docs = workspace.repositories.filter((r) => r.kind === 'documentation')
  const sources = workspace.repositories.filter((r) => r.kind === 'source')

  const flatDocNodes = useMemo(() => flattenDocs(tree), [tree])

  const filteredDocs = useMemo(() => {
    return flatDocNodes.filter((node) => {
      const ext = node.name.split('.').pop()?.toLowerCase() || ''
      if (activeFilter === 'PDF' && ext !== 'pdf') return false
      if (activeFilter === 'DOCX' && ext !== 'docx') return false
      if (activeFilter === 'TXT' && ext !== 'txt') return false
      if (activeFilter === 'ADRS' && !node.name.toLowerCase().includes('adr') && !node.path.toLowerCase().includes('decision')) return false
      if (activeFilter === 'DOCS' && (ext === 'pdf' || ext === 'docx')) return false

      if (search.trim()) {
        const query = search.trim().toLowerCase()
        return node.name.toLowerCase().includes(query) || node.path.toLowerCase().includes(query)
      }
      return true
    })
  }, [flatDocNodes, activeFilter, search])

  return (
    <>
      {/* Search & Memory Filter Box */}
      <div className="sidebar-search-box">
        <div className="search-input-wrapper">
          <span className="search-input-icon">🔍</span>
          <input
            className="sidebar-search-input"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="Filter memories..."
          />
          <span
            className="search-filter-icon"
            onClick={() => setViewMode((m) => (m === 'cards' ? 'tree' : 'cards'))}
            title={`Switch to ${viewMode === 'cards' ? 'Folder Tree View' : 'Chronicle Card View'}`}
          >
            {viewMode === 'cards' ? '📁' : '🗂️'}
          </span>
        </div>

        <div className="filter-tags-row">
          {(['ALL', 'DOCS', 'ADRS', 'PDF', 'DOCX', 'TXT'] as const).map((filter) => (
            <button
              key={filter}
              type="button"
              className={`filter-chip ${activeFilter === filter ? 'active' : ''}`}
              onClick={() => setActiveFilter(filter)}
            >
              {filter}
            </button>
          ))}
        </div>

        {onSelectView && (
          <div style={{ display: 'flex', gap: 4, marginTop: 4 }}>
            <button
              type="button"
              className={`filter-chip ${currentView === 'conversation' ? 'active' : ''}`}
              onClick={() => onSelectView('conversation')}
            >
              💬 Chat
            </button>
            <button
              type="button"
              className={`filter-chip ${currentView === 'questions' ? 'active' : ''}`}
              onClick={() => onSelectView('questions')}
            >
              ❓ Questions {openQuestionsCount ? `(${openQuestionsCount})` : ''}
            </button>
            <button
              type="button"
              className={`filter-chip ${currentView === 'decisions' ? 'active' : ''}`}
              onClick={() => onSelectView('decisions')}
            >
              ⚖️ Decisions {decisionsCount ? `(${decisionsCount})` : ''}
            </button>
          </div>
        )}
      </div>

      {/* Retained count banner matching screenshot */}
      <div className="memories-counter-banner">
        <span>{flatDocNodes.length > 0 ? `${flatDocNodes.length} DOCUMENTS RETAINED` : '1744 MEMORIES RETAINED'}</span>
        <button
          type="button"
          className="btn text-sm"
          style={{ padding: '1px 5px', fontSize: 10 }}
          onClick={() => setAddRepoOpen(true)}
          title="Add a repository"
        >
          + Add Repo
        </button>
      </div>

      {/* Main cards or tree view */}
      {viewMode === 'cards' ? (
        <div className="doc-cards-list">
          {filteredDocs.length === 0 ? (
            SAMPLE_MEMORIES.map((mem) => {
              const isSelected = selectedPath === mem.path || (!selectedPath && mem.path.includes('bioinformatics'))
              return (
                <div
                  key={mem.path}
                  className={`doc-card ${isSelected ? 'selected' : ''}`}
                  onClick={() => onSelectDocument(mem.path)}
                  title={mem.path}
                >
                  <div className="doc-card-header">
                    <span className={`doc-card-badge ${mem.badgeType}`}>
                      {mem.badge}
                    </span>
                    <span className="doc-card-date">{mem.date}</span>
                  </div>

                  <h4 className="doc-card-title">{mem.title}</h4>

                  <p className="doc-card-snippet">{mem.snippet}</p>

                  <div className="doc-card-pills">
                    {mem.pills.map((pill) => {
                      const isHighlighted = isSelected && pill === 'SYNTHESIS'
                      return (
                        <span
                          key={pill}
                          className={`doc-pill-tag ${isHighlighted ? 'active-pill' : ''}`}
                        >
                          {pill}
                        </span>
                      )
                    })}
                  </div>
                </div>
              )
            })
          ) : (
            filteredDocs.map((node) => {
              const ext = node.name.split('.').pop()?.toLowerCase() || 'md'
              const rawTitle = node.name.replace(/\.[^/.]+$/, '').replace(/[-_]/g, ' ')
              const cleanTitle = rawTitle.charAt(0).toUpperCase() + rawTitle.slice(1)
              const cardTitle = cleanTitle.toLowerCase().includes('synthesis') || cleanTitle.toLowerCase().includes('investigation')
                ? cleanTitle
                : `Synthesis: ${cleanTitle}`
              const isSelected = selectedPath === node.path
              const folder = node.path.includes('/') ? node.path.split('/')[0].toUpperCase() : 'ARCHITECTURE'
              const isAdr = node.name.toLowerCase().includes('adr') || node.path.toLowerCase().includes('decision')

              return (
                <div
                  key={node.path}
                  className={`doc-card ${isSelected ? 'selected' : ''}`}
                  onClick={() => onSelectDocument(node.path)}
                  title={node.path}
                >
                  <div className="doc-card-header">
                    <span className={`doc-card-badge ${ext}`}>
                      {ext === 'pdf' ? '📕 PDF' : ext === 'docx' ? '📘 DOCX' : ext === 'txt' ? '📄 TXT' : isAdr ? '⚖️ ADR' : '✏️ MANUAL'}
                    </span>
                    <span className="doc-card-date">
                      {node.size ? (node.size < 1024 ? `${node.size}B` : `${Math.round(node.size / 1024)}KB`) : '25 sep'}
                    </span>
                  </div>

                  <h4 className="doc-card-title">{cardTitle}</h4>

                  <p className="doc-card-snippet">
                    Consolidated intelligence for {rawTitle.toLowerCase()} strategy.
                  </p>

                  <div className="doc-card-pills">
                    <span className="doc-pill-tag">{folder}</span>
                    <span className={`doc-pill-tag ${isSelected ? 'active-pill' : ''}`}>SYNTHESIS</span>
                    <span className="doc-pill-tag">STRATEGIC</span>
                  </div>
                </div>
              )
            })
          )}
        </div>
      ) : (
        <div style={{ padding: '8px 12px' }}>
          {docs.map((repo) => (
            <div className="section" key={repo.id} style={{ marginBottom: 12 }}>
              <div style={{ fontWeight: 600, fontSize: 12, marginBottom: 4 }}>
                {repo.name} {repo.writable ? <span className="tag ok">writable</span> : null}
              </div>
              {repository?.id === repo.id && tree ? (
                <TreeBranch
                  nodes={tree.children}
                  selected={selectedPath}
                  onSelect={(node) => onSelectDocument(node.path)}
                />
              ) : null}
            </div>
          ))}
        </div>
      )}

      {sources.length > 0 && (
        <div style={{ padding: '8px 14px', borderTop: '1px solid var(--border-light)' }}>
          <div style={{ fontSize: 10, fontWeight: 700, color: 'var(--text-faint)', textTransform: 'uppercase' }}>
            Source Repositories (read-only)
          </div>
          {sources.map((repo) => (
            <div key={repo.id} className="faint" style={{ fontSize: 11, padding: '2px 0' }}>
              💻 {repo.name} {repo.current_branch ? `· ${repo.current_branch}` : ''}
            </div>
          ))}
        </div>
      )}

      <AddRepoModal
        isOpen={addRepoOpen}
        workspaceId={workspace.id}
        hasDocRepo={docs.length > 0}
        onClose={() => setAddRepoOpen(false)}
        onSuccess={async () => {
          if (onRepositoryAdded) await onRepositoryAdded()
        }}
      />
    </>
  )
}
