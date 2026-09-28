import { useCallback, useEffect, useState, type DragEvent } from 'react'
import { ApiError, api, type Group, type GroupDocument } from '../api/client'

/**
 * The arrangement, as a board.
 *
 * Most groups are a view over documents: dragging between them rewrites rows in
 * the database and leaves the repository on disk exactly as it was. That is why
 * dragging is offered freely rather than behind a confirmation -- the reader can
 * correct Delphi as often as they like, and none of it touches their files.
 *
 * A group may also name a *folder*, and that changes what a drop means: the
 * document is proposed to move there, and the move happens only when the reader
 * accepts the proposal. So the two kinds of group are labelled on the card
 * rather than left to be discovered, because "where does this live" and "where
 * does this belong" are different questions and a drop can answer the second
 * without touching the first.
 */

interface Props {
  workspaceId: number
  /** Called when a document card is activated, to open it in the reading pane. */
  onOpenDocument: (repositoryId: number, path: string) => void
  /** Currently open document, so the board can show where it sits. */
  activeDocument?: { repositoryId: number; path: string } | null
  /**
   * The group whose details the sidebar is showing, and the way to change that.
   *
   * The board already knew everything the sidebar needs -- the members are in
   * `members` and the origin is on the card -- so this is a pointer, not a
   * second copy of the same state. `null` for both means the sidebar has nothing
   * selected, which is also what clearing the selection does.
   */
  selectedGroupId?: number | null
  onSelectGroup?: (groupId: number | null) => void
}

const ARCHIVE_NAME = 'Archief'

/** A document being dragged, carried in the dataTransfer payload. */
interface DragPayload {
  repositoryId: number
  path: string
  /** Where it came from, so the drop can leave that group. */
  fromGroupId: number | null
}

function readPayload(event: DragEvent): DragPayload | null {
  try {
    const raw = event.dataTransfer.getData('application/x-apollo-document')
    if (!raw) return null
    const parsed = JSON.parse(raw) as DragPayload
    return typeof parsed.repositoryId === 'number' && typeof parsed.path === 'string'
      ? parsed
      : null
  } catch {
    return null
  }
}

function fileName(path: string): string {
  return path.split('/').pop() ?? path
}

