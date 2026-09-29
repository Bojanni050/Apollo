import { useState } from 'react'
import type { Workspace } from '../api/client'

export type NavSection =
  | 'inbox'
  | 'all'
  | 'groups'
  | 'docs'
  | 'repos'
  | 'decisions'
  | 'questions'
  | 'proposals'
  | 'conversations'
  | 'inventory'
  | 'pulse'

export interface ObjectCounts {
  /** Documents waiting in the inbox. */
  inbox: number
  all: number
  groups: number
  docs: number
  repos: number
  decisions: number
  questions: number
  proposals: number
  conversations: number
  inventory: number
  pulseWoven: number
}

interface Props {
  workspace: Workspace | null
  workspaces: Workspace[]
  activeSection: NavSection
  onSelectSection: (section: NavSection) => void
  counts: ObjectCounts
  onNewObject: () => void
  onFocusSearch: () => void
  onSelectWorkspace: (ws: Workspace) => void
  onAddRepository: () => void
  onOpenSettings: () => void
  onDeleteWorkspace: (ws: Workspace) => void
  /**
   * True while a Delphi Pulse scan is running. The pinned footer button is
   * the only place Pulse exists in this column, so it is also the only place
   * that can show the app is working: the glyph breathes while it runs.
   */
  pulseActive?: boolean
}

