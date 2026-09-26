import { useCallback, useEffect, useState } from 'react'
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
  type Repository,
  type Workspace,
} from './api/client'
import { ConversationPanel } from './components/ConversationPanel'
import { ContextPanel } from './components/ContextPanel'
import { DecisionsPanel } from './components/DecisionsPanel'
import { LoginForm } from './components/LoginForm'
import { QuestionsPanel } from './components/QuestionsPanel'
import { SynthesisView } from './components/SynthesisView'
import { SetupWizard } from './components/SetupWizard'
import { WorkspacePanel } from './components/WorkspacePanel'

export default function App() {
  const [workspaces, setWorkspaces] = useState<Workspace[]>([])
  const [workspace, setWorkspace] = useState<Workspace | null>(null)
  const [repository, setRepository] = useState<Repository | null>(null)
  const [tree, setTree] = useState<DocNode | null>(null)
  const [documentPath, setDocumentPath] = useState<string | null>(null)
  const [documentMarkdown, setDocumentMarkdown] = useState<string | null>(null)
  const [showRaw, setShowRaw] = useState(false)

  const [view, setView] = useState<'conversation' | 'synthesis' | 'questions' | 'decisions'>('synthesis')
  const [questions, setQuestions] = useState<OpenQuestion[]>([])
  const [decisions, setDecisions] = useState<Decision[]>([])

  const [conversations, setConversations] = useState<Conversation[]>([])
  const [conversation, setConversation] = useState<ConversationDetail | null>(null)
  const [chatStatus, setChatStatus] = useState<ChatStatus | null>(null)

  const [inventoryRun, setInventoryRun] = useState<InventoryRun | null>(null)
  const [proposals, setProposals] = useState<Proposal[]>([])

  const [contextOpen, setContextOpen] = useState(true)
  const [sending, setSending] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  // null = not yet known. The API is the only authority on whether a session
  // exists; the UI must never assume it is authenticated.
  const [authRequired, setAuthRequired] = useState<boolean | null>(null)
  const [authenticated, setAuthenticated] = useState(false)

  const report = (e: unknown) =>
    setError(e instanceof ApiError ? e.detail : 'Something went wrong. Is the backend running?')

  const docsRepo = (ws: Workspace) =>
    ws.repositories.find((r) => r.kind === 'documentation') ?? null

  const reloadTree = useCallback(
    async (ws: Workspace, repo: Repository | null) => {
      if (!repo) {
        setTree(null)
        return
      }
      setTree((await api.tree(ws.id, repo.id)).root)
    },
    [],
  )

  const openConversation = useCallback(async (workspaceId: number, conversationId: number) => {
    setConversation(await api.getConversation(workspaceId, conversationId))
  }, [])

  const newConversation = useCallback(async (workspaceId: number) => {
    const created = await api.createConversation(workspaceId, 'explore')
    setConversations((prev) => [created, ...prev])
    setConversation({ ...created, messages: [] })
  }, [])

  const selectWorkspace = useCallback(
    async (ws: Workspace) => {
      setWorkspace(ws)
      setDocumentPath(null)
      setDocumentMarkdown(null)
      setInventoryRun(null)
      setError(null)

      const repo = docsRepo(ws)
      setRepository(repo)
      try {
        await reloadTree(ws, repo)
      } catch (e) {
        report(e)
      }

      try {
        const [status, convs, props, runs, qs, decs] = await Promise.all([
          api.chatStatus(ws.id),
          api.listConversations(ws.id),
          api.listProposals(ws.id),
          api.listInventoryRuns(ws.id),
          api.listQuestions(ws.id),
          api.listDecisions(ws.id),
        ])
        setChatStatus(status)
        setConversations(convs)
        setProposals(props)
        setQuestions(qs)
        setDecisions(decs)
        if (runs.length > 0) setInventoryRun(runs[0])
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

  // -- bootstrap ---------------------------------------------------------
  useEffect(() => {
    api
      .listWorkspaces()
      .then((list) => {
        setWorkspaces(list)
        if (list.length > 0) selectWorkspace(list[0])
      })
      .catch(report)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  // Ask the backend whether we need to sign in. Until it answers, render
  // nothing: fetching data first would flash an error for a signed-in user
  // whose session is perfectly valid.
  useEffect(() => {
    api
      .authStatus()
      .then((status) => {
        setAuthRequired(status.auth_required)
        setAuthenticated(status.authenticated)
      })
      .catch((e) => {
        // An unreachable backend is not an auth decision; show the app error.
        setAuthRequired(false)
        report(e)
      })
    // eslint-disable-next-line react-hooks/exhaustive-deps
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

  const signOut = useCallback(async () => {
    try {
      await api.logout()
    } catch {
      // The cookie is cleared regardless; carry on to the login screen.
    }
    setAuthenticated(false)
    setWorkspace(null)
    setConversation(null)
    setTree(null)
    setDocumentMarkdown(null)
  }, [])

  // Called by the setup wizard once a workspace exists. Selecting it here means
  // the app is usable immediately, and re-running it after a repository is
  // registered reloads the workspace so the new document tree appears.
  //
  // This must be declared before ANY early return below. Hooks called after a
  // conditional return are only registered on the renders that reach them, and
  // React then fails with "Rendered more hooks than during the previous render"
  // the moment the condition flips -- which is exactly what happens on the
  // first workspace creation.
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



  // -- actions -----------------------------------------------------------

  const openDocument = useCallback(
    async (path: string) => {
      if (!workspace || !repository) return
      setDocumentPath(path)
      setDocumentMarkdown(null)
      setView('synthesis')
      setContextOpen(true)
      try {
        const doc = await api.document(workspace.id, repository.id, path)
        setDocumentMarkdown(doc.raw_markdown)
      } catch (e) {
        report(e)
      }
    },
    [workspace, repository],
  )

  const send = async (text: string) => {
    if (!workspace || !conversation) return
    setSending(true)
    setError(null)
    try {
      await api.sendMessage(workspace.id, conversation.id, text)
      setConversation(await api.getConversation(workspace.id, conversation.id))
    } catch (e) {
      report(e)
    } finally {
      setSending(false)
    }
  }

  const changeMode = async (mode: Mode) => {
    if (!workspace || !conversation) return
    // Update locally first: the mode only changes how the AI behaves, and the
    // conversation itself is untouched.
    setConversation({ ...conversation, mode })
    try {
      await api.setMode(workspace.id, conversation.id, mode)
    } catch (e) {
      report(e)
    }
  }

  const refreshInventory = async (runId: number) => {
    if (!workspace) return
    setInventoryRun(await api.getInventoryRun(workspace.id, runId))
  }

  const runInventory = async () => {
    if (!workspace) return
    setBusy(true)
    setError(null)
    try {
      setInventoryRun(await api.createInventoryRun(workspace.id))
    } catch (e) {
      report(e)
    } finally {
      setBusy(false)
    }
  }

  // Any accepted change moves files, so the tree must be reloaded afterwards.
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
      await refreshInventory(inventoryRun.id)
    })

  const skipItem = (itemId: number) =>
    inventoryRun &&
    workspace &&
    withTreeRefresh(async () => {
      await api.skipInventoryItem(workspace.id, inventoryRun.id, itemId)
      await refreshInventory(inventoryRun.id)
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

  // -- render ------------------------------------------------------------

  // While the auth question is unanswered, render only the frame. Attempting a
  // data load first would either flash an error or issue a request the backend
  // is about to reject.
  if (authRequired === null) {
    return (
      <div className="app">
        <div className="titlebar">
          <h1>Gaia Docs Architect</h1>
        </div>
      </div>
    )
  }

  if (authRequired && !authenticated) {
    return (
      <div className="app">
        <div className="titlebar">
          <h1>Gaia Docs Architect</h1>
        </div>
        <div className="empty" style={{ paddingTop: 80 }}>
          <LoginForm onAuthenticated={onAuthenticated} />
        </div>
      </div>
    )
  }

  if (workspaces.length === 0) {
    return (
      <div className="app">
        <div className="titlebar">
          <h1>Gaia Docs Architect</h1>
        </div>
        <div className="empty" style={{ paddingTop: 60 }}>
          <SetupWizard onWorkspaceCreated={onWorkspaceCreated} />
        </div>
      </div>
    )
  }

  return (
    <div className="app">
      <div className="titlebar">
        <div className="brand-wrapper">
          <div className="brand-icon">
            <svg width="16" height="16" viewBox="0 0 24 24" fill="currentColor">
              <path d="M4 19.5v-15A2.5 2.5 0 0 1 6.5 2H20v20H6.5a2.5 2.5 0 0 1-2.5-2.5Z"/>
              <path d="M6 6h10M6 10h10M6 14h6" stroke="#FFFFFF" strokeWidth="1.5" strokeLinecap="round"/>
            </svg>
          </div>
          <div className="brand-text-block">
            <h1 className="brand-title">Chronicle</h1>
            <span className="brand-subtitle">ARCHIVE &amp; SYNTHESIS</span>
          </div>
        </div>

        <div className="titlebar-modes" style={{ marginLeft: 16 }}>
          {/* Insights button with chart icon */}
          <button
            type="button"
            className={`topbar-nav-btn ${view === 'decisions' || view === 'questions' ? 'active' : ''}`}
            onClick={() => setView(view === 'decisions' ? 'questions' : 'decisions')}
            title="View Questions & Decisions"
          >
            <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round">
              <path d="M18 20V10M12 20V4M6 20v-6"/>
            </svg>
            Insights
            {(questions.filter((q) => q.status === 'open').length > 0 || decisions.length > 0) && (
              <span style={{ fontSize: 10, background: '#E59838', color: '#FFF', padding: '1px 5px', borderRadius: 8, fontWeight: 700 }}>
                {questions.filter((q) => q.status === 'open').length + decisions.length}
              </span>
            )}
          </button>

          {/* Knowledge Base button (active olive-green pill) */}
          <button
            type="button"
            className={`topbar-nav-btn knowledge-base ${view === 'synthesis' ? 'active' : ''}`}
            onClick={() => setView('synthesis')}
            title="View Synthesis & Document Archive"
          >
            <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round">
              <path d="M4 19.5v-15A2.5 2.5 0 0 1 6.5 2H20v20H6.5a2.5 2.5 0 0 1-2.5-2.5Z"/>
              <path d="M6 6h10M6 10h10M6 14h6"/>
            </svg>
            Knowledge Base
          </button>

          {/* Power Search button with magnifying glass */}
          <button
            type="button"
            className={`topbar-nav-btn ${view === 'conversation' ? 'active' : ''}`}
            onClick={() => setView('conversation')}
            title="Power Search & Conversation"
          >
            <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round">
              <circle cx="11" cy="11" r="8"/>
              <line x1="21" y1="21" x2="16.65" y2="16.65"/>
            </svg>
            Power Search
          </button>
        </div>

        {view === 'conversation' && conversations.length > 0 && (
          <select
            className="workspace-select-pill"
            value={conversation?.id ?? ''}
            onChange={(e) => workspace && openConversation(workspace.id, Number(e.target.value))}
            style={{ width: 180, marginLeft: 8 }}
          >
            {conversations.map((c) => (
              <option key={c.id} value={c.id}>
                {c.title}
              </option>
            ))}
          </select>
        )}
        {view === 'conversation' && workspace && (
          <button className="btn text-sm" onClick={() => newConversation(workspace.id)}>
            + New
          </button>
        )}

        <span className="spacer" />

        <select
          className="workspace-select-pill"
          value={workspace?.id ?? ''}
          onChange={(e) => {
            const ws = workspaces.find((w) => w.id === Number(e.target.value))
            if (ws) selectWorkspace(ws)
          }}
          title="Active Workspace"
        >
          {workspaces.map((w) => (
            <option key={w.id} value={w.id}>
              📁 {w.name}
            </option>
          ))}
        </select>

        {authRequired && (
          <button className="btn text-sm" onClick={signOut}>
            Sign out
          </button>
        )}
        <button
          className="btn text-sm"
          onClick={() => setContextOpen((v) => !v)}
          title="Toggle Related Chats & Context Panel"
        >
          {contextOpen ? 'Hide Context' : 'Related Chats'}
        </button>
      </div>

      <div className={`layout ${contextOpen ? '' : 'context-collapsed'}`}>
        <div className="panel left-sidebar">
          {workspace && (
            <WorkspacePanel
              workspace={workspace}
              repository={repository}
              tree={tree}
              selectedPath={documentPath}
              onSelectDocument={openDocument}
              currentView={view}
              onSelectView={setView}
              openQuestionsCount={questions.filter((q) => q.status === 'open').length}
              decisionsCount={decisions.length}
              onRepositoryAdded={async () => {
                if (!workspace) return
                const updated = await api.getWorkspace(workspace.id)
                await selectWorkspace(updated)
              }}
            />
          )}
        </div>

        <div className="panel conversation">
          {view === 'conversation' && (
            <ConversationPanel
              workspaceId={workspace?.id}
              conversation={conversation}
              chatStatus={chatStatus}
              sending={sending}
              error={error}
              onSend={send}
              onModeChange={changeMode}
              onOpenFile={openDocument}
              onOpenQuestion={() => setView('questions')}
              onOpenDecision={() => setView('decisions')}
              onQuestionSaved={refreshQuestions}
              onDecisionSaved={refreshDecisions}
            />
          )}
          {view === 'synthesis' && (
            <SynthesisView
              repository={repository}
              path={documentPath}
              markdown={documentMarkdown}
              onOpenConversation={() => setView('conversation')}
              onToggleRaw={() => setShowRaw((v) => !v)}
              showRaw={showRaw}
            />
          )}
          {view === 'questions' && workspace && (
            <QuestionsPanel
              workspace={workspace}
              questions={questions}
              decisions={decisions}
              onRefreshQuestions={refreshQuestions}
              onRefreshDecisions={refreshDecisions}
              onOpenDecision={() => setView('decisions')}
            />
          )}
          {view === 'decisions' && workspace && (
            <DecisionsPanel
              workspace={workspace}
              repository={repository}
              decisions={decisions}
              questions={questions}
              onRefreshDecisions={refreshDecisions}
              onRefreshQuestions={refreshQuestions}
              onRefreshTree={() => reloadTree(workspace, repository)}
              onSelectDocument={openDocument}
              onOpenQuestion={() => setView('questions')}
            />
          )}
        </div>

        {contextOpen && (
          <div className="panel right-context">
            <ContextPanel
              repository={repository}
              documentPath={documentPath}
              documentMarkdown={documentMarkdown}
              inventoryRun={inventoryRun}
              proposals={proposals}
              busy={busy}
              onRunInventory={runInventory}
              onApplyInventoryAll={applyAll}
              onApplyInventoryItem={applyItem}
              onSkipInventoryItem={skipItem}
              onAcceptProposal={acceptProposal}
              onRejectProposal={rejectProposal}
              onToggleRaw={() => setShowRaw((v) => !v)}
              showRaw={showRaw}
              onClose={() => setContextOpen(false)}
            />
          </div>
        )}
      </div>
    </div>
  )
}

