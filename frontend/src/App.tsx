import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import {
  ApiError,
  api,
  type ChatStatus,
  type Conversation,
  type ConversationDetail,
  type Decision,
  type DocNode,
  type InventoryRun,
  type Mode,
  type OpenQuestion,
  type Proposal,
  type PulseItemPart,
  type PulseRun,
  type DocumentLinks,
  type Group,
  type GroupDocument,
  type GroupProposal,
  type InboxFile,
  type Signal,
  type Repository,
  type Workspace,
} from './api/client'
import { AddRepoModal } from './components/AddRepoModal'
import { SettingsModal } from './components/SettingsModal'
import { ContextSidebar } from './components/ContextSidebar'
import { FileContentColumn } from './components/FileContentColumn'
import { FolderContentsColumn, type ItemCard } from './components/FolderContentsColumn'
import { GroupsBoard } from './components/GroupsBoard'
import { LoginForm } from './components/LoginForm'
import { NavigationColumn, type NavSection } from './components/NavigationColumn'
import { ColumnResizer, useColumnResizers, MIN_DOCUMENT } from './columnResize'
import { NewObjectModal } from './components/NewObjectModal'
import { SetupWizard } from './components/SetupWizard'

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

export default function App() {
  const [workspaces, setWorkspaces] = useState<Workspace[]>([])
  const [workspace, setWorkspace] = useState<Workspace | null>(null)
  const [repository, setRepository] = useState<Repository | null>(null)
  const [tree, setTree] = useState<DocNode | null>(null)

  // Document & Selection State
  const [documentPath, setDocumentPath] = useState<string | null>(null)
  const [documentMarkdown, setDocumentMarkdown] = useState<string | null>(null)
  // The heading the open document declares, or null when it declares none.
  const [documentTitle, setDocumentTitle] = useState<string | null>(null)
  const [selectedItem, setSelectedItem] = useState<ItemCard | null>(null)

  // Navigation State
  //
  // The inbox is where a session starts, not "all objects". Every other section
  // is a way of looking at what is already here; this one is how documents
  // arrive, and a reader who cannot find it is stuck at step zero.
  const [activeSection, setActiveSection] = useState<NavSection>('inbox')
  // Which section the "Ingestion" tab returns to. Tracked separately from
  // activeSection so switching to the "Groups" tab and back does not forget
  // where the reader was -- 'groups' itself is never a valid value here.
  const lastIngestSection = useRef<NavSection>('inbox')
  useEffect(() => {
    if (activeSection !== 'groups') lastIngestSection.current = activeSection
  }, [activeSection])
  const [searchQuery, setSearchQuery] = useState('')

  // Questions, Decisions, Conversations, Proposals, Inventory
  const [questions, setQuestions] = useState<OpenQuestion[]>([])
  const [decisions, setDecisions] = useState<Decision[]>([])
  const [conversations, setConversations] = useState<Conversation[]>([])
  const [conversation, setConversation] = useState<ConversationDetail | null>(null)
  // The mode picked in the chat panel while no conversation exists yet. It is
  // applied to the conversation that the first message creates, so the user can
  // choose a mode before typing rather than being locked to 'explore'.
  const [pendingMode, setPendingMode] = useState<Mode>('explore')
  // Read for one thing only: the analysis button has to be disabled, with a
  // reason, when there is no model to do the analysis.
  const [chatStatus, setChatStatus] = useState<ChatStatus | null>(null)
  const [inventoryRun, setInventoryRun] = useState<InventoryRun | null>(null)
  const [proposals, setProposals] = useState<Proposal[]>([])
  const [pulseRun, setPulseRun] = useState<PulseRun | null>(null)
  const [pulseRunning, setPulseRunning] = useState(false)
  // The visual arrangement, loaded here only so the navigation badge can show
  // how many groups exist. The board itself loads its own members, because they
  // change on every drag and re-fetching them here would make a drop appear to
  // do nothing until something else happened to trigger a reload.
  const [groups, setGroups] = useState<Group[]>([])
  // The group whose details the panel is showing, and what the panel needs to
  // show them. Two pieces rather than one object: the group itself is already in
  // `groups`, so storing it again would be a second copy that can disagree.
  const [selectedGroupId, setSelectedGroupId] = useState<number | null>(null)
  const [groupMembers, setGroupMembers] = useState<GroupDocument[]>([])
  const [groupSignals, setGroupSignals] = useState<Signal[]>([])
  // Which groups the open document sits in. Null means "not read yet", which is
  // a different claim from "it is in no group" and the panel words them apart.
  const [documentGroups, setDocumentGroups] = useState<Group[] | null>(null)

  // Documents dropped in, and the repository they were stored in. Held here
  // rather than in the column because the navigation badge, the inbox column and
  // the reading pane all need the same answer, and three separate fetches could
  // disagree about it.
  const [inbox, setInbox] = useState<{ repository_id: number | null; files: InboxFile[] }>({
    repository_id: null,
    files: [],
  })

  /* Delphi's findings, and the two things they feed.

     The findings themselves belong to the open document, and are re-read
     whenever it changes -- including after a dismissal, so hiding one needs no
     second source of truth. The count belongs to the workspace, because the
     reader's real question is "is there anything I have not looked at yet", not
     "what is wrong with the file I happen to be reading". */
  const [signals, setSignals] = useState<Signal[]>([])
  const [openSignals, setOpenSignals] = useState(0)
  const [analysing, setAnalysing] = useState(false)
  // The pass's own account of itself, shown once above the list: what it read,
  // what it found, and what it did not do.
  const [delphiSummary, setDelphiSummary] = useState<string | null>(null)
  const [delphiErrors, setDelphiErrors] = useState<string[]>([])
  // The groups this pass proposed. Named in the report rather than left to be
  // discovered on the board, so a group the reader did not make is never mistaken
  // for one that was always there.
  const [delphiGroups, setDelphiGroups] = useState<GroupProposal[]>([])

  // UI Panels & Modals
  const [contextOpen, setContextOpen] = useState(true)
  // Counts how often AI Chat was asked for, so the panel can switch to its chat
  // tab in response. A counter, not a flag: opening chat twice must work twice.
  const [chatNonce, setChatNonce] = useState(0)
  const [newObjectModalOpen, setNewObjectModalOpen] = useState(false)
  const [addRepoModalOpen, setAddRepoModalOpen] = useState(false)
  const [settingsOpen, setSettingsOpen] = useState(false)
  // Said once after archiving, in the panel that has the button. "Archive" is a
  // word that suggests the file is gone, so the panel states plainly what did and
  // did not happen: the document is filed, the file has not moved.
  const [archiveNotice, setArchiveNotice] = useState<string | null>(null)

  // Async Status
  const [sending, setSending] = useState(false)
  const [busy, setBusy] = useState(false)
  const [_error, setError] = useState<string | null>(null)

  // Layout
  // Draggable column widths for the two list columns. Capped together at half
  // the screen so the document column can never be squeezed to nothing.
  const {
    widths: columnWidths,
    startDrag: startColumnDrag,
    nudge: nudgeColumn,
    reset: resetColumnWidths,
  } = useColumnResizers()
  /*
   * Auto-collapse of the navigation column. When the window (or the column
   * widths) leave the document too little room, the left navigation folds
   * away so the document -- the reason the app exists -- keeps a readable
   * width. `navCollapsed` is derived, not stored: it follows the layout on
   * every resize, and the menu button brings the column back as a flyout
   * overlay without changing the layout underneath.
   */
  const [navFlyoutOpen, setNavFlyoutOpen] = useState(false)
  const [viewportWidth, setViewportWidth] = useState(() => window.innerWidth)
  useEffect(() => {
    const onResize = () => setViewportWidth(window.innerWidth)
    window.addEventListener('resize', onResize)
    return () => window.removeEventListener('resize', onResize)
  }, [])
  // The Groups board also folds the navigation away, on top of the width
  // check: it wants the room for itself and the document beside it, not for
  // a sidebar whose sections are all in the other tab.
  const navCollapsed =
    activeSection === 'groups' ||
    viewportWidth - columnWidths.nav - columnWidths.contents < MIN_DOCUMENT + 480
  useEffect(() => {
    if (!navCollapsed) setNavFlyoutOpen(false)
  }, [navCollapsed])

  // Auth Status
  const [authRequired, setAuthRequired] = useState<boolean | null>(null)
  const [authenticated, setAuthenticated] = useState(false)

  const report = (e: unknown) =>
    setError(e instanceof ApiError ? e.detail : 'Something went wrong. Is the backend running?')

  const docsRepo = (ws: Workspace) =>
    ws.repositories.find((r) => r.kind === 'documentation') ?? null

  const reloadTree = useCallback(async (ws: Workspace, repo: Repository | null) => {
    if (!repo) {
      setTree(null)
      return
    }
    try {
      const res = await api.tree(ws.id, repo.id)
      setTree(res.root)
    } catch (e) {
      report(e)
    }
  }, [])

  /* The inbox listing.

     Refetched after a drop and on a workspace change rather than cached: the
     folder is the truth, and a file the reader added or removed outside the app
     has to show up here instead of in a list that quietly disagrees with the
     disk. A failure leaves an empty inbox rather than an error, because the drop
     zone beneath it reports its own failures and a listing that cannot be read
     must not stop files being added. */
  const refreshInbox = useCallback(async (ws: Workspace) => {
    try {
      const listing = await api.inbox(ws.id)
      setInbox({ repository_id: listing.repository_id, files: listing.files })
    } catch {
      setInbox({ repository_id: null, files: [] })
    }
  }, [])

  /* The number on the analysis button.

     Re-read whenever the workspace changes rather than remembered from the last
     analysis, so a restart cannot leave a badge claiming there is nothing to look
     at while three findings are open. A workspace whose count cannot be read
     shows no badge at all, which is a smaller claim than a wrong number. */
  const refreshSignalCount = useCallback(async (ws: Workspace) => {
    try {
      const count = await api.openSignalCount(ws.id)
      setOpenSignals(count.open_signals)
    } catch {
      setOpenSignals(0)
    }
  }, [])

  const openConversation = useCallback(async (workspaceId: number, conversationId: number) => {
    try {
      const detail = await api.getConversation(workspaceId, conversationId)
      setConversation(detail)
    } catch (e) {
      report(e)
    }
  }, [])

  const newConversation = useCallback(async (workspaceId: number) => {
    try {
      const created = await api.createConversation(workspaceId, 'explore')
      setConversations((prev) => [created, ...prev])
      setConversation({ ...created, messages: [] })
    } catch (e) {
      report(e)
    }
  }, [])

  const selectWorkspace = useCallback(
    async (ws: Workspace) => {
      setWorkspace(ws)
      setDocumentPath(null)
      setDocumentMarkdown(null)
      setDocumentTitle(null)
      setSelectedItem(null)
      setInventoryRun(null)
      setPulseRun(null)
      setError(null)

      const repo = docsRepo(ws)
      setRepository(repo)
      if (repo) {
        await reloadTree(ws, repo)
      } else {
        setTree(null)
      }

      try {
        const [status, convs, props, runs, qs, decs, pulseRuns, grps] = await Promise.all([
          api.chatStatus(ws.id).catch(() => null),
          api.listConversations(ws.id).catch(() => []),
          api.listProposals(ws.id).catch(() => []),
          api.listInventoryRuns(ws.id).catch(() => []),
          api.listQuestions(ws.id).catch(() => []),
          api.listDecisions(ws.id).catch(() => []),
          api.listPulseRuns(ws.id).catch(() => []),
          // Caught rather than allowed to fail the whole load: a workspace whose
          // groups cannot be read should still show its documents, and the board
          // reports its own error when opened.
          api.groups(ws.id).catch(() => []),
        ])
        setChatStatus(status)
        setConversations(convs)
        setProposals(props)
        setQuestions(qs)
        setDecisions(decs)
        setGroups(grps)
        await Promise.all([refreshInbox(ws), refreshSignalCount(ws)])
        setSignals([])
        setDelphiSummary(null)
        setDelphiErrors([])
        setDelphiGroups([])
        if (runs.length > 0) setInventoryRun(runs[0])
        // The newest Pulse run *with items*, not simply the newest run. A
        // scheduled scan on a repository whose documents are not committed yet
        // completes with nothing in it, and taking that run showed the reader an
        // empty Delphi Pulse view -- every previously found document and every
        // pending suggestion silently gone, with nothing on screen to explain
        // why. An empty run carries no information the older one does not.
        const usablePulse = pulseRuns.find((r) => (r.items?.length ?? 0) > 0)
        if (usablePulse) setPulseRun(usablePulse)
        else if (pulseRuns.length > 0) setPulseRun(pulseRuns[0])
        if (convs.length > 0) await openConversation(ws.id, convs[0].id)
        // No conversation is created just by looking at a workspace. The chat
        // creates one on the first message, so an unused workspace stays clean.
        else setConversation(null)
      } catch (e) {
        report(e)
      }
    },
    [newConversation, openConversation, refreshInbox, refreshSignalCount, reloadTree],
  )

  const refreshQuestions = useCallback(async () => {
    if (!workspace) return
    try {
      setQuestions(await api.listQuestions(workspace.id))
    } catch (e) {
      report(e)
    }
  }, [workspace])

  const refreshDecisions = useCallback(async () => {
    if (!workspace) return
    try {
      setDecisions(await api.listDecisions(workspace.id))
    } catch (e) {
      report(e)
    }
  }, [workspace])

  // Bootstrap data load
  useEffect(() => {
    api
      .listWorkspaces()
      .then((list) => {
        setWorkspaces(list)
        if (list.length > 0) selectWorkspace(list[0])
      })
      .catch(report)
  }, [selectWorkspace])

  // Auth check
  useEffect(() => {
    api
      .authStatus()
      .then((status) => {
        setAuthRequired(status.auth_required)
        setAuthenticated(status.authenticated)
      })
      .catch((e) => {
        setAuthRequired(false)
        report(e)
      })
  }, [])

  const loadWorkspaces = useCallback(async () => {
    const list = await api.listWorkspaces()
    setWorkspaces(list)
    if (list.length > 0) selectWorkspace(list[0])
  }, [selectWorkspace])

  const onAuthenticated = useCallback(() => {
    setAuthenticated(true)
    setError(null)
    void loadWorkspaces().catch(report)
  }, [loadWorkspaces])

  const onWorkspaceCreated = useCallback(
    async (created: Workspace) => {
      const fresh = await api.getWorkspace(created.id)
      setWorkspaces((prev) => {
        const others = prev.filter((w) => w.id !== fresh.id)
        return [...others, fresh].sort((a, b) => a.name.localeCompare(b.name))
      })
      await selectWorkspace(fresh)
    },
    [selectWorkspace],
  )

  const onDeleteWorkspace = useCallback(
    async (ws: Workspace) => {
      const ok = window.confirm(
        `Delete workspace "${ws.name}"?\n\nThis removes its repositories, conversations, decisions, questions, proposals and pulse runs from the app. Files on disk are not touched. This cannot be undone.`,
      )
      if (!ok) return
      try {
        await api.deleteWorkspace(ws.id)
        const remaining = await api.listWorkspaces()
        setWorkspaces(remaining)
        if (workspace?.id === ws.id) {
          if (remaining.length > 0) {
            await selectWorkspace(remaining[0])
          } else {
            setWorkspace(null)
            setRepository(null)
            setTree(null)
            setSelectedItem(null)
            setDocumentPath(null)
            setDocumentMarkdown(null)
            setDocumentTitle(null)
            setConversations([])
            setConversation(null)
            setDecisions([])
            setQuestions([])
            setProposals([])
            setInventoryRun(null)
            setPulseRun(null)
          }
        }
      } catch (e) {
        report(e)
      }
    },
    [selectWorkspace, workspace],
  )

  /* The document's real Markdown links, from the repository rather than from
     Delphi Pulse. Fetched per document, because they are the author's own
     links and change whenever the file changes -- caching them would let the
     panel describe relationships the text no longer contains. A failure leaves
     this null and the panel shows what it has rather than an error: the
     document is still readable without a link graph. */
  const [documentLinks, setDocumentLinks] = useState<DocumentLinks | null>(null)

  // Document selection
  //
  // `repositoryId` is optional because most callers already know which
  // repository they mean -- the reading pane, a link, a Pulse item. The groups
  // board cannot assume it: a group can hold documents from any repository this
  // workspace knows, and the active one is not necessarily where the dragged
  // document came from. Opening the wrong repository's file of the same name
  // would be a silent, plausible-looking wrong answer.
  const openDocument = useCallback(
    async (path: string, repositoryId?: number) => {
      if (!workspace || !repository) return
      const repoId = repositoryId ?? repository.id
      setDocumentPath(path)
      setDocumentMarkdown(null)
      setDocumentLinks(null)
      // The panel describes one thing at a time. Opening a document ends the
      // group's details rather than leaving them above a document that has
      // nothing to do with that group.
      setSelectedGroupId(null)
      // The document, its links and its findings are independent reads, so they
      // are not awaited one after the other: the text appears as soon as it is
      // ready, and a failure in either of the other two leaves the document
      // readable rather than showing an error where a document should be.
      const links = api
        .documentLinks(workspace.id, repoId, path)
        .then(setDocumentLinks)
        .catch(() => setDocumentLinks(null))
      const findings = api
        .signalsForDocument(workspace.id, repoId, path)
        .then(setSignals)
        .catch(() => setSignals([]))
      // Read with the same repository id as the text above. Asking with the
      // documentation repository while reading an inbox file returns an empty
      // answer rather than an error, which would read as "it is in no group".
      const belonging = api
        .groupsOfDocument(workspace.id, repoId, path)
        .then(setDocumentGroups)
        .catch(() => setDocumentGroups([]))
      try {
        const doc = await api.document(workspace.id, repoId, path)
        setDocumentMarkdown(doc.raw_markdown)
        // The title the file itself declares, kept so the sidebar can name this
        // document by it. A heading is what a reader calls the document; a path
        // is where it happens to sit today, and it changes the moment the
        // document is filed. Discarding this here meant every panel downstream
        // could only ever show a path.
        setDocumentTitle(doc.title || null)
      } catch (e) {
        report(e)
      }
      await Promise.all([links, findings, belonging])
    },
    [workspace, repository],
  )

  const handleSelectItem = useCallback(
    async (item: ItemCard) => {
      setSelectedItem(item)
      if (item.rawNode) {
        await openDocument(item.rawNode.path)
      } else if (item.rawDecision) {
        const d = item.rawDecision
        setDocumentPath(`decisions/ADR-${d.id}.md`)
        setDocumentTitle(`ADR-${d.id}: ${d.title}`)
        setDocumentMarkdown(
          `# ADR-${d.id}: ${d.title}\n\n**Status**: ${d.status.toUpperCase()}\n\n### Context\n${d.context || 'No context specified.'}\n\n### Decision\n${d.decision || 'No decision record specified.'}\n\n### Consequences\n${d.consequences || 'None recorded.'}`
        )
      } else if (item.rawQuestion) {
        const q = item.rawQuestion
        setDocumentPath(`questions/Q-${q.id}.md`)
        setDocumentTitle(q.title)
        setDocumentMarkdown(
          `# ${q.title}\n\n**Status**: ${q.status.toUpperCase()}\n\n### Context\n${q.description || 'Architectural question raised.'}`
        )
      } else if (item.rawConversation) {
        if (workspace) await openConversation(workspace.id, item.rawConversation.id)
      } else if (item.rawPulseItem) {
        /* A Pulse card is a real document, so it opens like one.

           This branch was missing, so every Pulse card fell through to the demo
           case below: documentPath became the card id ("pulse-1") instead of the
           file path, and the reading pane showed an id where a path belongs.
           Anything that reads documentPath -- the review panel, the context
           panel's links -- then had nothing real to match on, which is why a
           document with three known connections reported none. */
        /* The item names its repository: a run covers the documentation
           tree and the inbox, and opening the inbox copy from the docs
           repository (or the reverse) shows a plausible-looking wrong file. */
        await openDocument(item.rawPulseItem.file_path, item.rawPulseItem.repository_id ?? undefined)
      } else if (item.rawInboxFile) {
        /* An inbox document names its own repository, because it lives in
           Apollo's storage rather than in whatever folder happens to be open in
           the tree. Opening the wrong repository's file of the same name would
           be a plausible-looking wrong answer. */
        await openDocument(item.rawInboxFile.path, item.rawInboxFile.repositoryId)
      } else {
        // Demo card
        setDocumentPath(item.id)
        setDocumentMarkdown(null)
        setDocumentTitle(null)
      }
    },
    [openDocument, openConversation, workspace],
  )

  // Chat message sending
  const send = async (text: string) => {
    if (!workspace) return
    let activeConvId = conversation?.id
    if (!activeConvId) {
      const created = await api.createConversation(workspace.id, pendingMode)
      setConversations((prev) => [created, ...prev])
      setConversation({ ...created, messages: [] })
      activeConvId = created.id
    }
    setSending(true)
    setError(null)
    try {
      /* The open document rides along with the message, not with the
         conversation. It is a pointer for this turn only: the model is told
         which file "this" means and then reads that file itself, so an answer
         is grounded in what is on disk rather than in a copy the reading pane
         might be holding stale. A conversation outlives many documents, so
         pinning one to it would be wrong the moment the reader moves on. */
      await api.sendMessage(workspace.id, activeConvId, text, documentPath)
      setConversation(await api.getConversation(workspace.id, activeConvId))
    } catch (e) {
      report(e)
    } finally {
      setSending(false)
    }
  }

  const changeMode = async (mode: Mode) => {
    if (!workspace) return
    // Before the first message there is no conversation to patch yet, so the
    // choice is held and applied by send() when it creates one.
    if (!conversation) {
      setPendingMode(mode)
      return
    }
    setConversation({ ...conversation, mode })
    try {
      await api.setMode(workspace.id, conversation.id, mode)
    } catch (e) {
      report(e)
    }
  }

  /**
   * The Pulse suggestion belonging to the document in the reading pane, if any.
   *
   * The list cards carry `pulse-<id>` as their id, so the link back to the
   * suggestion is made through the selected item rather than by tracking a
   * second piece of state. Null unless the Pulse section is the one being
   * browsed, which keeps the review panel out of every other view.
   */
  const selectedPulseItem = useMemo(() => {
    if (activeSection !== 'pulse' || !pulseRun || !selectedItem) return null
    const id = selectedItem.id
    if (!id.startsWith('pulse-')) return null
    const pulseId = Number(id.slice('pulse-'.length))
    if (!Number.isFinite(pulseId)) return null
    return pulseRun.items.find((i) => i.id === pulseId) ?? null
  }, [activeSection, pulseRun, selectedItem])

  // Inventory & Proposals Actions
  const withTreeRefresh = async (action: () => Promise<void>) => {
    if (!workspace) return
    setBusy(true)
    try {
      await action()
      await reloadTree(workspace, repository)
    } catch (e) {
      report(e)
    } finally {
      setBusy(false)
    }
  }

  const applyAll = () =>
    inventoryRun &&
    workspace &&
    withTreeRefresh(() =>
      api.applyInventoryRun(workspace.id, inventoryRun.id).then(() => undefined),
    )

  const applyItem = (itemId: number) =>
    inventoryRun &&
    workspace &&
    withTreeRefresh(async () => {
      await api.applyInventoryItem(workspace.id, inventoryRun.id, itemId)
      setInventoryRun(await api.getInventoryRun(workspace.id, inventoryRun.id))
    })

  const skipItem = (itemId: number) =>
    inventoryRun &&
    workspace &&
    withTreeRefresh(async () => {
      await api.skipInventoryItem(workspace.id, inventoryRun.id, itemId)
      setInventoryRun(await api.getInventoryRun(workspace.id, inventoryRun.id))
    })

  const acceptProposal = (id: number) =>
    workspace &&
    withTreeRefresh(async () => {
      await api.acceptProposal(workspace.id, id)
      setProposals(await api.listProposals(workspace.id))
    })

  const rejectProposal = async (id: number) => {
    if (!workspace) return
    setBusy(true)
    try {
      await api.rejectProposal(workspace.id, id)
      setProposals(await api.listProposals(workspace.id))
    } catch (e) {
      report(e)
    } finally {
      setBusy(false)
    }
  }

  // Delphi Pulse actions
  const refreshPulseRun = async (workspaceId: number, runId: number) => {
    setPulseRun(await api.getPulseRun(workspaceId, runId))
  }

  /* Archiving, from the panel's action list.

     The same two steps as dragging onto the archive card, which is why this does
     not move a file: it files the document into the archive and a proposal to
     move it into `Archief/`, and the reader accepts or declines that separately.
     Saying so here matters, because "archive" is a word that suggests tidying
     away, and nothing is tidied away -- the file is readable at its old path
     until the proposal is accepted.

     The groups and the folder tree are both re-read afterwards, because the
     arrangement changed now and the file's location will change on acceptance.
     Leaving either stale would show the document as filed but not filed. */
  const archiveDocument = () =>
    workspace &&
    repository &&
    documentPath &&
    (async () => {
      setBusy(true)
      setError(null)
      try {
        const filed = await api.archiveDocument(workspace.id, repository.id, documentPath)
        // Read the answer back rather than guessing what it means: the document
        // now sits in the archive, and the arrangement and the folder tree both
        // say so. Anything left stale would show the document as filed on one
        // screen and not filed on the next.
        const [groupList, belonging] = await Promise.all([
          api.groups(workspace.id),
          api.groupsOfDocument(workspace.id, repository.id, filed.path),
        ])
        setGroups(groupList)
        setDocumentGroups(belonging)
        if (filed.proposal_id !== null) {
          setArchiveNotice(
            `${documentPath.split('/').pop()} is in the archive. A proposal to ` +
              `move the file into Archief/ is waiting for you — nothing has moved yet.`,
          )
        }
      } catch (e) {
        report(e)
      } finally {
        setBusy(false)
      }
    })()

  const runPulse = () =>
    workspace &&
    (async () => {
      setPulseRunning(true)
      setError(null)
      try {
        const run = await api.createPulseRun(workspace.id)
        setPulseRun(run)
      } catch (e) {
        report(e)
      } finally {
        setPulseRunning(false)
      }
    })()

  const applyPulseAll = () =>
    pulseRun &&
    workspace &&
    withTreeRefresh(async () => {
      await api.applyPulseRun(workspace.id, pulseRun.id)
      await refreshPulseRun(workspace.id, pulseRun.id)
    })

  /* Opening a document from the Pulse run list in the context sidebar. The
     list used to decide on the spot, which meant the same suggestion could be
     accepted from two places under two different names. Now it only navigates,
     and the reading pane above the document is where the choice is made. */
  const openPulseItem = useCallback(
    async (filePath: string) => {
      await openDocument(filePath)
    },
    [openDocument],
  )

  /* Why the last Pulse click did not take effect, and a wording for the common
     case. An untracked document is refused on purpose -- writing it would lose
     the original, since there is no earlier revision to fall back on -- but the
     server's sentence ("Refusing to overwrite an untracked document...") is a
     note to an implementer. The reader needs the fix, not the rule.

     Kept apart from the page-level error because it belongs next to the buttons
     that were just pressed; `report` puts it at the top of the window, which is
     why a refused accept looked like a dead button. */
  const [pulseError, setPulseError] = useState<string | null>(null)
  // Whether the last refusal was the "not in Git yet" one, which is the only
  // refusal the reader can undo from the panel. Kept beside the message rather
  // than re-derived from it, so the offered action and the words explaining it
  // cannot drift apart.
  const [pulseNeedsCommit, setPulseNeedsCommit] = useState(false)
  const [pulseCommitting, setPulseCommitting] = useState(false)

  const pulseFailure = (e: unknown) => {
    const detail = e instanceof ApiError ? e.detail : 'Something went wrong.'
    const untracked = /untracked|preserved through git/i.test(detail)
    setPulseNeedsCommit(untracked)
    setPulseError(
      untracked
        ? 'This document is not in Git yet, so writing to it would overwrite the original with no way back. Record the files first, then write.'
        : detail,
    )
    report(e)
  }

  const commitDocuments = async () => {
    if (!workspace) return
    setPulseCommitting(true)
    try {
      const result = await api.commitRepository(workspace.id, {
        message: 'Record current documentation state',
      })
      if (result.committed.length > 0) {
        setPulseError(
          `Recorded ${result.committed.length} file${
            result.committed.length === 1 ? '' : 's'
          } in Git. You can write the suggestion now.`,
        )
        setPulseNeedsCommit(false)
      } else {
        setPulseError('Everything is already recorded in Git. Try again.')
        setPulseNeedsCommit(false)
      }
    } catch (e) {
      report(e)
    } finally {
      setPulseCommitting(false)
    }
  }

  const applyPulsePart = (itemId: number, parts: PulseItemPart[]) =>
    pulseRun &&
    workspace &&
    withTreeRefresh(async () => {
      // The server records which halves are written, so there is nothing to
      // track here: reloading the run is the single source of truth.
      setPulseError(null)
      setPulseNeedsCommit(false)
      try {
        await api.applyPulseItem(workspace.id, pulseRun.id, itemId, parts)
        await refreshPulseRun(workspace.id, pulseRun.id)
      } catch (e) {
        pulseFailure(e)
      }
    })

  const skipPulseItem = (itemId: number) =>
    pulseRun &&
    workspace &&
    (async () => {
      setBusy(true)
      setPulseError(null)
      try {
        await api.skipPulseItem(workspace.id, pulseRun.id, itemId)
        await refreshPulseRun(workspace.id, pulseRun.id)
      } catch (e) {
        pulseFailure(e)
      } finally {
        setBusy(false)
      }
    })()

  // Object Creation Handlers
  const handleCreateDocument = async (title: string, path: string) => {
    setSelectedItem({
      id: path,
      title,
      snippet: `Document in ${path}`,
      type: 'DOC',
      tags: ['doc'],
    })
    setDocumentPath(path)
    setDocumentTitle(title)
    setDocumentMarkdown(`# ${title}\n\nStart writing notes and architectural specifications here...`)
  }

  const handleCreateDecision = async (title: string, context: string, decision: string) => {
    if (!workspace) return
    try {
      await api.createDecision(workspace.id, { title, context, decision })
      await refreshDecisions()
      setActiveSection('decisions')
    } catch (e) {
      report(e)
    }
  }

  const handleCreateQuestion = async (question: string, context: string) => {
    if (!workspace) return
    try {
      await api.createQuestion(workspace.id, { title: question, description: context })
      await refreshQuestions()
      setActiveSection('questions')
    } catch (e) {
      report(e)
    }
  }

  const handleCreateConversation = async () => {
    if (!workspace) return
    await newConversation(workspace.id)
    setActiveSection('conversations')
    setContextOpen(true)
  }

  // Opening AI Chat means the chat tab, not merely the panel: both the bubble in
  // the reading pane and the panel's own action go through here, so they cannot
  // disagree about where the reader lands.
  const openAiChat = () => {
    setContextOpen(true)
    setChatNonce((n) => n + 1)
  }

  /* After a drop, three things may have changed at once: the inbox list, the
     document tree, and the workspace's own repository list -- because the first
     upload is what creates the storage repository. Re-reading all three is what
     makes a document that was dropped a second ago openable straight away. */
  const onInboxStored = useCallback(async () => {
    if (!workspace) return
    await refreshInbox(workspace)
    const fresh = await api.getWorkspace(workspace.id).catch(() => workspace)
    setWorkspaces((prev) => prev.map((w) => (w.id === fresh.id ? fresh : w)))
    setWorkspace(fresh)
    // Before the first drop this workspace may have had no documentation
    // repository at all; now it has the storage one.
    const repo = docsRepo(fresh) ?? repository
    if (repo) {
      if (repo.id !== repository?.id) setRepository(repo)
      await reloadTree(fresh, repo)
    }
  }, [workspace, repository, refreshInbox, reloadTree])

  /* The one button.

     It reads the whole collection rather than the open document, because the
     question is which document stands out *among these*; answered one document
     at a time it would be a different question, and a worse one.

     The inbox is named explicitly when there is one, so the reader analyses the
     pile they just built rather than a folder they registered months ago.

     Left clickable with no model configured, rather than disabled: a disabled
     button only explains itself to someone who hovers it, and pressing it is
     exactly when the reader is asking why nothing happened. The check moved
     here so the answer is the same report a failed scan would show, not a
     tooltip nobody read. */
  const runDelphi = useCallback(async () => {
    if (!workspace) return
    if (!(chatStatus?.llm_configured ?? true)) {
      setDelphiErrors(['Delphi needs a model. Add one in Settings, then try again.'])
      return
    }
    setAnalysing(true)
    setDelphiErrors([])
    try {
      const result = await api.analyseDelphi(workspace.id, inbox.repository_id)
      setDelphiSummary(result.summary)
      setDelphiErrors(result.errors)
      setOpenSignals(result.open_signals)
      setDelphiGroups(result.groups)
      // The board may have gained a group, so it is re-read rather than guessed:
      // a proposal the reader cannot see is a proposal they cannot drag a
      // document out of. A failure here leaves the board as it was -- a refresh,
      // not news.
      await api
        .groups(workspace.id)
        .then(setGroups)
        .catch(() => {})
      // Re-read the open document rather than trusting the response list: the
      // response holds everything this pass found, and the bar above a document
      // should show that document's own findings, dismissed ones included out.
      if (documentPath) {
        const repoId = result.repository_id
        setSignals(await api.signalsForDocument(workspace.id, repoId, documentPath))
      }
    } catch (e) {
      report(e)
    } finally {
      setAnalysing(false)
    }
  }, [workspace, inbox.repository_id, documentPath, chatStatus])

  /* Show a group's details in the panel, or stop showing them.

     One request for the members and one for the findings, started together
     because neither is useful alone: a list of documents with nothing said about
     them, or findings about documents the reader cannot see, are each half an
     answer. Both are cleared first, so a slow second read cannot leave the
     previous group's documents on screen under the new group's name -- which is
     the one way this panel could be confidently wrong. */
  const selectGroup = useCallback(
    async (groupId: number | null) => {
      setSelectedGroupId(groupId)
      setGroupMembers([])
      setGroupSignals([])
      if (!workspace || groupId === null) return
      const [members, signals] = await Promise.all([
        api.groupDocuments(workspace.id, groupId).catch(() => []),
        api.signalsForGroup(workspace.id, groupId).catch(() => []),
      ])
      setGroupMembers(members)
      setGroupSignals(signals)
    },
    [workspace],
  )

  /* Hide one finding. The list is re-read rather than edited in place, so the bar
     cannot disagree with the server about what is still open -- and a dismissal
     survives the next analysis, which is the whole point of making one. */
  const dismissSignal = useCallback(
    async (signalId: number) => {
      if (!workspace || !documentPath || !repository) return
      try {
        await api.dismissSignal(workspace.id, signalId)
        // After, not before: re-reading first would fetch the list that still
        // contains the finding being dismissed.
        setSignals(
          await api.signalsForDocument(workspace.id, repository.id, documentPath),
        )
        const count = await api.openSignalCount(workspace.id)
        setOpenSignals(count.open_signals)
      } catch (e) {
        report(e)
      }
    },
    [workspace, repository, documentPath],
  )

  /* Open the document a finding names, in its own repository.

     The reference is a path, and a path belongs to a repository: opening it in
     whichever repository happens to be open would be a plausible-looking wrong
     answer. The finding knows which one it meant. */
  const openSignalReference = useCallback(
    async (signal: Signal) => {
      if (!signal.reference) return
      try {
        await openDocument(signal.reference, signal.repository_id)
      } catch (e) {
        report(e)
      }
    },
    [openDocument],
  )

  // Calculate object counts for Column 1
  const flatDocs = useMemo(() => flattenDocs(tree), [tree])
  // The folder picked while creating a workspace is registered as
  // 'documentation', so every kind belongs here. Filtering this down to
  // 'source' made the nav count (which counts all repositories) disagree
  // with the list, showing a badge of 1 above an empty column.
  const repositories = useMemo(() => workspace?.repositories || [], [workspace])

  const counts = useMemo(
    () => ({
      inbox: inbox.files.length,
      all: flatDocs.length,
      groups: groups.length,
      pendingGroups: groups.filter((g) => !g.reviewed).length,
      docs: flatDocs.length,
      repos: workspace?.repositories.length || 0,
      decisions: decisions.length,
      questions: questions.length,
      proposals: proposals.length,
      conversations: conversations.length,
      inventory: inventoryRun ? inventoryRun.items.length : 0,
      pulseWoven: pulseRun ? pulseRun.items.filter((i) => i.decision === 'pending').length : 0,
    }),
    [flatDocs.length, groups.length, workspace, decisions.length, questions.length, proposals.length, conversations.length, inventoryRun, pulseRun, inbox.files.length],
  )

  // Auth & Wizard gates
  if (authRequired === null) {
    return (
      <div className="mindstack-app-shell">
      </div>
    )
  }

  if (authRequired && !authenticated) {
    return (
      <div className="mindstack-app-shell">
        <div className="empty" style={{ paddingTop: 80 }}>
          <LoginForm onAuthenticated={onAuthenticated} />
        </div>
      </div>
    )
  }

  if (workspaces.length === 0) {
    return (
      <div className="mindstack-app-shell">
        <div className="empty" style={{ paddingTop: 60 }}>
          <SetupWizard onWorkspaceCreated={onWorkspaceCreated} />
        </div>
      </div>
    )
  }

  return (
    <div className="mindstack-app-shell">
      {/* 4-Column Layout */}
      <div
        className={[
          'mindstack-layout',
          contextOpen ? 'context-open' : 'context-closed',
          navCollapsed ? 'nav-collapsed' : '',
          activeSection === 'groups' ? 'groups-mode' : '',
        ]
          .filter(Boolean)
          .join(' ')}
        style={
          {
            '--col-nav-width': `${columnWidths.nav}px`,
            '--col-contents-width': `${columnWidths.contents}px`,
            // Zero means "not customised": the panel then keeps the CSS
            // clamp() default rather than collapsing to nothing.
            ...(columnWidths.context
              ? { '--col-context-width': `${columnWidths.context}px` }
              : {}),
          } as React.CSSProperties
        }
      >
        {/* Column 1: Navigation. When the layout collapses it (see
            navCollapsed above), the column becomes a flyout overlay and the
            menu button is the only trace of it -- the document keeps the room. */}
        {navCollapsed ? (
          <>
            <button
              type="button"
              className={`nav-flyout-btn ${navFlyoutOpen ? 'active' : ''}`}
              onClick={() => setNavFlyoutOpen((v) => !v)}
              title={navFlyoutOpen ? 'Close navigation' : 'Show navigation'}
              aria-expanded={navFlyoutOpen}
            >
              {navFlyoutOpen ? '✕' : '☰'}
            </button>
            {navFlyoutOpen && (
              <>
                <div
                  className="nav-flyout-backdrop"
                  onClick={() => setNavFlyoutOpen(false)}
                />
                <div className="nav-flyout">
                  <NavigationColumn
            workspace={workspace}
            workspaces={workspaces}
            activeSection={activeSection}
            onSelectSection={setActiveSection}
            counts={counts}
            onNewObject={() => setNewObjectModalOpen(true)}
            onFocusSearch={() => {
              const el = document.querySelector('.folder-search-input') as HTMLInputElement | null
              el?.focus()
            }}
            onSelectWorkspace={selectWorkspace}
            onAddRepository={() => setAddRepoModalOpen(true)}
            onOpenSettings={() => setSettingsOpen(true)}
            onDeleteWorkspace={onDeleteWorkspace}
            pulseActive={pulseRunning}
                  />
                </div>
              </>
            )}
          </>
        ) : (
          <NavigationColumn
            workspace={workspace}
            workspaces={workspaces}
            activeSection={activeSection}
            onSelectSection={setActiveSection}
            counts={counts}
            onNewObject={() => setNewObjectModalOpen(true)}
            onFocusSearch={() => {
              const el = document.querySelector('.folder-search-input') as HTMLInputElement | null
              el?.focus()
            }}
            onSelectWorkspace={selectWorkspace}
            onAddRepository={() => setAddRepoModalOpen(true)}
            onOpenSettings={() => setSettingsOpen(true)}
            onDeleteWorkspace={onDeleteWorkspace}
            pulseActive={pulseRunning}
          />
        )}
        <ColumnResizer
          side="nav"
          width={columnWidths.nav}
          onPointerDown={startColumnDrag('nav')}
          onNudge={(delta) => nudgeColumn('nav', delta)}
          onReset={resetColumnWidths}
        />

        {/* Column 2: two tabs above whichever it currently shows.

            "Ingestion" is every section this column has always had (inbox,
            docs, decisions, and the rest); "Groups" is the board. The board
            replaces the object list rather than sitting beside it: it is a
            different thing to look at, not another filter of the same list,
            and showing both would halve the width each gets for no gain. */}
        <div className="mindstack-middle">
          <div className="mindstack-tabbar" role="tablist" aria-label="Ingestion or Groups">
            <button
              type="button"
              role="tab"
              aria-selected={activeSection !== 'groups'}
              className={`mindstack-tab${activeSection !== 'groups' ? ' active' : ''}`}
              onClick={() => setActiveSection(lastIngestSection.current)}
            >
              Ingestion
            </button>
            <button
              type="button"
              role="tab"
              aria-selected={activeSection === 'groups'}
              className={`mindstack-tab${activeSection === 'groups' ? ' active' : ''}`}
              onClick={() => {
                setActiveSection('groups')
                // Closed on entry, every time: the board and the document
                // beside it want the width the context panel would take, and
                // a reader who wants it back can still open it themselves.
                setContextOpen(false)
              }}
              title="Which documents belong together"
            >
              Groups
              {counts.pendingGroups > 0 && (
                <span className="mindstack-tab-count">{counts.pendingGroups}</span>
              )}
            </button>
          </div>

          {activeSection === 'groups' ? (
            <div className="folder-contents-column folder-contents-column--board">
              <GroupsBoard
                workspaceId={workspace!.id}
                onOpenDocument={(repositoryId, path) => void openDocument(path, repositoryId)}
                selectedGroupId={selectedGroupId}
                onSelectGroup={(groupId) => void selectGroup(groupId)}
                activeDocument={
                  documentPath && repository
                    ? { repositoryId: repository.id, path: documentPath }
                    : null
                }
                tree={tree}
                treeRepositoryId={repository?.id ?? null}
              />
            </div>
          ) : (
            <FolderContentsColumn
              activeSection={activeSection}
              searchQuery={searchQuery}
              onSearchChange={setSearchQuery}
              workspace={workspace}
              repository={repository}
              tree={tree}
              selectedId={selectedItem?.id || documentPath}
              onSelectItem={handleSelectItem}
              onNewItem={() => setNewObjectModalOpen(true)}
              decisions={decisions}
              questions={questions}
              conversations={conversations}
              repositories={repositories}
              pulseItems={pulseRun?.items || []}
              pulseRunning={pulseRunning}
              onRunPulse={runPulse}
              onOpenPulseSettings={() => setSettingsOpen(true)}
              inboxFiles={inbox.files}
              inboxRepositoryId={inbox.repository_id}
              workspaceId={workspace?.id ?? null}
              onInboxStored={onInboxStored}
              onRunDelphi={runDelphi}
              analysing={analysing}
              llmConfigured={chatStatus?.llm_configured ?? true}
              openFindings={openSignals}
              delphiSummary={delphiSummary}
              delphiErrors={delphiErrors}
              delphiGroups={delphiGroups}
              onDismissDelphiReport={() => {
                setDelphiSummary(null)
                setDelphiErrors([])
                setDelphiGroups([])
              }}
            />
          )}
        </div>

        {/* Not shown in Groups: the board and the document split the space
            evenly there instead of at a remembered pixel width, so there is
            no boundary this handle's own math would agree with. */}
        {activeSection !== 'groups' && (
          <ColumnResizer
            side="contents"
            width={columnWidths.nav + columnWidths.contents}
            onPointerDown={startColumnDrag('contents')}
            onNudge={(delta) => nudgeColumn('contents', delta)}
            onReset={resetColumnWidths}
          />
        )}

        {/* Column 3: File Content Canvas */}
        <FileContentColumn
          selectedItem={selectedItem}
          documentMarkdown={documentMarkdown}
          repository={repository}
          signals={signals}
          onDismissSignal={(id) => void dismissSignal(id)}
          onOpenReference={(signal) => void openSignalReference(signal)}
          pulseItem={selectedPulseItem}
          busy={busy}
          onApplyPulsePart={applyPulsePart}
          onSkipPulseItem={skipPulseItem}
          pulseError={pulseError}
          onDismissPulseError={() => {
            setPulseError(null)
            setPulseNeedsCommit(false)
          }}
          pulseNeedsCommit={pulseNeedsCommit}
          pulseCommitting={pulseCommitting}
          onCommitDocuments={commitDocuments}
          onOpenAiChat={openAiChat}
          onToggleContext={() => setContextOpen((v) => !v)}
          contextOpen={contextOpen}
          onDelete={() => {
            setSelectedItem(null)
            setDocumentPath(null)
            setDocumentMarkdown(null)
            setDocumentTitle(null)
          }}
        />

        {/* Column 4: Collapsible Context Sidebar coming from the right */}
        {contextOpen && (
          <ColumnResizer
            side="context"
            width={columnWidths.context || 0}
            onPointerDown={startColumnDrag('context')}
            onNudge={(delta) => nudgeColumn('context', delta)}
            onReset={resetColumnWidths}
          />
        )}
        <ContextSidebar
          isOpen={contextOpen}
          onClose={() => setContextOpen(false)}
          repository={repository}
          documentPath={documentPath}
          documentMarkdown={documentMarkdown}
          documentTitle={documentTitle}
          inventoryRun={inventoryRun}
          proposals={proposals}
          conversations={conversations}
          activeConversation={conversation}
          pendingMode={pendingMode}
          onSendMessage={send}
          onModeChange={changeMode}
          sending={sending}
          busy={busy}
          onAcceptProposal={acceptProposal}
          onRejectProposal={rejectProposal}
          onApplyInventoryAll={applyAll}
          onApplyInventoryItem={applyItem}
          onSkipInventoryItem={skipItem}
          pulseRun={pulseRun}
          onAcceptPulseAll={applyPulseAll}
          onOpenPulseItem={openPulseItem}
          onOpenDocument={(path) => void openDocument(path)}
          documentLinks={documentLinks}
          onOpenAiChat={openAiChat}
          chatNonce={chatNonce}
          onRunPulse={workspace ? runPulse : undefined}
          documentGroups={documentGroups}
          selectedGroup={groups.find((g) => g.id === selectedGroupId) ?? null}
          groupMembers={groupMembers}
          groupSignals={groupSignals}
          onOpenGroup={(groupId) => void selectGroup(groupId)}
          onOpenDocumentIn={(repositoryId, path) => void openDocument(path, repositoryId)}
          onArchive={workspace ? () => void archiveDocument() : undefined}
          archiveNotice={archiveNotice}
          onDismissArchiveNotice={() => setArchiveNotice(null)}
        />
      </div>

      {/* New Object Modal */}
      <NewObjectModal
        isOpen={newObjectModalOpen}
        onClose={() => setNewObjectModalOpen(false)}
        onCreateDocument={handleCreateDocument}
        onCreateDecision={handleCreateDecision}
        onCreateQuestion={handleCreateQuestion}
        onCreateConversation={handleCreateConversation}
        onAddRepo={() => setAddRepoModalOpen(true)}
      />

      {/* Add Repository Modal */}
      {workspace && (
        <AddRepoModal
          isOpen={addRepoModalOpen}
          workspaceId={workspace.id}
          hasDocRepo={workspace.repositories.some((r) => r.kind === 'documentation')}
          onClose={() => setAddRepoModalOpen(false)}
          onSuccess={async () => {
            const updated = await api.getWorkspace(workspace.id)
            await selectWorkspace(updated)
          }}
        />
      )}
      <SettingsModal
        isOpen={settingsOpen}
        onClose={() => setSettingsOpen(false)}
        workspaceId={workspace?.id ?? null}
        onSavedPulse={async () => {
          // Only the latest run is affected, and only its configuration. The
          // document being read is untouched, so the workspace is not reloaded.
          if (!workspace) return
          try {
            setPulseRun(
              await api
                .listPulseRuns(workspace.id)
                // Same rule as on workspace load: the newest run with items in
                // it, so saving the settings can never blank the Pulse view.
                .then((runs) => runs.find((r) => (r.items?.length ?? 0) > 0) ?? null)
                .catch(() => null),
            )
          } catch (e) {
            report(e)
          }
        }}
        onSaved={async () => {
          // Only the model behind the API changed. Re-selecting the workspace
          // here used to clear the open document, the selected list item and
          // the loaded runs, so saving a setting threw away the reader's place
          // and sent them back to "No document selected". Refresh the one thing
          // that actually depends on the new configuration instead.
          if (!workspace) return
          try {
            setChatStatus(await api.chatStatus(workspace.id).catch(() => null))
          } catch (e) {
            report(e)
          }
        }}
        onDatabaseReset={async () => {
          // The data really is gone, so this is the full teardown.
          const remaining = await api.listWorkspaces()
          setWorkspaces(remaining)
          if (remaining.length > 0) {
            await selectWorkspace(remaining[0])
          } else {
            setWorkspace(null)
            setRepository(null)
            setTree(null)
            setSelectedItem(null)
            setDocumentPath(null)
            setDocumentMarkdown(null)
            setDocumentTitle(null)
            setConversations([])
            setConversation(null)
            setDecisions([])
            setQuestions([])
            setProposals([])
            setInventoryRun(null)
            setPulseRun(null)
          }
        }}
      />
    </div>
  )
}
