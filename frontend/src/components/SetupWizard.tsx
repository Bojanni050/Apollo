/**
 * First-run setup: create a workspace and give it a folder, in one pass.
 *
 * Why this exists
 * ---------------
 * The API has always supported creating a workspace, choosing its working
 * folder and registering a documentation repository, and the typed client has
 * methods for all three, but nothing in the UI called them. A new user had to
 * open a terminal, run curl, and reload the page before the app was usable at
 * all. This component closes that gap.
 *
 * Two steps, not three
 * ---------------------
 * This used to be three steps: name the workspace, choose a working folder,
 * then separately register an existing documentation folder. That third step
 * read as a second, different kind of "pick a folder" question right after the
 * first one, which is exactly where people got lost -- two folders that look
 * like the same choice are not the same choice.
 *
 * The folder step now creates the workspace's repository the moment a folder
 * is chosen (see `set_working_dir` on the backend, which does this eagerly
 * rather than waiting for a first upload) -- so as soon as step 2's folder is
 * confirmed, the workspace already has somewhere to keep documents and chat is
 * not blocked. Adding documents -- one at a time, or a whole existing folder
 * copied in at once, both through `InboxDropzone` -- is then one optional,
 * later half of the same step rather than a step of its own: real, offered
 * plainly, but not something the reader has to get past to finish. Everything
 * added this way lands in the Inbox, the same as it would from the workspace
 * itself; this wizard has no path that registers a folder to be read in place
 * instead of copied -- that is a distinct, later choice, made from the
 * workspace's own repository settings once there is something to compare it to.
 *
 * Path validation is the backend's job, not this component's. The form
 * deliberately does not re-implement the allow-list rules: it sends what the
 * user typed and renders the server's message verbatim. A second implementation
 * in TypeScript would drift from the real one and could tell the user a path is
 * fine when the server disagrees -- the worse outcome, because the server is
 * the security boundary.
 */
import { useState } from 'react'
import { ApiError, api, type AreaTemplate, type Workspace } from '../api/client'
import { FolderPickerModal } from './FolderPickerModal'
import { InboxDropzone } from './InboxDropzone'

type Step = 'workspace' | 'folder' | 'done'

interface Props {
  /** Called when a workspace exists, so the app can load and select it. */
  onWorkspaceCreated: (workspace: Workspace) => void | Promise<void>
}

