import { useMemo } from 'react'
import type { Conversation, Decision, DocNode, GroupProposal, InboxFile, OpenQuestion, PulseItem, Repository, Workspace } from '../api/client'
import type { NavSection } from './NavigationColumn'
import { InboxDropzone } from './InboxDropzone'
import { WorkingFolderCard } from './WorkingFolderCard'

export interface ItemCard {
  id: string
  title: string
  snippet: string
  type: string
  dateOrSize?: string
  tags: string[]
  rawNode?: DocNode
  rawDecision?: Decision
  rawQuestion?: OpenQuestion
  rawConversation?: Conversation
  rawRepo?: Repository
  rawPulseItem?: PulseItem
  /**
   * A document in the inbox, with the repository it lives in.
   *
   * The repository travels with it because the inbox is Apollo's own folder and
   * is not necessarily the repository the reader has open: opening the wrong
   * repository's file of the same name would be a silent, plausible-looking
   * wrong answer.
   */
  rawInboxFile?: { path: string; name: string; size: number; repositoryId: number }
}

interface Props {
  activeSection: NavSection
  searchQuery: string
  onSearchChange: (q: string) => void
  workspace: Workspace | null
  repository: Repository | null
  tree: DocNode | null
  selectedId: string | null
  onSelectItem: (item: ItemCard) => void
  onNewItem: () => void
  decisions: Decision[]
  questions: OpenQuestion[]
  conversations: Conversation[]
  // Every repository the workspace holds, of any kind. The 'repos' section is
  // driven by this list, so it must match counts.repos in the navigation.
  repositories: Repository[]
  pulseItems: PulseItem[]
  pulseRunning?: boolean
  onRunPulse?: () => void
  onOpenPulseSettings?: () => void
  // The inbox: documents dropped in, and the repository they were stored in.
  // Null until the first upload, which is how the column knows there is nothing
  // to open yet rather than that the folder is empty.
  inboxFiles: InboxFile[]
  inboxRepositoryId: number | null
  /** The workspace the drop zone stores into. */
  workspaceId: number | null
  /** Called after a drop, so the inbox list and the tree are re-read. */
  onInboxStored: () => void
  // Delphi's pass, and what it said about itself. One group of props because
  // they are one feature: the button, its state, and the report it produces.
  onRunDelphi?: () => void
  analysing?: boolean
  /** False when no model is configured, so the button can say so instead of
   *  failing when it is pressed. */
  llmConfigured?: boolean
  /** Findings still waiting for a decision, shown on the button. */
  openFindings?: number
  delphiSummary?: string | null
  delphiErrors?: string[]
  /** The groups this pass proposed, named in the report so the reader knows a
   *  group is Delphi's proposal and not something that was always there. */
  delphiGroups?: GroupProposal[]
  onDismissDelphiReport?: () => void
}

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

