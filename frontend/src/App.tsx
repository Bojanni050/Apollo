import { useCallback, useEffect, useMemo, useState } from 'react'
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
  type PulseRun,
  type Repository,
  type Workspace,
} from './api/client'
import { AddRepoModal } from './components/AddRepoModal'
import { SettingsModal } from './components/SettingsModal'
import { PulseSettingsModal } from './components/PulseSettingsModal'
import { ContextSidebar } from './components/ContextSidebar'
import { FileContentColumn } from './components/FileContentColumn'
import { FolderContentsColumn, type ItemCard } from './components/FolderContentsColumn'
import { LoginForm } from './components/LoginForm'
import { NavigationColumn, type NavSection } from './components/NavigationColumn'
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
  const [selectedItem, setSelectedItem] = useState<ItemCard | null>(null)

  // Navigation State
  const [activeSection, setActiveSection] = useState<NavSection>('all')
  const [searchQuery, setSearchQuery] = useState('')

  // Questions, Decisions, Conversations, Proposals, Inventory
  const [questions, setQuestions] = useState<OpenQuestion[]>([])
  const [decisions, setDecisions] = useState<Decision[]>([])
  const [conversations, setConversations] = useState<Conversation[]>([])
  const [conversation, setConversation] = useState<ConversationDetail | null>(null)
  const [_chatStatus, setChatStatus] = useState<ChatStatus | null>(null)
  const [inventoryRun, setInventoryRun] = useState<InventoryRun | null>(null)
  const [proposals, setProposals] = useState<Proposal[]>([])
  const [pulseRun, setPulseRun] = useState<PulseRun | null>(null)
  const [pulseRunning, setPulseRunning] = useState(false)

  // UI Panels & Modals
  const [contextOpen, setContextOpen] = useState(true)
  const [newObjectModalOpen, setNewObjectModalOpen] = useState(false)
  const [addRepoModalOpen, setAddRepoModalOpen] = useState(false)
  const [settingsOpen, setSettingsOpen] = useState(false)
  const [pulseSettingsOpen, setPulseSettingsOpen] = useState(false)

  // Async Status
  const [sending, setSending] = useState(false)
  const [busy, setBusy] = useState(false)
  const [_error, setError] = useState<string | null>(null)

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
        const [status, convs, props, runs, qs, decs, pulseRuns] = await Promise.all([
          api.chatStatus(ws.id).catch(() => null),
          api.listConversations(ws.id).catch(() => []),
          api.listProposals(ws.id).catch(() => []),
          api.listInventoryRuns(ws.id).catch(() => []),
          api.listQuestions(ws.id).catch(() => []),
          api.listDecisions(ws.id).catch(() => []),
          api.listPulseRuns(ws.id).catch(() => []),
        ])
        setChatStatus(status)
        setConversations(convs)
        setProposals(props)
        setQuestions(qs)
        setDecisions(decs)
        if (runs.length > 0) setInventoryRun(runs[0])
        if (pulseRuns.length > 0) setPulseRun(pulseRuns[0])
        if (convs.length > 0) await openConversation(ws.id, convs[0].id)
        else await newConversation(ws.id)
      } catch (e) {
        report(e)
      }
    },
    [newConversation, openConversation, reloadTree],
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

  // Document selection
  const openDocument = useCallback(
    async (path: string) => {
      if (!workspace || !repository) return
      setDocumentPath(path)
      setDocumentMarkdown(null)
      try {
        const doc = await api.document(workspace.id, repository.id, path)
        setDocumentMarkdown(doc.raw_markdown)
      } catch (e) {
        report(e)
      }
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
        setDocumentMarkdown(
          `# ADR-${d.id}: ${d.title}\n\n**Status**: ${d.status.toUpperCase()}\n\n### Context\n${d.context || 'No context specified.'}\n\n### Decision\n${d.decision || 'No decision record specified.'}\n\n### Consequences\n${d.consequences || 'None recorded.'}`
        )
      } else if (item.rawQuestion) {
        const q = item.rawQuestion
        setDocumentPath(`questions/Q-${q.id}.md`)
        setDocumentMarkdown(
          `# ${q.title}\n\n**Status**: ${q.status.toUpperCase()}\n\n### Context\n${q.description || 'Architectural question raised.'}`
        )
      } else if (item.rawConversation) {
        if (workspace) await openConversation(workspace.id, item.rawConversation.id)
      } else {
        // Demo card
        setDocumentPath(item.id)
        setDocumentMarkdown(null)
      }
    },
    [openDocument, openConversation, workspace],
  )

  // Chat message sending
  const send = async (text: string) => {
    if (!workspace) return
    let activeConvId = conversation?.id
    if (!activeConvId) {
      const created = await api.createConversation(workspace.id, 'explore')
      setConversations((prev) => [created, ...prev])
      setConversation({ ...created, messages: [] })
      activeConvId = created.id
    }
    setSending(true)
    setError(null)
    try {
      await api.sendMessage(workspace.id, activeConvId, text)
      setConversation(await api.getConversation(workspace.id, activeConvId))
    } catch (e) {
      report(e)
    } finally {
      setSending(false)
    }
  }

  const changeMode = async (mode: Mode) => {
    if (!workspace || !conversation) return
    setConversation({ ...conversation, mode })
    try {
      await api.setMode(workspace.id, conversation.id, mode)
    } catch (e) {
      report(e)
    }
  }

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

  const applyPulseItem = (itemId: number) =>
    pulseRun &&
    workspace &&
    withTreeRefresh(async () => {
      await api.applyPulseItem(workspace.id, pulseRun.id, itemId)
      await refreshPulseRun(workspace.id, pulseRun.id)
    })

  const skipPulseItem = (itemId: number) =>
    pulseRun &&
    workspace &&
    (async () => {
      setBusy(true)
      try {
        await api.skipPulseItem(workspace.id, pulseRun.id, itemId)
        await refreshPulseRun(workspace.id, pulseRun.id)
      } catch (e) {
        report(e)
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

  // Calculate object counts for Column 1
  const flatDocs = useMemo(() => flattenDocs(tree), [tree])
  const sources = useMemo(
    () => workspace?.repositories.filter((r) => r.kind === 'source') || [],
    [workspace],
  )

  const counts = useMemo(
    () => ({
      all: Math.max(flatDocs.length, 4),
      docs: flatDocs.length,
      repos: workspace?.repositories.length || 0,
      decisions: decisions.length,
      questions: questions.length,
      proposals: proposals.length,
      conversations: conversations.length,
      inventory: inventoryRun ? inventoryRun.items.length : 0,
      pulseWoven: pulseRun ? pulseRun.items.filter((i) => i.decision === 'pending').length : 0,
    }),
    [flatDocs.length, workspace, decisions.length, questions.length, proposals.length, conversations.length, inventoryRun, pulseRun],
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
      <div className={`mindstack-layout ${contextOpen ? 'context-open' : 'context-closed'}`}>
        {/* Column 1: Navigation */}
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
        />

        {/* Column 2: Folder Contents */}
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
          sources={sources}
          pulseItems={pulseRun?.items || []}
          pulseRunning={pulseRunning}
          onRunPulse={runPulse}
          onOpenPulseSettings={() => setPulseSettingsOpen(true)}
        />

        {/* Column 3: File Content Canvas */}
        <FileContentColumn
          selectedItem={selectedItem}
          documentMarkdown={documentMarkdown}
          repository={repository}
          onOpenAiChat={() => {
            setContextOpen(true)
          }}
          onToggleContext={() => setContextOpen((v) => !v)}
          contextOpen={contextOpen}
          onDelete={() => {
            setSelectedItem(null)
            setDocumentPath(null)
            setDocumentMarkdown(null)
          }}
        />

        {/* Column 4: Collapsible Context Sidebar coming from the right */}
        <ContextSidebar
          isOpen={contextOpen}
          onClose={() => setContextOpen(false)}
          repository={repository}
          documentPath={documentPath}
          documentMarkdown={documentMarkdown}
          inventoryRun={inventoryRun}
          proposals={proposals}
          conversations={conversations}
          activeConversation={conversation}
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
          onApplyPulseAll={applyPulseAll}
          onApplyPulseItem={applyPulseItem}
          onSkipPulseItem={skipPulseItem}
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
        onSaved={async () => {
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
      {workspace && (
        <PulseSettingsModal
          isOpen={pulseSettingsOpen}
          workspaceId={workspace.id}
          onClose={() => setPulseSettingsOpen(false)}
          onSaved={() => selectWorkspace(workspace)}
        />
      )}
    </div>
  )
}
