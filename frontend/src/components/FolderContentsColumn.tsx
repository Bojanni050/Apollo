import { useMemo } from 'react'
import type { Conversation, Decision, DocNode, OpenQuestion, PulseItem, Repository, Workspace } from '../api/client'
import type { NavSection } from './NavigationColumn'

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
  sources: Repository[]
  pulseItems: PulseItem[]
  pulseRunning?: boolean
  onRunPulse?: () => void
  pulseMode?: 'suggest' | 'apply'
  onPulseModeChange?: (mode: 'suggest' | 'apply') => void
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
  sources,
  pulseItems,
  pulseRunning = false,
  onRunPulse,
  pulseMode,
  onPulseModeChange,
}: Props) {
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
      return sources.map((r) => ({
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
  }, [tree, activeSection, decisions, questions, conversations, sources, pulseItems])

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
        return 'Pulse-woven'
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
            {onPulseModeChange && (
              <label
                style={{
                  display: 'flex',
                  gap: 4,
                  alignItems: 'center',
                  fontSize: 11,
                  opacity: 0.8,
                  cursor: 'pointer',
                }}
              >
                <input
                  type="checkbox"
                  checked={pulseMode === 'apply'}
                  onChange={(e) => onPulseModeChange(e.target.checked ? 'apply' : 'suggest')}
                />
                auto-apply
              </label>
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

      {/* 3. Items List */}
      <div className="folder-contents-list">
        {filteredItems.length === 0 ? (
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
