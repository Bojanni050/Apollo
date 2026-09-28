import { useEffect, useState, useMemo, useRef } from 'react'
import { api, type FolderBrowseResult } from '../api/client'

interface Props {
  isOpen: boolean
  initialPath?: string
  title?: string
  onSelect: (selectedPath: string) => void
  onClose: () => void
}

export function FolderPickerModal({
  isOpen,
  initialPath,
  title = 'Select Directory',
  onSelect,
  onClose,
}: Props) {
  const [data, setData] = useState<FolderBrowseResult | null>(null)
  const [currentPath, setCurrentPath] = useState(initialPath || '')
  const [pathInput, setPathInput] = useState(initialPath || '')
  const [selectedFolder, setSelectedFolder] = useState<string | null>(null)
  const [filter, setFilter] = useState('')
  const [loading, setLoading] = useState(false)
  const [nativeBusy, setNativeBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  /** Which browse is the current one; see loadDirectory. */
  const browseSeq = useRef(0)
  /** Bumped by every keystroke in the path field; see loadDirectory. */
  const typedMark = useRef(0)

  const loadDirectory = async (path?: string) => {
    // Two guards, because the dialog browses on open and the reader can start
    // typing before that answer arrives.
    //
    // `browseSeq` drops a response that is no longer the newest one, so two
    // quick navigations cannot arrive out of order.
    //
    // `typedMark` stops an in-flight browse from overwriting what the reader has
    // typed since it began. Without it, the folders load, the field the reader
    // is halfway through typing in is rewritten to wherever the dialog started,
    // and pressing Go navigates to that instead -- which reads as the picker
    // ignoring you.
    const seq = ++browseSeq.current
    const mark = typedMark.current
    setLoading(true)
    setError(null)
    try {
      const res = await api.browseFolders(path)
      if (seq !== browseSeq.current) return
      setData(res)
      setCurrentPath(res.current_path)
      setSelectedFolder(res.current_path)
      setFilter('')
      if (typedMark.current === mark) {
        setPathInput(res.current_path)
      }
    } catch (err) {
      if (seq !== browseSeq.current) return
      setError(err instanceof Error ? err.message : 'Could not browse directory.')
    } finally {
      if (seq === browseSeq.current) setLoading(false)
    }
  }

  useEffect(() => {
    if (isOpen) {
      void loadDirectory(initialPath?.trim() || undefined)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [isOpen])

  const handleNativePick = async () => {
    setNativeBusy(true)
    setError(null)
    try {
      const res = await api.pickNativeFolder()
      if (res.path) {
        onSelect(res.path)
        onClose()
      } else if (res.error) {
        setError(res.error)
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Native picker failed.')
    } finally {
      setNativeBusy(false)
    }
  }

  const handleSelectCurrent = () => {
    const finalPath = selectedFolder || currentPath
    if (finalPath) {
      onSelect(finalPath)
      onClose()
    }
  }

  const filteredFolders = useMemo(() => {
    if (!data?.folders) return []
    const term = filter.trim().toLowerCase()
    if (!term) return data.folders
    return data.folders.filter((f) => f.name.toLowerCase().includes(term))
  }, [data?.folders, filter])

  // Split currentPath for clickable breadcrumbs
  const breadcrumbs = useMemo(() => {
    if (!currentPath) return []
    const clean = currentPath.replace(/\\/g, '/')
    const parts = clean.split('/').filter(Boolean)
    const isWindows = /^[A-Za-z]:/.test(clean)

    const list: { name: string; fullPath: string }[] = []
    let accum = isWindows ? '' : '/'

    parts.forEach((part, index) => {
      if (index === 0 && isWindows) {
        accum = `${part}/`
        list.push({ name: part, fullPath: `${part}\\` })
      } else {
        accum = accum.endsWith('/') ? `${accum}${part}` : `${accum}/${part}`
        const full = isWindows ? accum.replace(/\//g, '\\') : accum
        list.push({ name: part, fullPath: full })
      }
    })
    return list
  }, [currentPath])

  if (!isOpen) return null

  return (
    <div className="modal-backdrop" onClick={onClose}>
      <div
        className="modal-dialog folder-picker-modal"
        onClick={(e) => e.stopPropagation()}
        role="dialog"
        aria-modal="true"
      >
        <div className="modal-header">
          <div className="modal-title-row">
            <h3>{title}</h3>
            <div className="btn-row" style={{ gap: 8 }}>
              <button
                className="btn text-sm"
                type="button"
                onClick={handleNativePick}
                disabled={nativeBusy || loading}
                title="Open host system folder selection dialog"
              >
                {nativeBusy ? 'Opening OS Dialog…' : '🖥️ System Dialog'}
              </button>
              <button className="btn icon-btn" type="button" onClick={onClose} title="Close">
                ✕
              </button>
            </div>
          </div>

          <div className="picker-path-row">
            <button
              className="btn icon-btn"
              type="button"
              disabled={loading || !data?.parent_path}
              onClick={() => data?.parent_path && loadDirectory(data.parent_path)}
              title="Go up to parent directory"
            >
              ⬆ Up
            </button>

            <form
              className="picker-path-form"
              onSubmit={(e) => {
                e.preventDefault()
                if (pathInput.trim()) void loadDirectory(pathInput.trim())
              }}
            >
              <input
                className="picker-path-input"
                value={pathInput}
                onChange={(e) => {
                  typedMark.current += 1
                  setPathInput(e.target.value)
                }}
                placeholder="Enter folder path..."
                spellCheck={false}
              />
              <button className="btn" type="submit" disabled={loading || !pathInput.trim()}>
                Go
              </button>
            </form>
          </div>

          {breadcrumbs.length > 0 && (
            <div className="picker-breadcrumbs">
              {breadcrumbs.map((b, idx) => (
                <span key={b.fullPath} className="breadcrumb-segment">
                  <button
                    type="button"
                    className="breadcrumb-btn"
                    onClick={() => loadDirectory(b.fullPath)}
                    title={b.fullPath}
                  >
                    {b.name}
                  </button>
                  {idx < breadcrumbs.length - 1 && <span className="breadcrumb-sep">/</span>}
                </span>
              ))}
            </div>
          )}
        </div>

        <div className="picker-body">
          <div className="picker-sidebar">
            {data?.drives && data.drives.length > 0 && (
              <div className="picker-nav-group">
                <div className="picker-nav-header">Drives</div>
                {data.drives.map((d) => (
                  <button
                    key={d}
                    type="button"
                    className={`picker-nav-item ${currentPath.toUpperCase().startsWith(d.toUpperCase()) ? 'active' : ''}`}
                    onClick={() => loadDirectory(d)}
                  >
                    💾 {d}
                  </button>
                ))}
              </div>
            )}

            {data?.quick_access && data.quick_access.length > 0 && (
              <div className="picker-nav-group">
                <div className="picker-nav-header">Quick Access</div>
                {data.quick_access.map((q) => (
                  <button
                    key={q.path}
                    type="button"
                    className={`picker-nav-item ${currentPath === q.path ? 'active' : ''}`}
                    onClick={() => loadDirectory(q.path)}
                    title={q.path}
                  >
                    📁 {q.name}
                  </button>
                ))}
              </div>
            )}
          </div>

          <div className="picker-content">
            <div className="picker-filter-row">
              <input
                className="picker-filter-input"
                value={filter}
                onChange={(e) => setFilter(e.target.value)}
                placeholder="Filter folders..."
              />
              {filter && (
                <button
                  type="button"
                  className="btn text-sm"
                  onClick={() => setFilter('')}
                  style={{ padding: '2px 8px' }}
                >
                  Clear
                </button>
              )}
            </div>

            {error && <div className="picker-error">{error}</div>}

            <div className="picker-folder-list">
              {loading ? (
                <div className="picker-empty">Loading folders…</div>
              ) : filteredFolders.length === 0 ? (
                <div className="picker-empty">
                  {filter ? 'No folders match your filter.' : 'No subdirectories found.'}
                </div>
              ) : (
                filteredFolders.map((f) => {
                  const isSelected = selectedFolder === f.path
                  return (
                    <div
                      key={f.path}
                      className={`picker-folder-item ${isSelected ? 'selected' : ''}`}
                      onClick={() => setSelectedFolder(f.path)}
                      onDoubleClick={() => loadDirectory(f.path)}
                      title={`${f.name} (Double-click to open)`}
                    >
                      <span className="folder-icon">📁</span>
                      <span className="folder-name">{f.name}</span>
                      <button
                        type="button"
                        className="picker-open-btn"
                        onClick={(e) => {
                          e.stopPropagation()
                          void loadDirectory(f.path)
                        }}
                        title="Enter folder"
                      >
                        Open ➔
                      </button>
                    </div>
                  )
                })
              )}
            </div>
          </div>
        </div>

        <div className="modal-footer">
          <div className="picker-selected-path" title={selectedFolder || currentPath}>
            <span className="faint">Selected:</span>{' '}
            <span className="mono">{selectedFolder || currentPath || '(none)'}</span>
          </div>
          <div className="btn-row">
            <button className="btn" type="button" onClick={onClose}>
              Cancel
            </button>
            <button
              className="btn primary"
              type="button"
              disabled={loading || (!selectedFolder && !currentPath)}
              onClick={handleSelectCurrent}
            >
              Select Folder
            </button>
          </div>
        </div>
      </div>
    </div>
  )
}
