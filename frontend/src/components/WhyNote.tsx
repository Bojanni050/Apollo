import { useEffect, useState } from 'react'

/**
 * A pinned reminder of what this workspace is for.
 *
 * The default text describes what the software visibly does, and is deliberately
 * modest. The reason a project exists belongs to the person building it, so this
 * is editable and kept in localStorage rather than being a slogan in the source:
 * the words should be replaceable by whoever is actually doing the work.
 */
const STORAGE_KEY = 'apollo.why-note'

const DEFAULT_TEXT = [
  'This exists so that what I believe about my own system is written down, checkable, and kept honest.',
  '',
  'Documents are the source of truth and live in Git, so a wrong claim is always recoverable. Delphi Pulse reads the corpus and proposes tags and links, but proposes only — nothing is written without my saying so. Decisions and open questions are first-class, because "we chose this" and "we do not know yet" are both worth keeping.',
].join('\n')

export function WhyNote() {
  const [text, setText] = useState(DEFAULT_TEXT)
  const [editing, setEditing] = useState(false)
  const [collapsed, setCollapsed] = useState(false)

  useEffect(() => {
    try {
      const stored = window.localStorage.getItem(STORAGE_KEY)
      // An empty stored value is a deliberate blank, not a missing note, so it is
      // honoured. `null` is the only thing that falls back to the default.
      if (stored !== null) setText(stored)
    } catch {
      // Private browsing, or storage disabled: the default still shows.
    }
  }, [])

  const save = (next: string) => {
    setText(next)
    try {
      window.localStorage.setItem(STORAGE_KEY, next)
    } catch {
      // Not being able to save is not a reason to refuse the edit.
    }
  }

  return (
    <div className="why-note">
      <div className="why-note-head">
        <button
          type="button"
          className="why-note-title"
          onClick={() => setCollapsed((v) => !v)}
          aria-expanded={!collapsed}
          title={collapsed ? 'Show why' : 'Hide'}
        >
          <span className="why-note-caret">{collapsed ? '▸' : '▾'}</span>
          Why this exists
        </button>
        <button
          type="button"
          className="why-note-edit"
          onClick={() => (editing ? (setEditing(false), save(text)) : setEditing(true))}
          title={editing ? 'Save' : 'Rewrite this in your own words'}
        >
          {editing ? 'Save' : 'Edit'}
        </button>
      </div>

      {!collapsed &&
        (editing ? (
          <textarea
            className="why-note-editor"
            value={text}
            autoFocus
            onChange={(e) => setText(e.target.value)}
            onBlur={() => {
              setEditing(false)
              save(text)
            }}
            rows={6}
          />
        ) : (
          <p className="why-note-body">{text}</p>
        ))}
    </div>
  )
}
