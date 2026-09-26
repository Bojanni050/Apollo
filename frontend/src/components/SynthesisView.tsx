import { useState } from 'react'
import type { Repository } from '../api/client'
import { renderMarkdown } from '../markdown'

interface Props {
  repository: Repository | null
  path: string | null
  markdown: string | null
  onOpenConversation?: () => void
  onToggleRaw?: () => void
  showRaw?: boolean
}

export function SynthesisView({
  repository,
  path,
  markdown,
  onOpenConversation,
  onToggleRaw,
  showRaw = false,
}: Props) {
  const [internalRaw, setInternalRaw] = useState(false)
  const isRaw = onToggleRaw ? showRaw : internalRaw
  const toggleRaw = onToggleRaw || (() => setInternalRaw((v) => !v))

  if (!path) {
    return (
      <div className="synthesis-container">
        <div className="synthesis-empty-wrapper">
          <div className="synthesis-avatar-icon" style={{ margin: '0 auto 16px' }}>
            <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round">
              <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/>
              <polyline points="14 2 14 8 20 8"/>
              <line x1="16" y1="13" x2="8" y2="13"/>
              <line x1="16" y1="17" x2="8" y2="17"/>
              <polyline points="10 9 9 9 8 9"/>
            </svg>
          </div>
          <h2 className="synthesis-title" style={{ textAlign: 'center' }}>No Document Selected</h2>
          <div className="synthesis-meta" style={{ textAlign: 'center', marginBottom: 20 }}>
            SELECT A DOCUMENT FROM THE ARCHIVE TO VIEW ITS ARCHITECTURAL SYNTHESIS
          </div>
          <div className="synthesis-main-card" style={{ maxWidth: 560, margin: '0 auto', textAlign: 'center' }}>
            <p style={{ color: 'var(--text-dim)', fontSize: 13, lineHeight: 1.7, margin: 0 }}>
              Choose any documentation file, specification, or architectural decision record from the archive on the left to read its synthesized summary, review verified code implementations, and discuss proposals with the AI architect.
            </p>
          </div>
        </div>
      </div>
    )
  }

  if (markdown === null) {
    return (
      <div className="synthesis-container">
        <div className="synthesis-empty-wrapper">
          <div className="synthesis-avatar-icon" style={{ margin: '0 auto 16px' }}>
            ⏳
          </div>
          <h2 className="synthesis-title" style={{ textAlign: 'center' }}>Loading Document…</h2>
          <div className="synthesis-meta" style={{ textAlign: 'center' }}>
            {repository?.name} • {path}
          </div>
        </div>
      </div>
    )
  }

  // Derive real document title, abstract, and tags from actual content
  const cleanName = path.replace(/[\\/]/g, '/').split('/').pop() || path
  const nameWithoutExt = cleanName.replace(/\.[^/.]+$/, '')
  const rawTitle = nameWithoutExt.replace(/[-_]/g, ' ')
  const ext = path.split('.').pop()?.toUpperCase() || 'MD'
  const isAdr = path.toLowerCase().includes('adr') || path.toLowerCase().includes('decision')
  const folderName = path.includes('/') ? path.split('/')[0].toUpperCase() : 'ROOT'

  const lines = markdown.split(/\r?\n/).map((l) => l.trim()).filter((l) => l.length > 0)

  // Find first heading as title if present
  let displayTitle = rawTitle.toUpperCase()
  for (const line of lines) {
    if (line.startsWith('# ')) {
      displayTitle = line.replace(/^#+\s*/, '').trim().toUpperCase()
      break
    }
  }

  // Find first descriptive paragraph as abstract
  let abstract = `Consolidated architectural specification and structure for ${cleanName}.`
  for (const line of lines) {
    if (!line.startsWith('#') && !line.startsWith('```') && !line.startsWith('- ') && !line.startsWith('> ') && line.length > 25) {
      abstract = line.slice(0, 260) + (line.length > 260 ? '…' : '')
      break
    }
  }

  const tags = [
    `◇ ${folderName}`,
    `◇ ${ext}`,
    isAdr ? '◇ ADR' : '◇ SPEC',
    `◇ ${repository?.writable ? 'WRITABLE' : 'READ-ONLY'}`,
  ]

  return (
    <div className="synthesis-container">
      {/* Top Header */}
      <div className="synthesis-header">
        <div className="synthesis-header-left">
          <div className="synthesis-avatar-icon">
            <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.3" strokeLinecap="round" strokeLinejoin="round">
              <path d="M12 20h9"/>
              <path d="M16.5 3.5a2.121 2.121 0 0 1 3 3L7 19l-4 1 1-4L16.5 3.5z"/>
            </svg>
          </div>
          <div>
            <h2 className="synthesis-title">{displayTitle}</h2>
            <div className="synthesis-meta">
              APO ARCHITECTURE • {repository?.name} • {path}
            </div>
          </div>
        </div>

        <div className="synthesis-actions">
          {onOpenConversation && (
            <button
              type="button"
              className="synthesis-action-btn highlight"
              onClick={onOpenConversation}
              title="Discuss this document in Conversation"
            >
              💬 Chat about doc
            </button>
          )}
          <button
            type="button"
            className="synthesis-icon-btn"
            onClick={toggleRaw}
            title={isRaw ? 'Show rendered Markdown' : 'Show raw source text'}
          >
            {isRaw ? '📖' : '✎'}
          </button>
        </div>
      </div>

      {/* Abstract Callout Box with Perched Badge */}
      <div className="synthesis-abstract-card">
        <span className="synthesis-abstract-badge">ABSTRACT</span>
        <p className="synthesis-abstract-text">{abstract}</p>
      </div>

      {/* Tag Pills Row (centered) */}
      <div className="synthesis-tags-row">
        {tags.map((t) => (
          <span key={t} className="synthesis-tag-pill">
            {t}
          </span>
        ))}
      </div>

      {/* Main Content Floating Card with Dashed Border */}
      <div className="synthesis-main-card">
        {isRaw ? (
          <pre className="code">{markdown}</pre>
        ) : (
          <div className="synthesis-rendered-body">
            {!markdown.trim().startsWith('# ') && <h1>{displayTitle}</h1>}
            {renderMarkdown(markdown)}
          </div>
        )}
      </div>
    </div>
  )
}
