import { useCallback, useEffect, useState } from 'react'
import {
  ApiError,
  api,
  type ChatStatus,
  type Conversation,
  type ConversationDetail,
  type DocNode,
  type InventoryRun,
  type Mode,
  type Proposal,
  type Repository,
  type Workspace,
} from './api/client'
import { ConversationPanel } from './components/ConversationPanel'
import { ContextPanel } from './components/ContextPanel'
import { WorkspacePanel } from './components/WorkspacePanel'

export default function App() {
  const [workspaces, setWorkspaces] = useState<Workspace[]>([])
  const [workspace, setWorkspace] = useState<Workspace | null>(null)
  const [repository, setRepository] = useState<Repository | null>(null)
  const [tree, setTree] = useState<DocNode | null>(null)
  const [documentPath, setDocumentPath] = useState<string | null>(null)
  const [documentMarkdown, setDocumentMarkdown] = useState<string | null>(null)
  const [showRaw, setShowRaw] = useState(false)

  const [conversations, setConversations] = useState<Conversation[]>([])
  const [conversation, setConversation] = useState<ConversationDetail | null>(null)
  const [chatStatus, setChatStatus] = useState<ChatStatus | null>(null)

  const [inventoryRun, setInventoryRun] = useState<InventoryRun | null>(null)
  const [proposals, setProposals] = useState<Proposal[]>([])

  const [contextOpen, setContextOpen] = useState(true)
  const [sending, setSending] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

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
        const [status, convs, props, runs] = await Promise.all([
          api.chatStatus(ws.id),
          api.listConversations(ws.id),
          api.listProposals(ws.id),
          api.listInventoryRuns(ws.id),
        ])
        setChatStatus(status)
        setConversations(convs)
        setProposals(props)
        if (runs.length > 0) setInventoryRun(runs[0])
        if (convs.length > 0) await openConversation(ws.id, convs[0].id)
        else await newConversation(ws.id)
      } catch (e) {
        report(e)
      }
    },
    [newConversation, openConversation, reloadTree],
  )

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



  // -- actions -----------------------------------------------------------

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

  if (workspaces.length === 0) {
    return (
      <div className="app">
        <div className="titlebar">
          <h1>Gaia Docs Architect</h1>
        </div>
        <div className="empty" style={{ paddingTop: 80 }}>
          <p>No workspace yet.</p>
          <p className="faint">Create one from the API, then reload this page:</p>
          <pre className="code" style={{ maxWidth: 460, margin: '12px auto' }}>
            {`curl -X POST localhost:8000/api/workspaces \\
  -H "content-type: application/json" \\
  -d '{"name":"Gaia"}'`}
          </pre>
        </div>
      </div>
    )
  }

  return (
    <div className="app">
      <div className="titlebar">
        <h1>Gaia Docs Architect</h1>
        <select
          value={workspace?.id ?? ''}
          onChange={(e) => {
            const ws = workspaces.find((w) => w.id === Number(e.target.value))
            if (ws) selectWorkspace(ws)
          }}
          style={{ width: 180 }}
        >
          {workspaces.map((w) => (
            <option key={w.id} value={w.id}>
              {w.name}
            </option>
          ))}
        </select>

        {conversations.length > 0 && (
          <select
            value={conversation?.id ?? ''}
            onChange={(e) => workspace && openConversation(workspace.id, Number(e.target.value))}
            style={{ width: 260 }}
          >
            {conversations.map((c) => (
              <option key={c.id} value={c.id}>
                {c.title}
              </option>
            ))}
          </select>
        )}
        {workspace && (
          <button className="btn" onClick={() => newConversation(workspace.id)}>
            New
          </button>
        )}

        <span className="spacer" />
        <button className="btn" onClick={() => setContextOpen((v) => !v)}>
          {contextOpen ? 'Hide context' : 'Show context'}
        </button>
      </div>

      <div className={`layout ${contextOpen ? '' : 'context-collapsed'}`}>
        <div className="panel">
          <div className="panel-header">Workspace</div>
          <div className="panel-body tight">
            {workspace ? (
              <WorkspacePanel
                workspace={workspace}
                repository={repository}
                tree={tree}
                selectedPath={documentPath}
                onSelectDocument={openDocument}
              />
            ) : null}
          </div>
        </div>

        <div className="panel conversation">
          <ConversationPanel
            conversation={conversation}
            chatStatus={chatStatus}
            sending={sending}
            error={error}
            onSend={send}
            onModeChange={changeMode}
            onOpenFile={openDocument}
          />
        </div>

        {contextOpen && (
          <div className="panel">
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
            />
          </div>
        )}
      </div>
    </div>
  )
}