export function FolderContentsColumn({
  activeSection,
  searchQuery,
  onSearchChange,
  workspace: _workspace,
  repository: _repository,
  tree,
  selectedId,
  onSelectItem,
  onNewItem,
  decisions,
  questions,
  conversations,
  repositories,
  pulseItems,
  pulseRunning = false,
  onRunPulse,
  onOpenPulseSettings,
  inboxFiles,
  inboxRepositoryId,
  workspaceId,
  onInboxStored,
  onRunDelphi,
  analysing = false,
  llmConfigured = true,
  openFindings = 0,
  delphiSummary = null,
  delphiErrors = [],
  delphiGroups = [],
  onDismissDelphiReport = () => {},
}: Props) {
  // The analysis belongs to the sections that list documents, and to the inbox:
  // it is a question about a collection, and those are the collections on offer.
  const canAnalyse =
    activeSection === 'inbox' || activeSection === 'all' || activeSection === 'docs'

  /* What the pass proposed, said the way the reader would say it.

     A member the reader had already placed is reported as left alone rather than
     as a member: it was not put anywhere, and that difference is the whole of
     the reader's right to disagree. */
  const groupLines = delphiGroups.map((group) => {
    const parts = [`${group.placed.length} ${group.placed.length === 1 ? 'document' : 'documenten'}`]
    if (group.left_alone.length > 0) {
      parts.push(`${group.left_alone.length} op de plek gelaten die je had bepaald`)
    }
    if (group.unavailable.length > 0) {
      parts.push(`${group.unavailable.length} niet meer te vinden`)
    }
    return { key: String(group.group_id), text: `${group.name}: ${parts.join(', ')}` }
  })
  // Convert current items into standard ItemCard format
  const items = useMemo<ItemCard[]>(() => {
    const flatFiles = flattenDocs(tree)

    if (activeSection === 'decisions') {
      return decisions.map((d) => ({
        id: `decision-${d.id}`,
        title: d.title || `ADR-${String(d.id).padStart(3, '0')}`,
        snippet: d.decision || d.context || 'Architectural decision record.',
        type: 'ADR',
        dateOrSize: d.status.toUpperCase(),
        tags: ['adr', d.status],
        rawDecision: d,
      }))
    }

    if (activeSection === 'questions') {
      return questions.map((q) => ({
        id: `question-${q.id}`,
        title: q.title,
        snippet: q.description || 'Open architectural inquiry regarding system design.',
        type: 'QUESTION',
        dateOrSize: q.status.toUpperCase(),
        tags: ['question', q.status],
        rawQuestion: q,
      }))
    }

    if (activeSection === 'conversations') {
      return conversations.map((c) => ({
        id: `conversation-${c.id}`,
        title: c.title || `Conversation #${c.id}`,
        snippet: `${c.message_count} messages in ${c.mode} mode.`,
        type: 'CHAT',
        dateOrSize: new Date(c.updated_at || c.created_at).toLocaleDateString(),
        tags: [c.mode, 'chat'],
        rawConversation: c,
      }))
    }

    if (activeSection === 'repos') {
      return repositories.map((r) => ({
        id: `repo-${r.id}`,
        title: r.name,
        snippet: r.local_path + (r.current_branch ? ` (${r.current_branch})` : ''),
        type: r.kind.toUpperCase(),
        dateOrSize: r.writable ? 'WRITABLE' : 'READ-ONLY',
        tags: [r.kind, r.is_git_repo ? 'git' : 'local'],
        rawRepo: r,
      }))
    }

    if (activeSection === 'pulse') {
      return pulseItems.map((p) => ({
        id: `pulse-${p.id}`,
        title: p.file_path,
        snippet: p.summary || 'No summary for this document.',
        type: 'PULSE',
        dateOrSize:
          p.decision === 'pending'
            ? 'SUGGESTED'
            : p.decision === 'applied'
              ? 'APPLIED'
              : 'SKIPPED',
        tags: p.tags,
        rawPulseItem: p,
      }))
    }

    if (activeSection === 'inbox') {
      // A file can only be listed once the storage repository exists, so the id
      // is always there when there is an entry to show. The guard is for the
      // type checker; if it ever fired, the listing and the repository would
      // have got out of step.
      if (inboxRepositoryId === null) return []
      return inboxFiles.map((file) => {
        const ext = file.name.split('.').pop()?.toLowerCase() || 'md'
        return {
          id: `inbox-${file.path}`,
          title: file.name.replace(/\.[^/.]+$/, ''),
          snippet: file.path,
          type: ext.toUpperCase(),
          dateOrSize:
            file.size < 1024 ? `${file.size} B` : `${Math.round(file.size / 1024)} KB`,
          tags: ['inbox', ext],
          rawInboxFile: {
            path: file.path,
            name: file.name,
            size: file.size,
            repositoryId: inboxRepositoryId,
          },
        }
      })
    }

    // Default: 'all' or 'docs' -> show document files from tree
    if (flatFiles.length === 0) {
      return []
    }

    return flatFiles.map((node) => {
      const ext = node.name.split('.').pop()?.toLowerCase() || 'md'
      const rawTitle = node.name.replace(/\.[^/.]+$/, '').replace(/[-_]/g, ' ')
      const cleanTitle = rawTitle.charAt(0).toUpperCase() + rawTitle.slice(1)
      const folder = node.path.includes('/') ? node.path.split('/')[0] : 'root'
      const isAdr = node.name.toLowerCase().includes('adr') || node.path.toLowerCase().includes('decision')

      return {
        id: node.path,
        title: cleanTitle,
        snippet: node.path,
        type: isAdr ? 'ADR' : ext.toUpperCase(),
        dateOrSize: node.size ? (node.size < 1024 ? `${node.size} B` : `${Math.round(node.size / 1024)} KB`) : 'DOC',
        tags: [folder, ext],
        rawNode: node,
      }
    })
  }, [tree, activeSection, decisions, questions, conversations, repositories, pulseItems, inboxFiles, inboxRepositoryId])

  // Filter based on search query
  const filteredItems = useMemo(() => {
    if (!searchQuery.trim()) return items
    const q = searchQuery.toLowerCase().trim()
    return items.filter(
      (it) =>
        it.title.toLowerCase().includes(q) ||
        it.snippet.toLowerCase().includes(q) ||
        it.tags.some((t) => t.toLowerCase().includes(q)),
    )
  }, [items, searchQuery])

  // Header Title derivation
  const sectionTitle = useMemo(() => {
    switch (activeSection) {
      case 'inbox':
        return 'Inbox'
      case 'all':
        return 'All objects'
      case 'docs':
        return 'Notes & Docs'
      case 'decisions':
        return 'Decisions (ADRs)'
      case 'questions':
        return 'Open Questions'
      case 'proposals':
        return 'Proposals'
      case 'conversations':
        return 'Conversations'
      case 'repos':
        return 'Repositories'
      case 'pulse':
        return 'Delphi Pulse'
      default:
        return 'Objects'
    }
  }, [activeSection])

  return (
    <div className="folder-contents-column">
      {/* 1. Header Bar matching screenshot */}
      <div className="folder-contents-header">
        <div className="folder-contents-title-block">
          <h2 className="folder-contents-title">{sectionTitle}</h2>
          <span className="folder-contents-subtitle">
            {filteredItems.length} {filteredItems.length === 1 ? 'object' : 'objects'}
          </span>
        </div>

        {activeSection === 'pulse' ? (
          <div style={{ display: 'flex', gap: 6, alignItems: 'center' }}>
            {onOpenPulseSettings && (
              <button
                type="button"
                className="btn"
                style={{ fontSize: 11, padding: '2px 8px' }}
                onClick={onOpenPulseSettings}
                title="Delphi Pulse settings: mode and automatic scanning"
              >
                ⚙
              </button>
            )}
            <button
              type="button"
              className="folder-contents-new-btn"
              onClick={onRunPulse}
              disabled={pulseRunning}
              title="Scan the documentation for tags and connections"
            >
              {pulseRunning ? 'Scanning…' : 'Run Pulse'}
            </button>
          </div>
        ) : (
          <div className="folder-contents-actions">
            {/* The one button, on the sections where a collection is on offer.

                No "New" button for the inbox: that would offer to create a
                decision or a conversation where what is wanted is dropping a
                file, which the drop zone below already does. */}
            {canAnalyse && onRunDelphi && (
              <button
                type="button"
                className="folder-contents-new-btn"
                onClick={onRunDelphi}
                disabled={analysing || !llmConfigured}
                title={
                  !llmConfigured
                    ? 'Delphi needs a model. Add one in Settings, then analyse.'
                    : 'Read the collection and say what stands out. Nothing is moved or changed.'
                }
              >
                {analysing
                  ? 'Analyseren…'
                  : openFindings > 0
                    ? `Analyseren · ${openFindings}`
                    : 'Analyseren'}
              </button>
            )}
            {activeSection !== 'inbox' && (
              <button
                type="button"
                className="folder-contents-new-btn"
                onClick={onNewItem}
                title="Create New Object"
              >
                New
              </button>
            )}
          </div>
        )}
      </div>

      {/* 2. Search Input */}
      <div className="folder-contents-search-bar">
        <div className="folder-search-input-wrapper">
          <svg className="folder-search-icon" width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round">
            <circle cx="11" cy="11" r="8"/>
            <line x1="21" y1="21" x2="16.65" y2="16.65"/>
          </svg>
          <input
            type="text"
            className="folder-search-input"
            placeholder="Search..."
            value={searchQuery}
            onChange={(e) => onSearchChange(e.target.value)}
          />
          {searchQuery && (
            <button
              type="button"
              className="folder-search-clear"
              onClick={() => onSearchChange('')}
            >
              ✕
            </button>
          )}
        </div>
      </div>

      {/* The pass's own account of itself, above the list: what it read, what it
          found, what it did not do, and anything that went wrong. Kept until the
          reader clears it, because "read 4 of 10 documents" is only useful if it
          stays on screen long enough to be read. */}
      {(delphiSummary || delphiErrors.length > 0 || groupLines.length > 0) && (
        <div className="delphi-report">
          {delphiSummary && <p className="delphi-report-summary">{delphiSummary}</p>}
          {groupLines.length > 0 && (
            <>
              <p className="delphi-report-groups">
                {groupLines.length === 1
                  ? 'Delphi stelde één groep voor:'
                  : `Delphi stelde ${groupLines.length} groepen voor:`}
              </p>
              {groupLines.map((line) => (
                <p key={line.key} className="delphi-report-group">
                  {line.text}
                </p>
              ))}
            </>
          )}
          {delphiErrors.map((error, index) => (
            <p key={index} className="delphi-report-error">
              {error}
            </p>
          ))}
          <button
            type="button"
            className="delphi-report-close"
            onClick={onDismissDelphiReport}
            title="Clear this report"
          >
            ✕
          </button>
        </div>
      )}

      {/* A non-empty inbox still has to offer the same drop zone, or adding a
          twelfth document would mean emptying the list to get at it. Slim
          enough not to compete with the documents themselves. */}
      {activeSection === 'inbox' && inboxFiles.length > 0 && workspaceId !== null && (
        <div className="folder-contents-inbox-tools">
          <InboxDropzone workspaceId={workspaceId} onStored={onInboxStored} compact />
        </div>
      )}

      {/* Only when the reader has not chosen one, and never in the way: a card
          for something already decided is a card pushing the documents down. */}
      {activeSection === 'inbox' && workspaceId !== null && (
        <WorkingFolderCard workspaceId={workspaceId} onChanged={onInboxStored} />
      )}

      {/* 3. Items List */}
      <div className="folder-contents-list">
        {activeSection === 'inbox' && inboxFiles.length === 0 && workspaceId !== null ? (
          /* An empty inbox has nothing to list and nothing to search, so the
             empty state *is* the way in: not an icon with a "create new" button,
             which would be an instruction to do something this section cannot
             do. */
          <InboxDropzone workspaceId={workspaceId} onStored={onInboxStored} />
        ) : filteredItems.length === 0 ? (
          <div className="folder-contents-empty">
            <div className="folder-empty-icon">📂</div>
            <div className="folder-empty-title">No objects found</div>
            <div className="folder-empty-desc">
              {activeSection === 'pulse'
                ? searchQuery
                  ? `No results for "${searchQuery}"`
                  : 'Run a Pulse scan to find themes and connections across your documentation.'
                : searchQuery
                  ? `No results for "${searchQuery}"`
                  : 'This category contains no objects yet.'}
            </div>
            <button
              type="button"
              className="folder-empty-create-btn"
              onClick={onNewItem}
            >
              + Create New
            </button>
          </div>
        ) : (
          filteredItems.map((item) => {
            const isSelected = selectedId === item.id || (item.rawNode && selectedId === item.rawNode.path)

            return (
              <div
                key={item.id}
                className={`object-card ${isSelected ? 'selected' : ''}`}
                onClick={() => onSelectItem(item)}
              >
                {/* Title */}
                <h3 className="object-card-title">{item.title}</h3>

                {/* Snippet / Abstract */}
                <p className="object-card-snippet">{item.snippet}</p>

                {/* Metadata & Tag Pills */}
                <div className="object-card-footer">
                  <span className="object-card-meta">
                    {item.type} {item.dateOrSize ? `· ${item.dateOrSize}` : ''}
                  </span>

                  {item.tags.length > 0 && (
                    <div className="object-card-tags">
                      {item.tags.slice(0, 3).map((tag) => (
                        <span key={tag} className="object-card-pill">
                          {tag}
                        </span>
                      ))}
                    </div>
                  )}
                </div>
              </div>
            )
          })
        )}
      </div>
    </div>
  )
}