export function SetupWizard({ onWorkspaceCreated }: Props) {
  const [step, setStep] = useState<Step>('workspace')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  // -- step 1: the workspace -------------------------------------------------
  const [name, setName] = useState('')
  const [description, setDescription] = useState('')
  /** The workspace from step 1, needed for every call the folder step makes. */
  const [created, setCreated] = useState<Workspace | null>(null)

  const message = (e: unknown, fallback: string) =>
    setError(e instanceof ApiError ? e.detail : fallback)

  const createWorkspace = async (e: React.FormEvent) => {
    e.preventDefault()
    setBusy(true)
    setError(null)
    try {
      const ws = await api.createWorkspace(name.trim(), description.trim() || null)
      setCreated(ws)
      // Deliberately does NOT notify the parent yet. Doing so would add the
      // workspace to App's list, which makes App render the main layout and
      // unmount this wizard -- so the folder step would never be seen.
      // The parent is told once the whole flow finishes, in finish().
      setStep('folder')
    } catch (err) {
      message(err, 'Could not create the workspace.')
    } finally {
      setBusy(false)
    }
  }

  /**
   * Hand the finished workspace to the app, which selects it and loads the
   * document tree. Reachable as soon as the folder is chosen -- everything
   * after that is optional.
   */
  const finish = async (ws: Workspace) => {
    setStep('done')
    try {
      await onWorkspaceCreated(ws)
    } catch (err) {
      // The workspace does exist; only the refresh failed. Surface it rather
      // than leaving the user on a wizard that looks finished but is not.
      message(err, 'The workspace was created, but could not be loaded.')
    }
  }

  // -- step 2: the folder ------------------------------------------------------
  // Choosing (or explicitly keeping Apollo's own) is what creates the
  // workspace's repository, so this half of the step cannot be skipped -- only
  // answered either way. What comes after it -- uploading something now, or
  // pointing at an existing documentation folder -- can be.
  const [workingPath, setWorkingPath] = useState('')
  const [workingPicked, setWorkingPicked] = useState(false)
  const [workingWarning, setWorkingWarning] = useState<string | null>(null)
  const [workingPickerOpen, setWorkingPickerOpen] = useState(false)

  const chooseWorkingFolder = async (path: string) => {
    setBusy(true)
    setError(null)
    try {
      const result = await api.setWorkingDir(created!.id, path)
      setWorkingPicked(true)
      setWorkingPath(result.working_dir ?? path)
      setWorkingWarning(result.warning)
    } catch (err) {
      message(err, 'That folder could not be chosen.')
    } finally {
      setBusy(false)
    }
  }

  /** Apollo's own folder is a real choice, made through the same call, so the
   *  repository exists either way -- not a dismissal that leaves it missing. */
  const useDefaultFolder = () => void chooseWorkingFolder('')

  // -- step 2, optional half: a starting structure ------------------------------
  // The stable top level (hoofdgebieden) a workspace's groups live under. A
  // template is nothing but a name for "create these few areas" -- picking one
  // is the same act as creating them by hand later, just faster for a reader
  // who already knows the shape of their project. Skippable, like everything
  // else in this step: nothing here is required to finish.
  const [applyingTemplate, setApplyingTemplate] = useState(false)
  const [appliedTemplate, setAppliedTemplate] = useState<AreaTemplate | null>(null)

  const applyTemplate = async (template: AreaTemplate) => {
    if (!created) return
    setApplyingTemplate(true)
    setError(null)
    try {
      await api.applyAreaTemplate(created.id, template)
      setAppliedTemplate(template)
    } catch (err) {
      message(err, 'That structure could not be applied.')
    } finally {
      setApplyingTemplate(false)
    }
  }

  // -- step 1: the workspace -------------------------------------------------
  if (step === 'workspace') {
    return (
      <form className="setup" onSubmit={createWorkspace}>
        <h2>Step 1 of 2 &mdash; name your workspace</h2>
        <p className="faint">
          A workspace holds your documentation and the conversations about it.
          It is a name for your work, not a folder &mdash; the next step asks
          for that.
        </p>

        <div className="form-group">
          <label className="form-label" htmlFor="ws-name">
            Name
          </label>
          <input
            id="ws-name"
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder="Gaia"
            maxLength={200}
            autoFocus
            required
          />
        </div>

        <div className="form-group">
          <label className="form-label" htmlFor="ws-desc">
            Description <span className="faint">(optional)</span>
          </label>
          <input
            id="ws-desc"
            value={description}
            onChange={(e) => setDescription(e.target.value)}
            placeholder="Gaia architecture documentation"
          />
        </div>

        {error && <p className="error">{error}</p>}

        <div className="btn-row">
          <button className="btn primary" type="submit" disabled={busy || !name.trim()}>
            {busy ? 'Creating…' : 'Create workspace'}
          </button>
        </div>
      </form>
    )
  }

  // -- step 2: the folder, then optionally some documents ----------------------
  if (step === 'folder' && created) {
    return (
      <>
        <div className="setup">
          <h2>Step 2 of 2 &mdash; your folder</h2>

          {!workingPicked ? (
            <>
              <p className="faint">
                Workspace <strong>{created.name}</strong> is ready. Choose a
                folder on this machine for Apollo to keep its own copies of
                documents in &mdash; or keep Apollo&rsquo;s own.
              </p>

              <div className="form-group">
                <label className="form-label" htmlFor="working-path">
                  Folder
                </label>
                <div className="input-with-button">
                  <input
                    id="working-path"
                    value={workingPath}
                    onChange={(e) => setWorkingPath(e.target.value)}
                    placeholder="C:/Documenten/Apollo"
                    spellCheck={false}
                    autoFocus
                    onKeyDown={(e) => {
                      if (e.key === 'Enter' && workingPath.trim()) {
                        e.preventDefault()
                        void chooseWorkingFolder(workingPath.trim())
                      }
                    }}
                  />
                  <button
                    type="button"
                    className="btn"
                    onClick={() => setWorkingPickerOpen(true)}
                    title="Browse local folders"
                  >
                    📁 Browse…
                  </button>
                </div>
                <p className="hint">
                  Must be an existing directory. An empty one is best, but a
                  folder that already has files in it is fine — Apollo adds its
                  own alongside and leaves the rest alone.
                </p>
                <div className="setup-tree">
                  <p className="setup-tree-title">
                    Apollo will make this, and nothing else:
                  </p>
                  <div className="setup-tree-body">
                    <div className="setup-tree-line">{workingPath.trim() || 'C:\\Documenten\\Apollo'}{'\\'}</div>
                    <div className="setup-tree-line setup-tree-indent">Inbox{'\\'}</div>
                    <div className="setup-tree-line setup-tree-indent setup-tree-arrow">
                      you drop documents in here
                    </div>
                  </div>
                  <p className="setup-tree-note">
                    <code>Inbox</code> is created as soon as you confirm this
                    folder, before you have dropped anything in. You never
                    choose it yourself.
                  </p>
                </div>
              </div>

              {error && <p className="error">{error}</p>}

              <div className="btn-row">
                <button
                  className="btn primary"
                  type="button"
                  disabled={busy || !workingPath.trim()}
                  onClick={() => void chooseWorkingFolder(workingPath.trim())}
                >
                  {busy ? 'Choosing…' : 'Use this folder'}
                </button>
                <button
                  className="btn"
                  type="button"
                  disabled={busy}
                  onClick={useDefaultFolder}
                >
                  {busy ? 'Working…' : "Use Apollo's own folder"}
                </button>
              </div>
            </>
          ) : (
            <>
              <div className="form-group">
                <label className="form-label">Your folder</label>
                <p className="setup-picked">{workingPath}</p>
                {workingWarning && <p className="hint">{workingWarning}</p>}
              </div>

              <div className="setup-optional">
                <h3>Optional &mdash; a starting structure</h3>
                <p className="faint">
                  Give the workspace a few stable top-level areas to start
                  from, or skip this and build them up yourself as you go.
                </p>
                <div className="btn-row">
                  <button
                    type="button"
                    className={`btn${appliedTemplate === 'software' ? ' primary' : ''}`}
                    disabled={applyingTemplate}
                    onClick={() => void applyTemplate('software')}
                  >
                    Software project
                  </button>
                  <button
                    type="button"
                    className={`btn${appliedTemplate === 'book' ? ' primary' : ''}`}
                    disabled={applyingTemplate}
                    onClick={() => void applyTemplate('book')}
                  >
                    Book
                  </button>
                  <button
                    type="button"
                    className={`btn${appliedTemplate === 'research' ? ' primary' : ''}`}
                    disabled={applyingTemplate}
                    onClick={() => void applyTemplate('research')}
                  >
                    Research
                  </button>
                </div>
                {appliedTemplate && (
                  <p className="hint">
                    Areas created. Delphi can still propose new ones later;
                    you decide whether to keep them.
                  </p>
                )}
              </div>

              <div className="setup-optional">
                <h3>Optional &mdash; add some documents now</h3>
                <p className="faint">
                  Drop files in, or use &ldquo;Add a whole folder&rdquo; to copy
                  in an existing documentation folder&rsquo;s contents &mdash;
                  they land in your Inbox above, structure kept, nothing in the
                  source folder touched. You can also do this later, from the
                  workspace itself.
                </p>
                <InboxDropzone workspaceId={created.id} onStored={() => {}} compact />
              </div>

              {error && <p className="error">{error}</p>}

              <div className="btn-row">
                <button
                  className="btn primary"
                  type="button"
                  disabled={busy}
                  onClick={() => void finish(created)}
                >
                  Finish setup
                </button>
              </div>
            </>
          )}
        </div>

        <FolderPickerModal
          isOpen={workingPickerOpen}
          initialPath={workingPath || undefined}
          title="Select Your Folder"
          onSelect={(path) => {
            setWorkingPath(path)
            setWorkingPickerOpen(false)
          }}
          onClose={() => setWorkingPickerOpen(false)}
        />
      </>
    )
  }

  // -- done ------------------------------------------------------------------
  // Only reached while the app is still loading the workspace. As soon as the
  // parent's list updates, App renders the main layout and unmounts this. If
  // the handoff failed, this stays on screen and says what went wrong.
  return (
    <div className="setup">
      <h2>Workspace ready</h2>
      {error ? <p className="error">{error}</p> : <p className="faint">Loading your documents…</p>}
    </div>
  )
}
