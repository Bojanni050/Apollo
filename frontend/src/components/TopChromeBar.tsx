import type { Workspace } from '../api/client'

interface Props {
  workspaces: Workspace[]
  activeWorkspace: Workspace | null
  onSelectWorkspace: (ws: Workspace) => void
  onToggleContext?: () => void
  contextOpen?: boolean
}

export function TopChromeBar({
  workspaces,
  activeWorkspace,
  onSelectWorkspace,
  onToggleContext,
  contextOpen,
}: Props) {
  return (
    <header className="top-chrome-bar">
      <div className="top-chrome-left">
        <div className="chrome-app-pill">
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round">
            <path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z" />
          </svg>
          <span>App builder</span>
        </div>

        <div className="chrome-tabs-strip">
          <button type="button" className="chrome-tab">
            <span className="chrome-tab-icon">#</span>
            <span>Home</span>
          </button>
          <button type="button" className="chrome-tab">
            <span className="chrome-tab-dot dim" />
            <span>cookie-comply-1</span>
          </button>
          <button type="button" className="chrome-tab">
            <span className="chrome-tab-dot dim" />
            <span>continue-app-182</span>
          </button>
          <button type="button" className="chrome-tab active">
            <span className="chrome-tab-dot active" />
            <span>{activeWorkspace?.name || 'capacity-match'}</span>
          </button>
          <button type="button" className="chrome-tab-add" title="New Tab">
            +
          </button>
        </div>
      </div>

      <div className="top-chrome-center">
        <div className="chrome-view-toggle">
          <button type="button" className="chrome-view-pill active">
            Preview
          </button>
          <button type="button" className="chrome-view-pill">
            Manage
          </button>
        </div>
      </div>

      <div className="top-chrome-right">
        <div className="chrome-credits-pill">
          <span className="credits-badge">Credits</span>
          <span className="credits-accent">40% more</span>
        </div>

        <button type="button" className="chrome-text-link">
          Need Help?
        </button>

        <div className="chrome-actions-group">
          <button type="button" className="chrome-icon-btn" title="Refresh">
            <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
              <path d="M21.5 2v6h-6M21.34 15.57a10 10 0 1 1-.57-8.38l5.67-5.67"/>
            </svg>
          </button>
          <button type="button" className="chrome-icon-btn" title="Copy Link">
            <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
              <path d="M10 13a5 5 0 0 0 7.54.54l3-3a5 5 0 0 0-7.07-7.07l-1.72 1.71"/>
              <path d="M14 11a5 5 0 0 0-7.54-.54l-3 3a5 5 0 0 0 7.07 7.07l1.71-1.71"/>
            </svg>
          </button>
          <button type="button" className="chrome-share-btn">
            <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
              <path d="M4 12v8a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-8"/>
              <polyline points="16 6 12 2 8 6"/>
              <line x1="12" y1="2" x2="12" y2="15"/>
            </svg>
            <span>Share</span>
          </button>
          <button type="button" className="chrome-publish-btn">
            Publish
          </button>

          {onToggleContext && (
            <button
              type="button"
              className={`chrome-context-toggle ${contextOpen ? 'active' : ''}`}
              onClick={onToggleContext}
              title={contextOpen ? 'Hide right context sidebar' : 'Show right context sidebar'}
            >
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                <rect x="3" y="3" width="18" height="18" rx="2" ry="2"/>
                <line x1="15" y1="3" x2="15" y2="21"/>
              </svg>
            </button>
          )}

          {workspaces.length > 1 && (
            <select
              className="chrome-workspace-dropdown"
              value={activeWorkspace?.id ?? ''}
              onChange={(e) => {
                const found = workspaces.find((w) => w.id === Number(e.target.value))
                if (found) onSelectWorkspace(found)
              }}
            >
              {workspaces.map((w) => (
                <option key={w.id} value={w.id}>
                  {w.name}
                </option>
              ))}
            </select>
          )}

          <div className="chrome-user-avatar" title={activeWorkspace?.name || 'User Profile'}>
            <span>{activeWorkspace?.name?.slice(0, 1).toUpperCase() || 'U'}</span>
          </div>
        </div>
      </div>
    </header>
  )
}
