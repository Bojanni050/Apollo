/**
 * First-run setup: create a workspace and register its documentation
 * repository, in one pass.
 *
 * Why this exists
 * ---------------
 * The API has always supported both operations (`POST /workspaces` and
 * `POST /workspaces/{id}/repositories`) and the typed client has methods for
 * them, but nothing in the UI called them. A new user had to open a terminal,
 * run curl, and reload the page before the app was usable at all. This
 * component closes that gap.
 *
 * Design notes
 * ------------
 * A workspace with no repository is nearly useless: the document tree is empty
 * and chat refuses to run (see test_workspace_without_repositories_cannot_chat).
 * That is why the repository step is part of the same flow rather than a
 * separate screen, and why it can be skipped but not forgotten -- the summary
 * says plainly what is still missing.
 *
 * Path validation is the backend's job, not this component's. The form
 * deliberately does not re-implement the allow-list rules: it sends what the
 * user typed and renders the server's message verbatim. A second implementation
 * in TypeScript would drift from the real one and could tell the user a path is
 * fine when the server disagrees -- the worse outcome, because the server is
 * the security boundary.
 */
import { useState } from 'react'
import { ApiError, api, type Workspace } from '../api/client'
import { FolderPickerModal } from './FolderPickerModal'

type Step = 'workspace' | 'working' | 'repository' | 'done'

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

  // -- step 2: the documentation repository ----------------------------------
  const [repoName, setRepoName] = useState('')
  const [localPath, setLocalPath] = useState('')
  const [writable, setWritable] = useState(true)
  const [pickerOpen, setPickerOpen] = useState(false)
  /** The workspace from step 1, needed to attach the repository to. */
  const [created, setCreated] = useState<Workspace | null>(null)

  const handleFolderPicked = (picked: string) => {
    setLocalPath(picked)
    if (!repoName.trim()) {
      const folderName = picked.replace(/[\\/]+$/, '').split(/[\\/]/).pop()
      if (folderName) setRepoName(folderName)
    }
  }

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
      // unmount this wizard -- so the repository step would never be seen.
      // The parent is told once the whole flow finishes, in finish().
      setStep('working')
    } catch (err) {
      message(err, 'Could not create the workspace.')
    } finally {
      setBusy(false)
    }
  }

  /**
   * Hand the finished workspace to the app, which selects it and loads the
   * document tree. Called after the repository is registered, or when the user
   * skips that step.
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

  // -- step 2: the working folder ---------------------------------------------
  // Asked before the documentation folder, and that order is the argument: this
  // is where documents *arrive*, and the folder they are read from is a
  // separate, later question. A reader with no existing documentation at all --
  // the common case for somebody opening this for the first time -- needs this
  // answer and not the other one.
  //
  // Which is also why this step has to say plainly that it wants a *new, empty*
  // folder and that it is not the documentation folder. The two questions both
  // end in "pick a folder", they look the same, and a reader who has just been
  // asked one of them reasonably assumes the second is the same one. So: a
  // distinct heading, a tree of what this choice actually creates, and a reminder
  // on the next step of what this one already was.
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

  /** Skipping is a real state, not a dismissal: the folder stays Apollo's own
   *  and the card beside the inbox keeps saying so. */
  const skipWorkingFolder = () => {
    if (created) setStep('repository')
  }

  const addRepository = async (e: React.FormEvent) => {
    e.preventDefault()
    if (!created) return
    setBusy(true)
    setError(null)
    try {
      const trimmedPath = localPath.trim().replace(/[\\/]+$/, '')
      // The name defaults to the folder name, which is nearly always what the
      // user wants and saves them typing it.
      const fallbackName = trimmedPath.split(/[\\/]/).pop() || 'docs'
      await api.addRepository(created.id, {
        name: repoName.trim() || fallbackName,
        local_path: trimmedPath,
        kind: 'documentation',
        writable,
      })
      await finish(created)
    } catch (err) {
      message(err, 'Could not register the repository.')
    } finally {
      setBusy(false)
    }
  }

  // -- step 1: the workspace -------------------------------------------------
  if (step === 'workspace') {
    return (
      <form className="setup" onSubmit={createWorkspace}>
        <h2>Step 1 of 3 &mdash; name your workspace</h2>
        <p className="faint">
          A workspace holds your documentation and the conversations about it.
          It is a name for your work, not a folder &mdash; you will be asked for
          two folders in the next two steps.
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

  // -- step 3: the documentation repository ----------------------------------
  if (step === 'working' && created) {
    return (
      <>
        <form className="setup" onSubmit={(e) => { e.preventDefault(); setStep('repository') }}>
          <h2>Step 2 of 3 &mdash; where Apollo keeps its own work</h2>
          <p className="faint">
            Workspace <strong>{created.name}</strong> is ready. Choose an empty
            folder on this machine for Apollo to work in. This folder is
            Apollo&rsquo;s own: it fills up as you drop documents in.
          </p>
          <p className="hint">
            This is <strong>not</strong> the folder with your existing
            documentation &mdash; that is the next step. You only choose one
            folder here.
          </p>

          {workingPicked ? (
            <div className="form-group">
              <label className="form-label">Your folder</label>
              <p className="setup-picked">{workingPath}</p>
              {workingWarning && <p className="hint">{workingWarning}</p>}
              <p className="hint">
                This is where Apollo will work. Next you point it at the folder
                with your existing documentation — that is a different folder,
                and you can leave this one as it is.
              </p>
            </div>
          ) : (
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
                Must be an existing directory. An empty one is best, but a folder
                that already has files in it is fine — Apollo adds its own
                alongside and leaves the rest alone.
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
                  folder, before you have dropped anything in. You never choose
                  it yourself.
                </p>
              </div>
            </div>
          )}

          {error && <p className="error">{error}</p>}

          <div className="btn-row">
            {workingPicked ? (
              <button className="btn primary" type="submit" disabled={busy}>
                {busy ? 'Working…' : 'Continue'}
              </button>
            ) : (
              <button
                className="btn primary"
                type="button"
                disabled={busy || !workingPath.trim()}
                onClick={() => void chooseWorkingFolder(workingPath.trim())}
              >
                {busy ? 'Choosing…' : 'Use this folder'}
              </button>
            )}
            <button
              className="btn"
              type="button"
              disabled={busy}
              onClick={skipWorkingFolder}
            >
              Use Apollo&rsquo;s own folder
            </button>
          </div>
        </form>

        <FolderPickerModal
          isOpen={workingPickerOpen}
          initialPath={workingPath || undefined}
          title="Select Your Working Folder"
          onSelect={(path) => {
            setWorkingPath(path)
            setWorkingPickerOpen(false)
          }}
          onClose={() => setWorkingPickerOpen(false)}
        />
      </>
    )
  }

  // -- step 4: the documentation repository ----------------------------------
  if (step === 'repository' && created) {
    return (
      <>
        <form className="setup" onSubmit={addRepository}>
          <h2>Step 3 of 3 &mdash; your existing documentation</h2>
          <p className="faint">
            Almost done. Point Apollo at the folder holding the documentation you
            already have (Markdown, PDF, Word .docx, plain text) — an existing
            directory on this machine. Nothing is cloned or copied.
          </p>
          {workingPicked && (
            <p className="hint">
              A different folder from step 2. Step 2 was Apollo&rsquo;s own
              working folder, now <code>{workingPath}</code>. Leave it there;
              choose here the folder your documents are actually in.
            </p>
          )}

          <div className="form-group">
            <label className="form-label" htmlFor="repo-path">
              Your documentation folder
            </label>
            <div className="input-with-button">
              <input
                id="repo-path"
                value={localPath}
                onChange={(e) => setLocalPath(e.target.value)}
                placeholder="C:/src/gaia-docs"
                spellCheck={false}
                autoFocus
                required
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
            <p className="hint">
              Must be an existing directory on this machine. You can type a path
              or click &quot;Browse&quot; to pick a folder.
            </p>
          </div>

          <div className="form-group">
            <label className="form-label" htmlFor="repo-name">
              Name <span className="faint">(optional)</span>
            </label>
            <input
              id="repo-name"
              value={repoName}
              onChange={(e) => setRepoName(e.target.value)}
              placeholder="Defaults to the folder name"
              maxLength={200}
            />
          </div>

          <div className="form-group">
            <label className="checkbox">
              <input
                type="checkbox"
                checked={writable}
                onChange={(e) => setWritable(e.target.checked)}
              />
              <span>
                Allow edits
                <span className="hint" style={{ display: 'block' }}>
                  Move, rename and edit proposals can be prepared against this
                  repository. Nothing is written without your explicit approval.
                </span>
              </span>
            </label>
          </div>

          {error && <p className="error">{error}</p>}

          <div className="btn-row">
            <button className="btn primary" type="submit" disabled={busy || !localPath.trim()}>
              {busy ? 'Registering…' : 'Register repository'}
            </button>
            <button
              className="btn"
              type="button"
              disabled={busy}
              onClick={() => finish(created)}
            >
              Skip for now
            </button>
          </div>
        </form>

        <FolderPickerModal
          isOpen={pickerOpen}
          initialPath={localPath}
          title="Select Documentation Folder"
          onSelect={handleFolderPicked}
          onClose={() => setPickerOpen(false)}
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
      {error ? (
        <p className="error">{error}</p>
      ) : created && created.repositories.length === 0 ? (
        <p className="faint">
          <strong>{created.name}</strong> has no repository yet, so the document
          tree is empty and chat has nothing to read. Register one from the
          Workspace panel whenever you are ready.
        </p>
      ) : (
        <p className="faint">Loading your documents…</p>
      )}
    </div>
  )
}
