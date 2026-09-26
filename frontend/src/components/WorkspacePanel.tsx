import { useState, useMemo } from 'react'
import {
  ApiError,
  api,
  type DocNode,
  type ManifestPreview,
  type RepoKind,
  type Repository,
  type SourceType,
  type Workspace,
} from '../api/client'
import { FolderPickerModal } from './FolderPickerModal'

const SOURCE_STATUS_LABELS: Record<string, { label: string; cls: string }> = {
  pending: { label: 'pending sync', cls: 'warn' },
  ready: { label: 'ready', cls: 'ok' },
  error: { label: 'error', cls: 'warn' },
  missing: { label: 'missing', cls: 'warn' },
}

const SOURCE_TYPE_LABELS: Record<string, string> = {
  local: 'Local',
  github: 'GitHub',
}

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

function AddSourceModal({
  isOpen,
  workspaceId,
  onClose,
  onSuccess,
}: {
  isOpen: boolean
  workspaceId: number
  onClose: () => void
  onSuccess: () => void | Promise<void>
}) {
  const [sourceType, setSourceType] = useState<SourceType>('local')
  const [location, setLocation] = useState('')
  const [name, setName] = useState('')
  const [branch, setBranch] = useState('')
  const [pickerOpen, setPickerOpen] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const handleFolderPicked = (picked: string) => {
    setLocation(picked)
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
      await api.addSource(workspaceId, {
        name: name.trim() || location.trim().split(/[\\/]/).pop() || 'source',
        location: location.trim(),
        source_type: sourceType,
        branch: branch.trim() || null,
      })
      await onSuccess()
      onClose()
    } catch (err) {
      setError(err instanceof ApiError ? err.detail : 'Could not add source.')
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
              <h3>Add Source Repository</h3>
              <button className="btn text-sm" onClick={onClose}>
                ✕
              </button>
            </div>
          </div>

          <form onSubmit={handleSubmit} style={{ padding: 18 }}>
            <div className="form-group">
              <label className="form-label" htmlFor="source-type">
                Type
              </label>
              <select
                id="source-type"
                value={sourceType}
                onChange={(e) => setSourceType(e.target.value as SourceType)}
              >
                <option value="local">Local Git repository</option>
                <option value="github">GitHub repository (read-only)</option>
              </select>
            </div>

            <div className="form-group">
              <label className="form-label" htmlFor="source-location">
                {sourceType === 'github' ? 'Repository URL' : 'Local Folder Path'}
              </label>
              <div className="input-with-button">
                <input
                  id="source-location"
                  value={location}
                  onChange={(e) => setLocation(e.target.value)}
                  placeholder={
                    sourceType === 'github'
                      ? 'https://github.com/org/repo'
                      : 'e.g. C:/Projects/my-service'
                  }
                  required
                  spellCheck={false}
                />
                {sourceType === 'local' && (
                  <button
                    type="button"
                    className="btn"
                    onClick={() => setPickerOpen(true)}
                    title="Browse local folders"
                  >
                    📁 Browse…
                  </button>
                )}
              </div>
            </div>

            <div className="form-group">
              <label className="form-label" htmlFor="source-name">
                Name <span className="faint">(optional)</span>
              </label>
              <input
                id="source-name"
                value={name}
                onChange={(e) => setName(e.target.value)}
                placeholder="Defaults to the folder or repository name"
              />
            </div>

            <div className="form-group">
              <label className="form-label" htmlFor="source-branch">
                Branch <span className="faint">(optional)</span>
              </label>
              <input
                id="source-branch"
                value={branch}
                onChange={(e) => setBranch(e.target.value)}
                placeholder="Detected for local; main for GitHub"
              />
            </div>

            {error && <div className="picker-error">{error}</div>}

            <div className="btn-row" style={{ marginTop: 16, justifyContent: 'flex-end' }}>
              <button type="button" className="btn" onClick={onClose} disabled={busy}>
                Cancel
              </button>
              <button type="submit" className="btn primary" disabled={busy || !location.trim()}>
                {busy ? 'Adding…' : 'Add Source'}
              </button>
            </div>
          </form>
        </div>
      </div>

      <FolderPickerModal
        isOpen={pickerOpen}
        initialPath={location}
        title="Select Source Repository Folder"
        onSelect={handleFolderPicked}
        onClose={() => setPickerOpen(false)}
      />
    </>
  )
}