export function NavigationColumn({
  workspace,
  workspaces,
  activeSection,
  onSelectSection,
  counts,
  onNewObject,
  onFocusSearch,
  onSelectWorkspace,
  onAddRepository,
  onOpenSettings,
  onDeleteWorkspace,
  pulseActive = false,
}: Props) {
  const [workspaceMenuOpen, setWorkspaceMenuOpen] = useState(false)

  return (
    <aside className="nav-column">
      {/* 1. App / Workspace Header */}
      <div className="nav-header">
        <div className="nav-brand-title">
          <span className="nav-brand-hash">#</span>
          <span className="nav-brand-name">
            {workspace ? workspace.name.toLowerCase().replace(/\s+/g, '-') : 'mindstack'}
          </span>
        </div>
      </div>

      {/* 2. Quick Action Buttons */}
      <div className="nav-actions-group">
        <button
          type="button"
          className="nav-action-btn"
          onClick={onNewObject}
          title="Create a new object (⌘N)"
        >
          <span className="nav-action-left">
            <span className="nav-action-icon">+</span>
            <span>New object</span>
          </span>
          <span className="nav-action-shortcut">⌘N</span>
        </button>

        <button
          type="button"
          className="nav-action-btn"
          onClick={onFocusSearch}
          title="Search documents and objects (⌘K)"
        >
          <span className="nav-action-left">
            <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round">
              <circle cx="11" cy="11" r="8"/>
              <line x1="21" y1="21" x2="16.65" y2="16.65"/>
            </svg>
            <span>Search</span>
          </span>
          <span className="nav-action-shortcut">⌘K</span>
        </button>
      </div>

      {/* 3. Primary & Object Types Navigation */}
      <div className="nav-scroll-area">
        {/* The inbox, above everything else.

            Every other destination is a way of looking at what is already here;
            this one is how documents arrive. It comes first because it is the
            first thing the product asks of you -- drop things in -- and because
            a reader who cannot find it is stuck at step zero. */}
        <div className="nav-list">
          <button
            type="button"
            className={`nav-item ${activeSection === 'inbox' ? 'active' : ''}`}
            onClick={() => onSelectSection('inbox')}
            title="Documents waiting to be understood"
          >
            <span className="nav-item-left">
              <span className="nav-item-glyph">＋</span>
              <span>Inbox</span>
            </span>
            <span className="nav-item-count">{counts.inbox}</span>
          </button>
        </div>

        {/* All Objects */}
        <div className="nav-list">
          <button
            type="button"
            className={`nav-item ${activeSection === 'all' ? 'active' : ''}`}
            onClick={() => onSelectSection('all')}
          >
            <span className="nav-item-left">
              <svg className="nav-item-icon" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                <rect x="3" y="3" width="7" height="7"/>
                <rect x="14" y="3" width="7" height="7"/>
                <rect x="14" y="14" width="7" height="7"/>
                <rect x="3" y="14" width="7" height="7"/>
              </svg>
              <span>All objects</span>
            </span>
            <span className="nav-item-count">{counts.all}</span>
          </button>
        </div>

        {/* Groups, above the object types.

            This is the first thing the product is for, so it is the first
            destination in the list: "which documents belong together" is the
            question a reader brings to a pile of documents, and everything below
            here is a way of looking at one of them. */}
        <div className="nav-section-group">
          <div className="nav-section-title">ARRANGEMENT</div>
          <div className="nav-list">
            <button
              type="button"
              className={`nav-item ${activeSection === 'groups' ? 'active' : ''}`}
              onClick={() => onSelectSection('groups')}
              title="Which documents belong together"
            >
              <span className="nav-item-left">
                <span className="nav-item-glyph">▦</span>
                <span>Groups</span>
              </span>
              <span className="nav-item-count">{counts.groups}</span>
            </button>
          </div>
        </div>

        {/* Section: Object Types */}
        <div className="nav-section-group">
          <div className="nav-section-title">OBJECT TYPES</div>
          <div className="nav-list">
            <button
              type="button"
              className={`nav-item ${activeSection === 'docs' ? 'active' : ''}`}
              onClick={() => onSelectSection('docs')}
            >
              <span className="nav-item-left">
                <span className="nav-item-glyph">📝</span>
                <span>Notes &amp; Docs</span>
              </span>
              <span className="nav-item-count">{counts.docs}</span>
            </button>

            <button
              type="button"
              className={`nav-item ${activeSection === 'proposals' ? 'active' : ''}`}
              onClick={() => onSelectSection('proposals')}
            >
              <span className="nav-item-left">
                <span className="nav-item-glyph">💡</span>
                <span>Ideas &amp; Proposals</span>
              </span>
              <span className="nav-item-count">{counts.proposals}</span>
            </button>

            <button
              type="button"
              className={`nav-item ${activeSection === 'conversations' ? 'active' : ''}`}
              onClick={() => onSelectSection('conversations')}
            >
              <span className="nav-item-left">
                <span className="nav-item-glyph">💬</span>
                <span>Conversations</span>
              </span>
              <span className="nav-item-count">{counts.conversations}</span>
            </button>
          </div>
        </div>

        {/* Everything that exists, but is not the way in.

            The list above is the workflow -- documents arrive, Delphi says what
            stands out, groups hold them together. Repositories, inventory runs,
            ADRs and open questions are how this was built and how it is looked
            after; they stay one click away rather than sitting at the same
            weight as the three things a reader opens the app for. A <details>
            element rather than a button and a flag: it is a disclosure, it works
            from the keyboard, and it remembers nothing. Nothing here is hidden
            or removed -- a section that is not on the front page is still a
            section you can open.

            Delphi Pulse is deliberately not repeated here. It has its own pinned
            button in the footer, always visible, and a second entry in a list
            that opens on demand is the same destination twice for no gain. */}
        <div className="nav-section-group">
          <details className="nav-more">
            <summary className="nav-section-title nav-more-summary">More</summary>
            <div className="nav-list">
              <button
                type="button"
                className={`nav-item ${activeSection === 'repos' ? 'active' : ''}`}
                onClick={() => onSelectSection('repos')}
              >
                <span className="nav-item-left">
                  <span className="nav-item-glyph">👤</span>
                  <span>Repositories</span>
                </span>
                <span className="nav-item-count">{counts.repos}</span>
              </button>

              <button
                type="button"
                className={`nav-item ${activeSection === 'decisions' ? 'active' : ''}`}
                onClick={() => onSelectSection('decisions')}
              >
                <span className="nav-item-left">
                  <span className="nav-item-glyph">⚖️</span>
                  <span>Decisions (ADRs)</span>
                </span>
                <span className="nav-item-count">{counts.decisions}</span>
              </button>

              <button
                type="button"
                className={`nav-item ${activeSection === 'questions' ? 'active' : ''}`}
                onClick={() => onSelectSection('questions')}
              >
                <span className="nav-item-left">
                  <span className="nav-item-glyph">❓</span>
                  <span>Open Questions</span>
                </span>
                <span className="nav-item-count">{counts.questions}</span>
              </button>

              <button
                type="button"
                className={`nav-item ${activeSection === 'inventory' ? 'active' : ''}`}
                onClick={() => onSelectSection('inventory')}
              >
                <span className="nav-item-left">
                  <span className="nav-item-glyph">📦</span>
                  <span>Inventory Runs</span>
                </span>
                <span className="nav-item-count">{counts.inventory}</span>
              </button>
            </div>
          </details>
        </div>
      </div>

      {/* 4. Bottom Pinned Section */}
      <div className="nav-footer">
        <button
          type="button"
          className={`nav-ai-pulse-btn ${pulseActive ? 'pulse-active' : ''}`}
          onClick={() => onSelectSection('pulse')}
        >
          <span className="ai-pulse-left">
            <span className="ai-pulse-glyph">✦</span>
            <span>Delphi Pulse</span>
          </span>
          <span className="ai-pulse-badge">*new</span>
        </button>

        <div className="nav-user-row">
          <div
            className="nav-user-info"
            onClick={() => setWorkspaceMenuOpen((v) => !v)}
            role="button"
            tabIndex={0}
          >
            <div className="nav-user-avatar">
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                <path d="M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2"/>
                <circle cx="12" cy="7" r="4"/>
              </svg>
            </div>
            <div className="nav-user-meta">
              <span className="nav-workspace-name">
                {workspace?.name || 'Personal workspace'}
              </span>
              <span className="nav-workspace-sub">
                {counts.all} objects · solo mode
              </span>
            </div>
            <span className="nav-chevron">▾</span>
          </div>
          <button
            type="button"
            className="nav-settings-btn"
            onClick={onOpenSettings}
            title="Settings"
          >
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
              <circle cx="12" cy="12" r="3" />
              <path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 0 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-4 0v-.09a1.65 1.65 0 0 0-1-1.51 1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 0 1-2.83-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1 0-4h.09a1.65 1.65 0 0 0 1.51-1 1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 0 1 2.83-2.83l.06.06a1.65 1.65 0 0 0 1.82.33h0a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 4 0v.09a1.65 1.65 0 0 0 1 1.51h0a1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 0 1 2.83 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82v0a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z" />
            </svg>
          </button>

          {workspaceMenuOpen && (
            <div className="workspace-popup-menu">
              <div className="popup-section-title">Switch Workspace</div>
              {workspaces.map((ws) => (
                <div key={ws.id} className="popup-menu-row">
                  <button
                    type="button"
                    className={`popup-menu-item ${ws.id === workspace?.id ? 'active' : ''}`}
                    onClick={() => {
                      onSelectWorkspace(ws)
                      setWorkspaceMenuOpen(false)
                    }}
                  >
                    <span>📁 {ws.name}</span>
                    {ws.id === workspace?.id && <span>✓</span>}
                  </button>
                  <button
                    type="button"
                    className="popup-menu-icon danger"
                    title={`Delete workspace "${ws.name}" and everything recorded in it`}
                    onClick={() => {
                      setWorkspaceMenuOpen(false)
                      onDeleteWorkspace(ws)
                    }}
                  >
                    <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                      <polyline points="3 6 5 6 21 6"/>
                      <path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/>
                    </svg>
                  </button>
                </div>
              ))}
              <div className="popup-divider" />
              <button
                type="button"
                className="popup-menu-item"
                onClick={() => {
                  setWorkspaceMenuOpen(false)
                  onAddRepository()
                }}
              >
                <span>+ Add Repository</span>
              </button>
            </div>
          )}
        </div>
      </div>
    </aside>
  )
}