export function GroupsBoard({
  workspaceId,
  onOpenDocument,
  activeDocument,
  selectedGroupId = null,
  onSelectGroup = () => {},
}: Props) {
  const [groups, setGroups] = useState<Group[]>([])
  const [members, setMembers] = useState<Record<number, GroupDocument[]>>({})
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [dragOverId, setDragOverId] = useState<number | null>(null)
  const [dragging, setDragging] = useState<DragPayload | null>(null)
  const [newName, setNewName] = useState('')
  const [creating, setCreating] = useState(false)
  // Set when a drop produced a move proposal, so the board can say "there is a
  // card waiting" instead of leaving the reader to find it on another screen.
  const [filed, setFiled] = useState<string | null>(null)
  // The group whose folder is being edited, so only one card shows the field.
  const [editingFolderId, setEditingFolderId] = useState<number | null>(null)
  const [folderDraft, setFolderDraft] = useState('')

  const report = (e: unknown) =>
    setError(e instanceof ApiError ? e.detail : 'Something went wrong. Is the backend running?')

  const load = useCallback(async () => {
    setError(null)
    try {
      const list = await api.groups(workspaceId)
      setGroups(list)
      // The archive is only fetched when it exists, so a workspace that has
      // never archived anything does not pay for the request or gain an empty
      // group it never asked for.
      const filled = await Promise.all(
        list.map((g) =>
          api
            .groupDocuments(workspaceId, g.id)
            .then((docs) => [g.id, docs] as const)
            .catch(() => [g.id, [] as GroupDocument[]] as const),
        ),
      )
      setMembers(Object.fromEntries(filled))
    } catch (e) {
      report(e)
    } finally {
      setLoading(false)
    }
  }, [workspaceId])

  useEffect(() => {
    void load()
  }, [load])

  const createGroup = async (e: React.FormEvent) => {
    e.preventDefault()
    const name = newName.trim()
    // Refused here rather than sent and rejected: an empty name is a half-typed
    // field, not a request, and the server's 400 for it would be noise.
    if (!name) return
    setCreating(true)
    try {
      await api.createGroup(workspaceId, { name })
      setNewName('')
      await load()
    } catch (err) {
      report(err)
    } finally {
      setCreating(false)
    }
  }

  const onDocumentDragStart = (
    event: DragEvent,
    repositoryId: number,
    path: string,
    fromGroupId: number | null,
  ) => {
    const payload: DragPayload = { repositoryId, path, fromGroupId }
    event.dataTransfer.setData('application/x-apollo-document', JSON.stringify(payload))
    // Required by Firefox, and it is also what makes the drag image appear.
    event.dataTransfer.effectAllowed = 'move'
    setDragging(payload)
  }

  /**
   * The drop.
   *
   * One request. The source group is left in the same call, so a failure cannot
   * leave the document in both groups or in neither -- which is what would make
   * the board lie about the arrangement.
   *
   * The response says whether a *file* was proposed to move. That is the part
   * the reader must not have to guess: a drop into a group with a folder has
   * left a proposal waiting, and a drop into a view has not.
   */
  const onDrop = async (event: DragEvent, targetGroupId: number) => {
    event.preventDefault()
    setDragOverId(null)
    const payload = readPayload(event) ?? dragging
    setDragging(null)
    if (!payload) return

    // Dropping onto the group it is already in changes nothing, and says so
    // rather than looking like a failed action.
    if (payload.fromGroupId === targetGroupId) return

    try {
      const result = await api.moveInGroup(workspaceId, {
        repository_id: payload.repositoryId,
        path: payload.path,
        to_group_id: targetGroupId,
        ...(payload.fromGroupId !== null ? { from_group_id: payload.fromGroupId } : {}),
      })
      if (result.proposal_id !== null) {
        setFiled(
          `${fileName(payload.path)} can be moved into ${
            groups.find((g) => g.id === targetGroupId)?.folder ?? 'its folder'
          }. Review the proposal to let it happen.`,
        )
      } else {
        setFiled(null)
      }
      await load()
    } catch (err) {
      report(err)
    }
  }

  /**
   * Save the folder a group stands for.
   *
   * Written down, never derived from the group's name. An empty field clears it,
   * which is a real choice -- a group goes back to being a view -- so the field
   * says so instead of silently doing nothing.
   */
  const saveFolder = async (groupId: number) => {
    const value = folderDraft.trim()
    setEditingFolderId(null)
    try {
      await api.setGroupFolder(workspaceId, groupId, value === '' ? null : value)
      await load()
    } catch (err) {
      report(err)
    }
  }

  if (loading) {
    return <div className="groups-board-empty">Loading your groups…</div>
  }

  return (
    <div className="groups-board">
      <div className="groups-board-header">
        <div>
          <h2 className="groups-board-title">Groups</h2>
          {/* Says plainly what a group is, because it is the one idea here that
              is not obvious -- and now that some groups own a folder, the honest
              version mentions both kinds. */}
          <p className="groups-board-hint">
            Delphi found these, and you can change them. Dragging changes this
            arrangement; a group with a folder will ask you first before any file
            moves.
          </p>
        </div>
        <form onSubmit={createGroup} className="groups-new-form">
          <input
            className="groups-new-input"
            value={newName}
            onChange={(e) => setNewName(e.target.value)}
            placeholder="New group"
            aria-label="New group name"
            maxLength={200}
          />
          <button className="btn primary" type="submit" disabled={creating || !newName.trim()}>
            Add
          </button>
        </form>
      </div>

      {error && <div className="groups-board-error">{error}</div>}

      {/* Said right where the drop happened, because the alternative is a reader
          who believes their file moved -- or believes it did not -- and is wrong
          either way until they go looking on another screen. */}
      {filed && <div className="groups-board-filed">{filed}</div>}

      {groups.length === 0 ? (
        <div className="groups-board-empty">
          No groups yet. Delphi proposes them after a scan, and you can make your
          own here.
        </div>
      ) : (
        <div className="groups-grid">
          {groups.map((group) => {
            const docs = members[group.id] ?? []
            const isTarget = dragOverId === group.id
            return (
              <section
                key={group.id}
                className={[
                  'group-card',
                  group.is_archive ? 'group-card--archive' : '',
                  isTarget ? 'group-card--droptarget' : '',
                  selectedGroupId === group.id ? 'group-card--selected' : '',
                ]
                  .filter(Boolean)
                  .join(' ')}
                onDragOver={(e) => {
                  e.preventDefault()
                  e.dataTransfer.dropEffect = 'move'
                  setDragOverId(group.id)
                }}
                onDragLeave={() => setDragOverId((id) => (id === group.id ? null : id))}
                onDrop={(e) => void onDrop(e, group.id)}
              >
                <header className="group-card-header">
                  <h3 className="group-card-name">{group.name}</h3>
                  <span className="group-card-count">
                    {docs.length} {docs.length === 1 ? 'document' : 'documents'}
                  </span>
                  {/* A button, not the card itself. The card is a drop target and
                      holds a draggable list, so making the whole thing clickable
                      would put a click handler on top of both -- and a reader who
                      misses a drag would silently change what the panel shows. */}
                  <button
                    type="button"
                    className="group-card-details"
                    onClick={() =>
                      onSelectGroup(selectedGroupId === group.id ? null : group.id)
                    }
                    aria-pressed={selectedGroupId === group.id}
                    title="Show this group's members and findings in the panel"
                  >
                    {selectedGroupId === group.id ? 'Hide details' : 'Details'}
                  </button>
                </header>

                {/* The folder, and what it means. This is the distinction the
                    board has to make visible: a group with a folder turns a drop
                    into a proposal to move a file, and one without is only a
                    view. A reader cannot tell them apart from the name. */}
                {editingFolderId === group.id ? (
                  <form
                    className="group-folder-form"
                    onSubmit={(e) => {
                      e.preventDefault()
                      void saveFolder(group.id)
                    }}
                  >
                    <input
                      className="group-folder-input"
                      value={folderDraft}
                      onChange={(e) => setFolderDraft(e.target.value)}
                      placeholder="Folder name, or empty for a view"
                      aria-label={`Folder for ${group.name}`}
                      maxLength={200}
                      // The archive's folder is not the reader's to choose, and
                      // offering to change it would only earn a refusal.
                      disabled={group.is_archive}
                      autoFocus
                    />
                    <button className="btn" type="submit">
                      Save
                    </button>
                    <button
                      className="btn"
                      type="button"
                      onClick={() => setEditingFolderId(null)}
                    >
                      Cancel
                    </button>
                  </form>
                ) : (
                  <div className="group-folder">
                    <span className="group-folder-value">
                      {group.folder ? (
                        <>
                          <span className="group-folder-label">Folder</span>
                          <code>{group.folder}</code>
                        </>
                      ) : (
                        <span className="group-folder-label group-folder-label--muted">
                          View only — your files do not move
                        </span>
                      )}
                    </span>
                    {!group.is_archive && (
                      <button
                        type="button"
                        className="group-folder-edit"
                        onClick={() => {
                          setFolderDraft(group.folder ?? '')
                          setEditingFolderId(group.id)
                        }}
                      >
                        {group.folder ? 'Change' : 'Give it a folder'}
                      </button>
                    )}
                  </div>
                )}

                {group.description && (
                  <p className="group-card-description">{group.description}</p>
                )}

                {/* Origin, stated rather than implied. An arrangement Delphi
                    proposed and one the reader built look identical otherwise,
                    and the reader has to be able to tell which is which. */}
                {group.source === 'ai' && (
                  <span className="group-card-origin">
                    Delphi proposed this group
                  </span>
                )}

                {docs.length === 0 ? (
                  <p className="group-card-empty">
                    {group.is_archive
                      ? 'Nothing archived. Documents you archive are kept here, never deleted.'
                      : 'Empty — drag a document here.'}
                  </p>
                ) : (
                  <ul className="group-card-list">
                    {docs.map((doc) => {
                      const key = `${doc.repository_id}:${doc.path}`
                      const isOpen =
                        activeDocument?.repositoryId === doc.repository_id &&
                        activeDocument?.path === doc.path
                      return (
                        <li key={key}>
                          <button
                            type="button"
                            className={`group-document${isOpen ? ' group-document--open' : ''}`}
                            draggable
                            onDragStart={(e) =>
                              onDocumentDragStart(e, doc.repository_id, doc.path, group.id)
                            }
                            onDragEnd={() => {
                              setDragging(null)
                              setDragOverId(null)
                            }}
                            onClick={() => onOpenDocument(doc.repository_id, doc.path)}
                            title={doc.path}
                          >
                            <span className="group-document-name">{fileName(doc.path)}</span>
                            <span className="group-document-path">{doc.path}</span>
                          </button>
                        </li>
                      )
                    })}
                  </ul>
                )}
              </section>
            )
          })}
        </div>
      )}

      {groups.some((g) => g.is_archive) && (
        <p className="groups-board-footnote">
          {ARCHIVE_NAME} keeps documents, it does not remove them. Everything in it
          is still on disk and can be dragged back out.
        </p>
      )}
    </div>
  )
}
