import { useCallback, useRef, useState, type DragEvent } from 'react'
import { ApiError, api } from '../api/client'
import { FolderPickerModal } from './FolderPickerModal'

/**
 * The way documents get in.
 *
 * A drop from the operating system: `dataTransfer.files`, not the
 * `application/x-apollo-document` payload the groups board carries. That
 * difference is the feature, not an implementation detail -- a document dragged
 * between groups is already in the workspace and is being rearranged, while a
 * file dragged from the desktop is being added, and confusing the two would
 * silently rearrange somebody's arrangement when they meant to add a file.
 *
 * Files are uploaded one at a time and every outcome is reported. Twelve
 * documents dropped together must not succeed or fail as one: eleven stored and
 * one refused is a better result than nothing stored, and the person who dropped
 * them needs to know which was which.
 */
interface Props {
  workspaceId: number
  /** Called after at least one document landed, so the lists can refresh. */
  onStored: () => void
  /** Tighter layout, for the button beside a list that is already full. */
  compact?: boolean
}

/** One file's outcome, kept so the panel can say what happened to each. */
interface Outcome {
  name: string
  ok: boolean
  message: string
}

export function InboxDropzone({ workspaceId, onStored, compact = false }: Props) {
  const [dragging, setDragging] = useState(false)
  const [busy, setBusy] = useState(false)
  const [outcomes, setOutcomes] = useState<Outcome[]>([])
  const [folderPickerOpen, setFolderPickerOpen] = useState(false)
  const inputRef = useRef<HTMLInputElement>(null)
  // dragenter and dragleave fire for every child the cursor crosses, so a
  // boolean would flicker off the moment the pointer moved onto the text inside
  // this box. Counting enters and leaves is what keeps the highlight steady.
  const depth = useRef(0)

  const upload = useCallback(
    async (files: File[]) => {
      if (files.length === 0) return
      setBusy(true)
      setOutcomes([])
      const results: Outcome[] = []
      let stored = 0
      for (const file of files) {
        try {
          const result = await api.uploadToInbox(workspaceId, file)
          stored += 1
          results.push({
            name: result.name,
            ok: result.readable,
            // What the server said, not a cheerful "done". A document Apollo
            // stored but cannot read is still stored, and saying so is the
            // difference between a place documents are kept and a pile.
            message: result.readable
              ? `Added ${result.path}`
              : `${result.path} is kept, but Apollo cannot read it: ${result.unreadable_reason}`,
          })
        } catch (e) {
          results.push({
            name: file.name,
            ok: false,
            message:
              e instanceof ApiError
                ? `${file.name}: ${e.detail}`
                : `${file.name}: the upload did not reach Apollo. Is the backend running?`,
          })
        }
        setOutcomes([...results])
      }
      setBusy(false)
      if (stored > 0) onStored()
    },
    [workspaceId, onStored],
  )

  const onDrop = (event: DragEvent) => {
    event.preventDefault()
    depth.current = 0
    setDragging(false)
    void upload(Array.from(event.dataTransfer.files ?? []))
  }

  /**
   * Copy a whole folder in.
   *
   * Chosen through the same folder picker every other "point at a folder"
   * action in the app uses, rather than typed: a path the reader has to spell
   * out by hand is a path they get wrong. What comes back is reported per
   * document rather than as a single "done", for the same reason a drop
   * reports per document: forty copied and one refused is a different outcome
   * from forty-one copied, and the reader needs to know which.
   */
  const importFolder = useCallback(async (path: string) => {
    if (!path) return
    setBusy(true)
    setOutcomes([])
    try {
      const result = await api.importFolder(workspaceId, path)
      const reported: Outcome[] = result.copied.map((file) => ({
        name: file.name,
        ok: file.readable,
        message: file.readable
          ? `Copied ${file.path}`
          : `${file.path} is kept, but Apollo cannot read it: ${file.unreadable_reason}`,
      }))
      for (const refusal of result.refused) {
        reported.push({ name: refusal.source_path, ok: false, message: `${refusal.source_path}: ${refusal.reason}` })
      }
      if (result.copied.length === 0) {
        reported.push({
          name: path,
          ok: false,
          message: `Nothing was copied from ${path}.`,
        })
      }
      // Said out loud rather than left for the reader to notice: a folder with
      // more documents than one import copies must not look complete.
      if (result.truncated) {
        reported.push({
          name: path,
          ok: false,
          message: `That folder has more documents than Apollo copies at once. Only the first ${result.copied.length} were copied.`,
        })
      }
      setOutcomes(reported)
      if (result.copied.length > 0) onStored()
    } catch (e) {
      setOutcomes([
        {
          name: path,
          ok: false,
          message:
            e instanceof ApiError
              ? `${path}: ${e.detail}`
              : `${path}: the request did not reach Apollo. Is the backend running?`,
        },
      ])
    } finally {
      setBusy(false)
    }
  }, [workspaceId, onStored])

  return (
    <div className={`inbox-drop${compact ? ' inbox-drop--compact' : ''}`}>
      <div
        className={[
          'inbox-dropzone',
          dragging ? 'inbox-dropzone--over' : '',
          busy ? 'inbox-dropzone--busy' : '',
        ]
          .filter(Boolean)
          .join(' ')}
        role="button"
        tabIndex={0}
        onDragEnter={(e) => {
          e.preventDefault()
          depth.current += 1
          setDragging(true)
        }}
        onDragOver={(e) => {
          e.preventDefault()
          e.dataTransfer.dropEffect = 'copy'
        }}
        onDragLeave={() => {
          depth.current = Math.max(0, depth.current - 1)
          if (depth.current === 0) setDragging(false)
        }}
        onDrop={onDrop}
        onClick={() => inputRef.current?.click()}
        onKeyDown={(e) => {
          if (e.key === 'Enter' || e.key === ' ') {
            e.preventDefault()
            inputRef.current?.click()
          }
        }}
        title="Drop documents here, or click to choose files"
      >
        <input
          ref={inputRef}
          type="file"
          multiple
          className="inbox-dropzone-input"
          onChange={(e) => {
            const input = e.target as HTMLInputElement
            const chosen = Array.from(input.files ?? [])
            // Cleared so that choosing the same file twice in a row still
            // counts as a second choice.
            input.value = ''
            void upload(chosen)
          }}
        />
        <span className="inbox-dropzone-glyph" aria-hidden="true">
          {busy ? '·' : '＋'}
        </span>
        <span className="inbox-dropzone-title">
          {busy ? 'Storing…' : dragging ? 'Drop them here' : 'Add documents'}
        </span>
        <span className="inbox-dropzone-hint">
          Drag files in, or click to choose. Markdown, text, PDF and Word. Apollo keeps
          its own copy; nothing you already have is moved or deleted.
        </span>

        {/* A whole folder, for the reader who already has one. A real button
            inside the dropzone, under its text -- stopping propagation so its
            click (and Enter/Space) opens the folder picker instead of also
            triggering the box's own "click anywhere to choose files". */}
        <button
          type="button"
          className="inbox-folder-open"
          onClick={(e) => {
            e.stopPropagation()
            setFolderPickerOpen(true)
          }}
          onKeyDown={(e) => e.stopPropagation()}
        >
          + Add a whole folder
        </button>
      </div>

      {outcomes.length > 0 && (
        <ul className="inbox-outcomes">
          {outcomes.map((outcome, index) => (
            <li
              key={`${outcome.name}-${index}`}
              className={`inbox-outcome${outcome.ok ? '' : ' inbox-outcome--warn'}`}
            >
              {outcome.message}
            </li>
          ))}
        </ul>
      )}

      <FolderPickerModal
        isOpen={folderPickerOpen}
        title="Select a folder to copy in"
        onSelect={(path) => {
          setFolderPickerOpen(false)
          void importFolder(path)
        }}
        onClose={() => setFolderPickerOpen(false)}
      />
    </div>
  )
}
