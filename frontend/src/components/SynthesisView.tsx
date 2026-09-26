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
  repository: _repository,
  path,
  markdown,
  onOpenConversation,
  onToggleRaw,
  showRaw = false,
}: Props) {
  const [internalRaw, setInternalRaw] = useState(false)
  const isRaw = onToggleRaw ? showRaw : internalRaw
  const toggleRaw = onToggleRaw || (() => setInternalRaw((v) => !v))

  // If no document is selected yet, render the showcase Bioinformatics Framework from screenshot
  const isDemo = !path
  const cleanName = path ? (path.replace(/[\\/]/g, '/').split('/').pop() || path) : 'bioinformatics-framework.md'
  const nameWithoutExt = cleanName.replace(/\.[^/.]+$/, '')
  const rawTitle = nameWithoutExt.replace(/[-_]/g, ' ')
  const title = (rawTitle.toLowerCase().includes('synthesis') ? rawTitle : `SYNTHESIS: ${rawTitle}`).toUpperCase()
  const displaySummaryHeading = `${rawTitle.replace(/^synthesis[:\s]*/i, '').trim().toUpperCase() || 'BIOINFORMATICS'} STRATEGIC SUMMARY`

  const folderName = path && path.includes('/') ? path.split('/')[0].toUpperCase() : 'GENETICS'

  let firstParagraph = 'Consolidated intelligence for bioinformatics strategy.'
  if (markdown) {
    const lines = markdown.split(/\r?\n/).filter((l) => l.trim().length > 0)
    for (const line of lines) {
      const stripped = line.trim()
      if (!stripped.startsWith('#') && stripped.length > 20) {
        firstParagraph = stripped.slice(0, 260) + (stripped.length > 260 ? '…' : '')
        break
      }
    }
  }

  const tags = [
    `◇ ${folderName}`,
    '◇ SYNTHESIS',
    '◇ STRATEGIC',
  ]

  const todayStr = 'SEPTEMBER 25, 2026'

  return (
    <div className="synthesis-container">
      {/* Top Header matching screenshot */}
      <div className="synthesis-header">
        <div className="synthesis-header-left">
          <div className="synthesis-avatar-icon">
            <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.3" strokeLinecap="round" strokeLinejoin="round">
              <path d="M12 20h9"/>
              <path d="M16.5 3.5a2.121 2.121 0 0 1 3 3L7 19l-4 1 1-4L16.5 3.5z"/>
            </svg>
          </div>
          <div>
            <h2 className="synthesis-title">{title}</h2>
            <div className="synthesis-meta">
              PERSONAL SYNTHESIS • {todayStr}
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
          <button
            type="button"
            className="synthesis-icon-btn"
            title="Create sub-note or add section"
            onClick={() => onOpenConversation && onOpenConversation()}
          >
            +
          </button>
          <button
            type="button"
            className="synthesis-icon-btn"
            title="Delete or archive"
            onClick={() => {}}
          >
            🗑
          </button>
        </div>
      </div>

      {/* Abstract Callout Box with Perched Badge */}
      <div className="synthesis-abstract-card">
        <span className="synthesis-abstract-badge">ABSTRACT</span>
        <p className="synthesis-abstract-text">{firstParagraph}</p>
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
        {isRaw && markdown ? (
          <pre className="code">{markdown}</pre>
        ) : isDemo || !markdown ? (
          <div className="synthesis-rendered-body">
            <h1>{displaySummaryHeading}</h1>
            <p>
              Consolidating findings from multiple AI interactions regarding sequencing ancient dna from permafrost samples.
            </p>
            <h2>KEY DRIVERS</h2>
            <ul>
              <li>genetics stability</li>
              <li>data integration</li>
              <li>Optimization of medicine</li>
            </ul>
            <p className="synthesis-footer-note">
              This synthesis serves as a foundation for further architectural planning.
            </p>
          </div>
        ) : (
          <div className="synthesis-rendered-body">
            {/* If markdown doesn't have an h1, provide the Chronicle header */}
            {!markdown.trim().startsWith('# ') && <h1>{displaySummaryHeading}</h1>}
            {renderMarkdown(markdown)}
          </div>
        )}
      </div>
    </div>
  )
}