function ImportSourcesModal({
  isOpen,
  workspaceId,
  onClose,
  onSuccess,
}: {
  isOpen: boolean
  workspaceId: number
  onClose: () => void
  onSuccess: () => void | Promise<void>
}) {
  const [content, setContent] = useState('')
  const [preview, setPreview] = useState<ManifestPreview | null>(null)
  const [busy, setBusy] = useState<'validate' | 'import' | null>(null)
  const [error, setError] = useState<string | null>(null)

  const handleValidate = async () => {
    setBusy('validate')
    setError(null)
    try {
      setPreview(await api.validateSourcesManifest(workspaceId, content))
    } catch (err) {
      setPreview(null)
      setError(err instanceof ApiError ? err.detail : 'Could not read the manifest.')
    } finally {
      setBusy(null)
    }
  }

  const handleImport = async () => {
    if (!preview) return
    setBusy('import')
    setError(null)
    try {
      await api.importSourcesManifest(workspaceId, content, true)
      await onSuccess()
      setPreview(null)
      setContent('')
      onClose()
    } catch (err) {
      setError(err instanceof ApiError ? err.detail : 'Import failed.')
    } finally {
      setBusy(null)
    }
  }

  const handleFile = async (file: File) => {
    const text = await file.text()
    setContent(text)
    setPreview(null)
  }

  if (!isOpen) return null

  return (
    <div className="modal-backdrop" onClick={onClose}>
      <div
        className="modal-dialog"
        style={{ width: 560 }}
        onClick={(e) => e.stopPropagation()}
        role="dialog"
      >
        <div className="modal-header">
          <div className="modal-title-row">
            <h3>Import .sources.yaml</h3>
            <button className="btn text-sm" onClick={onClose}>
              ✕
            </button>
          </div>
        </div>

        <div style={{ padding: 18 }}>
          <div className="form-group">
            <label className="form-label" htmlFor="sources-yaml-input">
              Manifest file
            </label>
            <input
              id="sources-yaml-input"
              type="file"
              accept=".yaml,.yml,text/yaml"
              onChange={(e) => {
                const file = e.target.files?.[0]
                if (file) void handleFile(file)
              }}
            />
          </div>

          <div className="form-group">
            <label className="form-label" htmlFor="sources-yaml-content">
              Or paste the manifest
            </label>
            <textarea
              id="sources-yaml-content"
              value={content}
              onChange={(e) => {
                setContent(e.target.value)
                setPreview(null)
              }}
              rows={6}
              spellCheck={false}
              placeholder={'sources:\n  - repo: Gaia\n    path: https://github.com/org/repo'}
              style={{ width: '100%', fontFamily: 'monospace', fontSize: 12 }}
            />
          </div>

          <div className="btn-row" style={{ justifyContent: 'flex-start' }}>
            <button
              type="button"
              className="btn"
              onClick={handleValidate}
              disabled={!content.trim() || busy !== null}
            >
              {busy === 'validate' ? 'Checking…' : 'Preview'}
            </button>
          </div>

          {preview && (
            <div style={{ marginTop: 12 }}>
              <div style={{ fontWeight: 600, fontSize: 12, marginBottom: 6 }}>
                {preview.valid_count} valid {preview.valid_count === 1 ? 'repository' : 'repositories'}
                {preview.invalid_count > 0 && ` · ${preview.invalid_count} invalid ${preview.invalid_count === 1 ? 'entry' : 'entries'}`}
                {preview.duplicate_count > 0 && ` · ${preview.duplicate_count} already configured`}
              </div>
              <div style={{ maxHeight: 220, overflowY: 'auto', border: '1px solid var(--border-light)', borderRadius: 6, padding: 8 }}>
                {preview.entries.map((entry) => (
                  <div key={entry.index} style={{ padding: '4px 0', fontSize: 12 }}>
                    <div>
                      {entry.valid ? '✓' : '✗'} <strong>{entry.repo}</strong>{' '}
                      <span className="tag">{SOURCE_TYPE_LABELS[entry.source_type] || entry.source_type}</span>
                      {entry.action === 'duplicate' && (
                        <span className="faint"> (already configured as {entry.existing_name})</span>
                      )}
                    </div>
                    <div className="faint" style={{ fontSize: 11 }}>{entry.path}</div>
                    {entry.error && (
                      <div style={{ fontSize: 11, color: 'var(--danger, #b00020)' }}>{entry.error}</div>
                    )}
                  </div>
                ))}
              </div>
            </div>
          )}

          {error && <div className="picker-error">{error}</div>}

          <div className="btn-row" style={{ marginTop: 16, justifyContent: 'flex-end' }}>
            <button type="button" className="btn" onClick={onClose} disabled={busy !== null}>
              Cancel
            </button>
            <button
              type="button"
              className="btn primary"
              onClick={handleImport}
              disabled={!preview || preview.new_count === 0 || busy !== null}
              title={
                preview && preview.new_count === 0
                  ? 'Every entry is already configured; there is nothing to import.'
                  : undefined
              }
            >
              {busy === 'import'
                ? 'Importing…'
                : preview
                  ? `Import ${preview.new_count} ${preview.new_count === 1 ? 'repository' : 'repositories'}`
                  : 'Import'}
            </button>
          </div>
        </div>
      </div>
    </div>
  )
}

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
  const [addSourceOpen, setAddSourceOpen] = useState(false)
  const [importOpen, setImportOpen] = useState(false)
  const [syncingId, setSyncingId] = useState<number | null>(null)
  const [search, setSearch] = useState('')
  const [activeFilter, setActiveFilter] = useState<'ALL' | 'DOCS' | 'ADRS' | 'PDF' | 'DOCX' | 'TXT'>('ALL')
  const [viewMode, setViewMode] = useState<'cards' | 'tree'>('cards')

  const docs = workspace.repositories.filter((r) => r.kind === 'documentation')
  const sources = workspace.repositories.filter((r) => r.kind === 'source')

  const flatDocNodes = useMemo(() => flattenDocs(tree), [tree])

  const handleSyncSource = async (repoId: number) => {
    setSyncingId(repoId)
    try {
      await api.syncSource(workspace.id, repoId)
      if (onRepositoryAdded) await onRepositoryAdded()
    } catch (err) {
      console.error(err)
    } finally {
      setSyncingId(null)
    }
  }

  const handleRemoveSource = async (repoId: number) => {
    try {
      await api.removeSource(workspace.id, repoId)
      if (onRepositoryAdded) await onRepositoryAdded()
    } catch (err) {
      console.error(err)
    }
  }

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
            placeholder="Search documents…"
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

      {/* Real document count banner */}
      <div className="memories-counter-banner">
        <span>{flatDocNodes.length} {flatDocNodes.length === 1 ? 'DOCUMENT' : 'DOCUMENTS'} IN ARCHIVE</span>
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
            <div className="doc-cards-empty" style={{ textAlign: 'center', padding: '36px 14px' }}>
              <div className="empty-icon-circle" style={{ margin: '0 auto 12px' }}>📂</div>
              <h4 style={{ margin: '0 0 6px', fontSize: 13, fontWeight: 700 }}>No Documents Found</h4>
              <p className="faint" style={{ fontSize: 12, marginBottom: 16 }}>
                {search
                  ? `No documents matching "${search}".`
                  : 'Connect a repository folder to index documentation.'}
              </p>
              <button
                type="button"
                className="btn primary text-sm"
                onClick={() => setAddRepoOpen(true)}
              >
                + Add Repository
              </button>
            </div>
          ) : (
            filteredDocs.map((node) => {
              const ext = node.name.split('.').pop()?.toLowerCase() || 'md'
              const rawTitle = node.name.replace(/\.[^/.]+$/, '').replace(/[-_]/g, ' ')
              const cleanTitle = rawTitle.charAt(0).toUpperCase() + rawTitle.slice(1)
              const isSelected = selectedPath === node.path
              const folder = node.path.includes('/') ? node.path.split('/')[0].toUpperCase() : 'ROOT'
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
                      {ext === 'pdf' ? '📕 PDF' : ext === 'docx' ? '📘 DOCX' : ext === 'txt' ? '📄 TXT' : isAdr ? '⚖️ ADR' : '📝 DOC'}
                    </span>
                    <span className="doc-card-date">
                      {node.size ? (node.size < 1024 ? `${node.size} B` : `${Math.round(node.size / 1024)} KB`) : ''}
                    </span>
                  </div>

                  <h4 className="doc-card-title">{cleanTitle}</h4>

                  <p className="doc-card-snippet">
                    {node.path}
                  </p>

                  <div className="doc-card-pills">
                    <span className="doc-pill-tag">{folder}</span>
                    <span className="doc-pill-tag">{ext.toUpperCase()}</span>
                    {isAdr && <span className="doc-pill-tag">ADR</span>}
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

      <div style={{ padding: '8px 14px', borderTop: '1px solid var(--border-light)' }}>
        <div
          style={{
            fontSize: 10,
            fontWeight: 700,
            color: 'var(--text-faint)',
            textTransform: 'uppercase',
            display: 'flex',
            justifyContent: 'space-between',
            alignItems: 'center',
          }}
        >
          <span>Source Repositories (read-only)</span>
          <span>
            <button
              type="button"
              className="btn text-sm"
              style={{ padding: '1px 5px', fontSize: 10 }}
              onClick={() => setAddSourceOpen(true)}
              title="Add a source repository (local or GitHub)"
            >
              + Source
            </button>{' '}
            <button
              type="button"
              className="btn text-sm"
              style={{ padding: '1px 5px', fontSize: 10 }}
              onClick={() => setImportOpen(true)}
              title="Import repositories from a .sources.yaml manifest"
            >
              ⤓ Import YAML
            </button>
          </span>
        </div>

        {sources.length === 0 ? (
          <div className="faint" style={{ fontSize: 11, padding: '4px 0' }}>
            No source repositories configured. Add one, or import a .sources.yaml manifest
            to register code repositories as architecture evidence.
          </div>
        ) : (
          sources.map((repo) => {
            const statusInfo = SOURCE_STATUS_LABELS[repo.status || 'ready'] || {
              label: repo.status || 'ready',
              cls: 'warn',
            }
            return (
              <div key={repo.id} style={{ padding: '4px 0', fontSize: 11 }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: 4 }}>
                  <span>💻</span>
                  <strong style={{ fontSize: 11 }}>{repo.name}</strong>
                  <span className={`tag ${statusInfo.cls}`}>{statusInfo.label}</span>
                  <span style={{ marginLeft: 'auto', display: 'flex', gap: 3 }}>
                    <button
                      type="button"
                      className="btn text-sm"
                      style={{ padding: '0 4px', fontSize: 10 }}
                      disabled={syncingId === repo.id}
                      onClick={() => void handleSyncSource(repo.id)}
                      title="Refresh / synchronize this source"
                    >
                      {syncingId === repo.id ? '…' : '⟳'}
                    </button>
                    <button
                      type="button"
                      className="btn text-sm"
                      style={{ padding: '0 4px', fontSize: 10 }}
                      onClick={() => void handleRemoveSource(repo.id)}
                      title="Remove this source registration (local files are untouched)"
                    >
                      ✕
                    </button>
                  </span>
                </div>
                <div className="faint" style={{ fontSize: 10 }}>
                  {SOURCE_TYPE_LABELS[repo.source_type || 'local'] || repo.source_type}
                  {' · '}
                  {repo.source_type === 'github' ? repo.source_url : repo.local_path}
                  {repo.current_branch ? ` · ${repo.current_branch}` : ''}
                </div>
                {repo.status_message && (
                  <div className="faint" style={{ fontSize: 10 }}>{repo.status_message}</div>
                )}
              </div>
            )
          })
        )}
      </div>

      <AddRepoModal
        isOpen={addRepoOpen}
        workspaceId={workspace.id}
        hasDocRepo={docs.length > 0}
        onClose={() => setAddRepoOpen(false)}
        onSuccess={async () => {
          if (onRepositoryAdded) await onRepositoryAdded()
        }}
      />
      <AddSourceModal
        isOpen={addSourceOpen}
        workspaceId={workspace.id}
        onClose={() => setAddSourceOpen(false)}
        onSuccess={async () => {
          if (onRepositoryAdded) await onRepositoryAdded()
        }}
      />
      <ImportSourcesModal
        isOpen={importOpen}
        workspaceId={workspace.id}
        onClose={() => setImportOpen(false)}
        onSuccess={async () => {
          if (onRepositoryAdded) await onRepositoryAdded()
        }}
      />
    </>
  )
}
