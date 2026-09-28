import { useCallback, useEffect, useState } from 'react'
import { ApiError, api, type WorkingDir } from '../api/client'
import { FolderPickerModal } from './FolderPickerModal'

/**
 * Where this workspace keeps its documents.
 *
 * Two things make this more than a path display, and both come from the same
 * place: the folder belongs to the reader. A folder the application picked is a
 * folder they have never looked at, and the first thing this product does with a
 * document is rearrange it on disk -- so the choice is theirs to make, and it is
 * offered before anything is dropped in rather than after.
 *
 * A folder that is not empty is *allowed*, not refused. Somebody keeping their
 * documents in a folder that already has some is doing something reasonable, and
 * the only thing Apollo owes them is saying plainly what it will and will not do
 * with what is already there. A refusal here would be Apollo deciding which of
 * the reader's own folders are allowed to exist.
 */
interface Props {
  workspaceId: number
  /** Called after the folder changed, so whatever lists it feeds can refresh. */
  onChanged?: () => void
}

export function WorkingFolderCard({ workspaceId, onChanged }: Props) {
  const [state, setState] = useState<WorkingDir | null>(null)
  const [pickerOpen, setPickerOpen] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(null)

  const load = useCallback(async () => {
    try {
      setState(await api.workingDir(workspaceId))
    } catch {
      // A card that cannot be read says nothing rather than blocking the
      // workspace behind it: the inbox works either way.
      setState(null)
    }
  }, [workspaceId])

  useEffect(() => {
    void load()
  }, [load])

  const choose = useCallback(
    async (path: string) => {
      setBusy(true)
      setError(null)
      setNotice(null)
      try {
        const next = await api.setWorkingDir(workspaceId, path)
        setState(next)
        // Said once, after the fact: what the choice did, in the reader's terms.
        setNotice(
          next.working_dir
            ? `Documents now go to ${next.working_dir}. Nothing already there was moved, and the folder keeps a history so a later rearrangement can be undone.`
            : 'Back to Apollo\'s own folder.',
        )
        onChanged?.()
      } catch (e) {
        setError(
          e instanceof ApiError
            ? e.detail
            : 'That folder could not be chosen. Is the backend running?',
        )
      } finally {
        setBusy(false)
      }
    },
    [workspaceId, onChanged],
  )

  if (state === null) return null

  const chosen = state.working_dir !== null

  return (
    <section className="working-folder-card" aria-label="Working folder">
      <header className="working-folder-head">
        <h3>{chosen ? 'Working folder' : 'Where should your documents go?'}</h3>
        <button type="button" onClick={() => setPickerOpen(true)} disabled={busy}>
          {chosen ? 'Change' : 'Choose a folder'}
        </button>
      </header>

      <p className="working-folder-path">
        {chosen ? (
          state.working_dir
        ) : (
          <>
            Apollo is using a folder of its own. Choose one of yours and your
            documents land where you can see them.
          </>
        )}
      </p>

      {chosen && (
        <p className="working-folder-detail">
          A document you drop in goes to <code>{state.inbox_dir}</code>.
          {!state.empty &&
            ' Apollo keeps that history, so nothing it moves can be lost without a trace.'}
        </p>
      )}

      {state.warning && chosen && (
        <p className="working-folder-warning" role="status">
          {state.warning}
        </p>
      )}

      {error && (
        <p className="working-folder-warning" role="alert">
          {error}
        </p>
      )}

      {notice && (
        <p className="working-folder-detail" role="status">
          {notice}
        </p>
      )}

      <FolderPickerModal
        isOpen={pickerOpen}
        initialPath={state.working_dir ?? undefined}
        title="Choose the folder to work in"
        onSelect={(path) => void choose(path)}
        onClose={() => setPickerOpen(false)}
      />
    </section>
  )
}