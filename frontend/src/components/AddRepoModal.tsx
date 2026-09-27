import React, { useState } from 'react'
import { ApiError, api, type RepoKind } from '../api/client'
import { FolderPickerModal } from './FolderPickerModal'

interface Props {
  isOpen: boolean
  workspaceId: number
  hasDocRepo: boolean
  onClose: () => void
  onSuccess: () => void | Promise<void>
}

export function AddRepoModal({
  isOpen,
  workspaceId,
  hasDocRepo,
  onClose,
  onSuccess,
}: Props) {
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
          style={{ width: 480 }}
          onClick={(e) => e.stopPropagation()}
          role="dialog"
        >
          <div className="modal-header">
            <div className="modal-title-row">
              <h3 style={{ margin: 0, fontSize: 16, fontWeight: 700 }}>Connect Repository</h3>
              <button type="button" className="btn text-sm" onClick={onClose}>
                ✕
              </button>
            </div>
          </div>

          <form onSubmit={handleSubmit} style={{ padding: '16px 20px' }}>
            {error && (
              <div style={{ color: 'var(--error-fg)', background: 'var(--error-bg)', padding: '6px 10px', borderRadius: 6, fontSize: 12, marginBottom: 12 }}>
                {error}
              </div>
            )}

            <div style={{ marginBottom: 14 }}>
              <label style={{ display: 'block', fontSize: 11, fontWeight: 700, textTransform: 'uppercase', color: 'var(--text-faint)', marginBottom: 4 }}>
                Repository Role
              </label>
              <div style={{ display: 'flex', gap: 6 }}>
                <button
                  type="button"
                  className={`filter-chip ${kind === 'documentation' ? 'active' : ''}`}
                  onClick={() => setKind('documentation')}
                >
                  📝 Documentation (Knowledge Base)
                </button>
                <button
                  type="button"
                  className={`filter-chip ${kind === 'source' ? 'active' : ''}`}
                  onClick={() => setKind('source')}
                >
                  💻 Source Code
                </button>
              </div>
            </div>

            <div style={{ marginBottom: 14 }}>
              <label style={{ display: 'block', fontSize: 11, fontWeight: 700, textTransform: 'uppercase', color: 'var(--text-faint)', marginBottom: 4 }}>
                Folder Location on Disk
              </label>
              <div style={{ display: 'flex', gap: 6 }}>
                <input
                  type="text"
                  value={localPath}
                  onChange={(e) => setLocalPath(e.target.value)}
                  placeholder="C:\path\to\folder"
                  required
                />
                <button
                  type="button"
                  className="btn text-sm"
                  onClick={() => setPickerOpen(true)}
                  style={{ whiteSpace: 'nowrap' }}
                >
                  Browse…
                </button>
              </div>
            </div>

            <div style={{ marginBottom: 16 }}>
              <label style={{ display: 'block', fontSize: 11, fontWeight: 700, textTransform: 'uppercase', color: 'var(--text-faint)', marginBottom: 4 }}>
                Display Name
              </label>
              <input
                type="text"
                value={name}
                onChange={(e) => setName(e.target.value)}
                placeholder="Documentation"
              />
            </div>

            {kind === 'documentation' && (
              <div style={{ marginBottom: 16 }}>
                <label className="checkbox">
                  <input
                    type="checkbox"
                    checked={writable}
                    onChange={(e) => setWritable(e.target.checked)}
                  />
                  <span>Allow AI proposals to write / edit files in this folder</span>
                </label>
              </div>
            )}

            <div style={{ display: 'flex', justifyContent: 'flex-end', gap: 8 }}>
              <button type="button" className="btn text-sm" onClick={onClose} disabled={busy}>
                Cancel
              </button>
              <button type="submit" className="btn primary text-sm" disabled={busy || !localPath.trim()}>
                {busy ? 'Connecting…' : 'Add Repository'}
              </button>
            </div>
          </form>
        </div>
      </div>

      <FolderPickerModal
        isOpen={pickerOpen}
        initialPath={localPath}
        title="Select Folder to Index"
        onSelect={handleFolderPicked}
        onClose={() => setPickerOpen(false)}
      />
    </>
  )
}
