import { useState } from 'react'
import { ApiError, api, type DocNode, type RepoKind, type Repository, type Workspace } from '../api/client'
import { FolderPickerModal } from './FolderPickerModal'

/** Human labels for the evidence categories, so claims are never ambiguous. */
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
              <button className="btn icon-btn" onClick={onClose}>
                ✕
              </button>
            </div>
          </div>

          <form onSubmit={handleSubmit} style={{ padding: 16 }}>
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
  currentView?: 'conversation' | 'questions' | 'decisions'
  onSelectView?: (view: 'conversation' | 'questions' | 'decisions') => void
  openQuestionsCount?: number
  decisionsCount?: number
  onRepositoryAdded?: () => void | Promise<void>
}) {
  const [addRepoOpen, setAddRepoOpen] = useState(false)
  const docs = workspace.repositories.filter((r) => r.kind === 'documentation')
  const sources = workspace.repositories.filter((r) => r.kind === 'source')

  return (
    <>
      <div className="section">
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
          <h3>Workspace</h3>
          <button
            type="button"
            className="btn text-sm"
            style={{ padding: '2px 6px', fontSize: 11 }}
            onClick={() => setAddRepoOpen(true)}
            title="Add a documentation or source repository"
          >
            + Add Repo
          </button>
        </div>
        <div className="list-title">{workspace.name}</div>
        <div className="list-sub">
          {workspace.repositories.length} repositor{workspace.repositories.length === 1 ? 'y' : 'ies'}
        </div>
      </div>

      {onSelectView && (
        <div className="section">
          <h3>Views</h3>
          <div
            className={`list-item ${currentView === 'conversation' ? 'active' : ''}`}
            onClick={() => onSelectView('conversation')}
          >
            <div className="list-title">Conversation</div>
            <div className="list-sub">Architectural discussion & analysis</div>
          </div>
          <div
            className={`list-item ${currentView === 'questions' ? 'active' : ''}`}
            onClick={() => onSelectView('questions')}
          >
            <div className="list-title" style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
              <span>Open Questions</span>
              {openQuestionsCount !== undefined && openQuestionsCount > 0 && (
                <span className="tag warn">{openQuestionsCount} open</span>
              )}
            </div>
            <div className="list-sub">Unresolved questions & issues</div>
          </div>
          <div
            className={`list-item ${currentView === 'decisions' ? 'active' : ''}`}
            onClick={() => onSelectView('decisions')}
          >
            <div className="list-title" style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
              <span>Decisions</span>
              {decisionsCount !== undefined && decisionsCount > 0 && (
                <span className="tag accent">{decisionsCount}</span>
              )}
            </div>
            <div className="list-sub">Architectural decisions & ADR sync</div>
          </div>
        </div>
      )}

      {docs.map((repo) => (
        <div className="section" key={repo.id}>
          <h3>
            {repo.name}
            {repo.writable ? <span className="tag ok" style={{ marginLeft: 6 }}>writable</span> : null}
          </h3>
          {repo.head_revision ? (
            <div className="list-sub mono">{repo.head_revision.slice(0, 8)}</div>
          ) : null}
          {repository?.id === repo.id && tree ? (
            <div style={{ marginTop: 6 }}>
              {tree.children.length === 0 ? (
                <div className="faint" style={{ fontSize: 12, padding: '4px 6px' }}>
                  No documents.
                </div>
              ) : (
                <TreeBranch
                  nodes={tree.children}
                  selected={selectedPath}
                  onSelect={(node) => onSelectDocument(node.path)}
                />
              )}
            </div>
          ) : null}
        </div>
      ))}

      {sources.length > 0 && (
        <div className="section">
          <h3>Source (read-only)</h3>
          {sources.map((repo) => (
            <div key={repo.id} className="list-sub" style={{ padding: '2px 0' }}>
              {repo.name}
              {repo.current_branch ? ` · ${repo.current_branch}` : ''}
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
