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
 *
 * Which is why the card also says when a folder's documents are *not yet
 * recoverable*. A folder that was already a Git repository when it was chosen is
 * left exactly as it was -- rightly, since its history is somebody else's -- so
 * its files are untracked and the move engine will refuse to touch them. Saying
 * "Apollo keeps that history" there would be a lie, and the refusal that follows
 * names Git rather than this application, so it reads as a bug in the product.
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

  /** The reader takes the decision; this only carries it out. */
  const adopt = useCallback(async () => {
    setBusy(true)
    setError(null)
    setNotice(null)
    try {
      const result = await api.adoptWorkingFolder(workspaceId)
      setState(await api.workingDir(workspaceId))
      // The server's own sentence, because it knows the count and whether there
      // was anything to do; a second wording here would eventually disagree.
      setNotice(result.message)
      onChanged?.()
    } catch (e) {
      setError(
        e instanceof ApiError
          ? e.detail
          : 'That folder could not be made recoverable. Is the backend running?',
      )
    } finally {
      setBusy(false)
    }
  }, [workspaceId, onChanged])

  if (state === null) return null

  const chosen = state.working_dir !== null
  // Non-zero means the folder's documents are not recorded, so every move in it
  // will be refused. Offering a button only then is the point: a folder Apollo
  // prepared already has a history, and a card that always showed this would be
  // asking the reader to fix something that is not broken.
  const unrecorded = state.untracked

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
          {!state.empty && unrecorded === 0 &&
            ' Apollo keeps that history, so nothing it moves can be lost without a trace.'}
        </p>
      )}

      {unrecorded > 0 && (
        <div className="working-folder-adopt">
          <p className="working-folder-warning" role="status">
            This folder already held {unrecorded}{' '}
            {unrecorded === 1 ? 'document' : 'documents'} when you chose it, and
            they are not recorded yet. Apollo will not move a file it cannot
            recover, so filing a document here would be refused.
          </p>
          <button type="button" onClick={() => void adopt()} disabled={busy}>
            {busy ? 'Recording…' : 'Record what is already there'}
          </button>
          <p className="working-folder-detail">
            One commit, adding nothing but the record. Nothing in the folder is
            moved, renamed or rewritten, and your existing commits are left as
            they are.
          </p>
        </div>
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