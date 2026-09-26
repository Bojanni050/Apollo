import { useState } from 'react'
import type { Repository } from '../api/client'
import { renderMarkdown } from '../markdown'
import type { ItemCard } from './FolderContentsColumn'

interface Props {
  selectedItem: ItemCard | null
  documentMarkdown: string | null
  repository: Repository | null
  onOpenAiChat: () => void
  onToggleContext?: () => void
  contextOpen?: boolean
  onDelete?: () => void
}

export function FileContentColumn({
  selectedItem,
  documentMarkdown,
  repository,
  onOpenAiChat,
  onToggleContext,
  contextOpen,
  onDelete,
}: Props) {
  const [showRaw, setShowRaw] = useState(false)
  const [tags, setTags] = useState<string[]>(['pulse-weave'])
  const [copied, setCopied] = useState(false)

  const activeContent = documentMarkdown ?? selectedItem?.snippet ?? null

  const handleShare = () => {
    navigator.clipboard?.writeText(window.location.href)
    setCopied(true)
    setTimeout(() => setCopied(false), 2000)
  }

  const handleRemoveTag = (t: string) => {
    setTags((prev) => prev.filter((item) => item !== t))
  }

  const handleAddTag = () => {
    const val = prompt('Enter new tag:')
    if (val && val.trim()) {
      setTags((prev) => [...prev, val.trim()])
    }
  }

  return (
    <main className="file-content-column">
      {/* 1. Top Action Toolbar */}
      <div className="file-toolbar">
        <div className="file-toolbar-left">
          <div className="file-collection-dropdown">
            <span className="file-collection-glyph">💡</span>
            <span className="file-collection-name">
              {selectedItem?.type || 'Ideas'}
            </span>
            <span className="file-collection-chevron">▾</span>
          </div>
        </div>

        <div className="file-toolbar-right">
          <button
            type="button"
            className="file-ai-enhance-btn"
            onClick={onOpenAiChat}
            title="Enhance document with AI analysis"
          >
            <span className="ai-enhance-icon">✨</span>
            <span>Enhance with AI</span>
          </button>

          <button
            type="button"
            className="file-icon-btn"
            onClick={() => setShowRaw((v) => !v)}
            title={showRaw ? 'Show rendered Markdown' : 'Show raw source text'}
          >
            {showRaw ? '📖' : '✎'}
          </button>

          {onDelete && (
            <button
              type="button"
              className="file-icon-btn"
              onClick={onDelete}
              title="Delete item"
            >
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                <polyline points="3 6 5 6 21 6"/>
                <path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/>
              </svg>
            </button>
          )}

          {onToggleContext && (
            <button
              type="button"
              className={`file-context-btn ${contextOpen ? 'active' : ''}`}
              onClick={onToggleContext}
              title={contextOpen ? 'Collapse context sidebar' : 'Expand context sidebar'}
            >
              <span style={{ fontSize: 13 }}>⚡</span>
              <span>{contextOpen ? 'Hide Context' : 'Context'}</span>
            </button>
          )}
        </div>
      </div>

      {/* 2. Main Centered Document Canvas */}
      <div className="file-canvas-scroll">
        <div className="file-canvas-inner">
          {/* Metadata line */}
          <div className="file-meta-line">
            <span>id · {selectedItem?.id?.slice(0, 8) || '—'}</span>
            <span>·</span>
            <span>updated —</span>
            {repository && (
              <>
                <span>·</span>
                <span>repo: {repository.name}</span>
              </>
            )}
          </div>

          {selectedItem?.rawDecision && (
            <div className="file-linked-sources">
              <span className="linked-sources-label">LINKED ADR</span>
              <div className="linked-sources-chips">
                <button
                  type="button"
                  className="linked-source-chip ok"
                  onClick={onOpenAiChat}
                >
                  <span className="chip-icon">⚖️</span>
                  <span>ADR-{selectedItem.rawDecision.id}</span>
                </button>
              </div>
            </div>
          )}

          {/* Large Document Title */}
          <h1 className="file-document-title">{selectedItem?.title || 'No document selected'}</h1>

          {/* Tag Chips Row */}
          <div className="file-tags-bar">
            {tags.map((t) => (
              <span key={t} className="file-tag-pill">
                <span>#{t}</span>
                <button
                  type="button"
                  className="tag-remove-x"
                  onClick={() => handleRemoveTag(t)}
                  title="Remove tag"
                >
                  ✕
                </button>
              </span>
            ))}
            <button
              type="button"
              className="file-add-tag-btn"
              onClick={handleAddTag}
            >
              + tag
            </button>
          </div>

          {/* Rendered Prose Content */}
          {activeContent ? (
            <div className="file-document-body">
              {showRaw ? (
                <pre className="file-raw-markdown">{activeContent}</pre>
              ) : (
                <div className="file-prose">
                  {renderMarkdown(activeContent)}
                </div>
              )}
            </div>
          ) : (
            <div className="file-document-body">
              <div className="file-prose file-empty-hint">
                Select a document from the folder view to read it here.
              </div>
            </div>
          )}
        </div>
      </div>

      {/* 3. Floating Bottom Action Pill (matching screenshot bottom center) */}
      <div className="file-floating-bottom-bar">
        <button
          type="button"
          className="bottom-pill-btn"
          onClick={() => setShowRaw((v) => !v)}
        >
          <span>{showRaw ? 'Preview' : 'Edit'}</span>
          <span style={{ fontSize: 10 }}>▾</span>
        </button>

        <span className="bottom-pill-divider" />

        <button
          type="button"
          className="bottom-pill-btn"
          onClick={onOpenAiChat}
        >
          <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round">
            <path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/>
          </svg>
          <span>Chat</span>
        </button>

        <span className="bottom-pill-divider" />

        <button
          type="button"
          className="bottom-pill-btn"
          onClick={handleShare}
        >
          <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round">
            <path d="M4 12v8a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-8"/>
            <polyline points="16 6 12 2 8 6"/>
            <line x1="12" y1="2" x2="12" y2="15"/>
          </svg>
          <span>{copied ? 'Copied!' : 'Share'}</span>
        </button>
      </div>

      {/* Floating Bottom-Right Chat Bubble Button */}
      <button
        type="button"
        className="file-floating-chat-bubble"
        onClick={onOpenAiChat}
        title="Open AI weave assistant"
      >
        <svg width="18" height="18" viewBox="0 0 24 24" fill="currentColor">
          <circle cx="9" cy="12" r="1.5"/>
          <circle cx="15" cy="12" r="1.5"/>
        </svg>
      </button>
    </main>
  )
}
