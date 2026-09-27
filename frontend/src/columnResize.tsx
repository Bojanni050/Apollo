import { useCallback, useEffect, useRef, useState, type CSSProperties, type PointerEvent as ReactPointerEvent, type KeyboardEvent as ReactKeyboardEvent } from 'react'

/** Where the widths are kept. A layout preference, like the theme. */
const STORAGE_KEY = 'apollo.column-widths'

export const MIN_NAV = 170
export const MIN_CONTENTS = 220

/**
 * Ceiling for the two list columns COMBINED, as a fraction of the viewport.
 *
 * The document column is the reason the app exists, and it is the only
 * flexible one (1fr). If the two lists could be dragged arbitrarily wide they
 * could squeeze the document down to a sliver, so they are capped together at
 * half the screen and the document always keeps the rest.
 *
 * The minimums can outvote the ceiling on a very narrow window: below roughly
 * 780px wide there is not enough room for both minimums and a usable document
 * column. The minimums win there, since a column too narrow to read is worse
 * than a document column that has collapsed, and the layout is not designed for
 * that width in the first place.
 */
export const MAX_COMBINED_FRACTION = 0.5

export interface ColumnWidths {
  nav: number
  contents: number
}

export const DEFAULT_WIDTHS: ColumnWidths = { nav: 230, contents: 290 }

function readStored(): ColumnWidths | null {
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY)
    if (!raw) return null
    const parsed = JSON.parse(raw)
    // isFinite rather than a bare typeof check: a hand-edited or corrupted
    // value must fall back to the defaults, and anything that reaches
    // clampWidths as NaN would poison the grid track and silently collapse the
    // layout on every load.
    if (!Number.isFinite(parsed?.nav) || !Number.isFinite(parsed?.contents)) return null
    return { nav: parsed.nav, contents: parsed.contents }
  } catch {
    return null
  }
}

/**
 * Keep both widths legal for the current viewport.
 *
 * Applied on every drag frame, on window resize, and when reading a stored
 * value -- a width saved on a 4K monitor is not automatically legal on a
 * laptop, and the ceiling is a fraction of the screen, so it moves.
 */
export function clampWidths(widths: ColumnWidths, viewportWidth: number): ColumnWidths {
  const ceiling = Math.max(
    MIN_NAV + MIN_CONTENTS,
    Math.floor(viewportWidth * MAX_COMBINED_FRACTION),
  )
  const nav = Math.max(MIN_NAV, Math.round(widths.nav))
  const contents = Math.max(MIN_CONTENTS, Math.round(widths.contents))
  if (nav + contents <= ceiling) return { nav, contents }

  const excess = nav + contents - ceiling
  // Shrink the navigation first: it is the column with the least to lose, and
  // the contents list benefits from more room the longer it gets.
  const navAfter = Math.max(MIN_NAV, nav - excess)
  if (navAfter + contents <= ceiling) return { nav: navAfter, contents }
  // The contents column cannot give back the rest, so take it from there.
  const contentsAfter = Math.max(MIN_CONTENTS, contents - excess)
  if (nav + contentsAfter <= ceiling) return { nav, contents: contentsAfter }
  // Both are at their minimum. `ceiling` is at least MIN_NAV + MIN_CONTENTS,
  // so this is only reachable on a very narrow window.
  return { nav: MIN_NAV, contents: Math.max(MIN_CONTENTS, ceiling - MIN_NAV) }
}

type Handle = 'nav' | 'contents'

interface DragState {
  handle: Handle
  startX: number
  startNav: number
  startContents: number
}

/**
 * Draggable widths for the navigation and contents columns.
 *
 * The widths live in React state and are written onto the layout as inline
 * custom properties, so the grid follows them without the stylesheet being
 * rewritten at runtime. Pointer events (not mouse events) so a touch drag works
 * too, and the listeners live on the window so a fast drag that outruns the
 * handle still tracks.
 */
export function useColumnResizers() {
  const [widths, setWidths] = useState<ColumnWidths>(() =>
    clampWidths(readStored() ?? DEFAULT_WIDTHS, window.innerWidth),
  )
  const drag = useRef<DragState | null>(null)

  // Re-clamp when the window changes: the ceiling is a fraction of the screen,
  // so shrinking the window has to shrink the columns with it.
  useEffect(() => {
    const onResize = () => setWidths((w) => clampWidths(w, window.innerWidth))
    window.addEventListener('resize', onResize)
    return () => window.removeEventListener('resize', onResize)
  }, [])

  useEffect(() => {
    try {
      window.localStorage.setItem(STORAGE_KEY, JSON.stringify(widths))
    } catch {
      // Persisting is a nicety; a storage that refuses must not stop the drag.
    }
  }, [widths])

  useEffect(() => {
    const onMove = (e: PointerEvent) => {
      const d = drag.current
      if (!d) return
      const delta = e.clientX - d.startX
      const proposed =
        d.handle === 'nav'
          ? { nav: d.startNav + delta, contents: d.startContents }
          : { nav: d.startNav, contents: d.startContents + delta }
      setWidths(clampWidths(proposed, window.innerWidth))
    }
    const end = () => {
      if (!drag.current) return
      drag.current = null
      document.body.classList.remove('is-col-resizing')
    }
    window.addEventListener('pointermove', onMove)
    window.addEventListener('pointerup', end)
    window.addEventListener('pointercancel', end)
    return () => {
      window.removeEventListener('pointermove', onMove)
      window.removeEventListener('pointerup', end)
      window.removeEventListener('pointercancel', end)
    }
  }, [])

  const startDrag = useCallback(
    (handle: Handle) => (e: React.PointerEvent) => {
      // Only the primary button starts a resize, and the event must not reach
      // the pane underneath or a click on the handle selects a row.
      if (e.button !== 0) return
      e.preventDefault()
      e.stopPropagation()
      drag.current = { handle, startX: e.clientX, startNav: widths.nav, startContents: widths.contents }
      document.body.classList.add('is-col-resizing')
    },
    [widths.nav, widths.contents],
  )

  const nudge = useCallback(
    (handle: Handle, delta: number) =>
      setWidths((w) =>
        clampWidths(
          handle === 'nav' ? { ...w, nav: w.nav + delta } : { ...w, contents: w.contents + delta },
          window.innerWidth,
        ),
      ),
    [],
  )

  const reset = useCallback(() => setWidths(clampWidths(DEFAULT_WIDTHS, window.innerWidth)), [])

  return { widths, startDrag, nudge, reset }
}

interface ResizerProps {
  /** Which column grows or shrinks. */
  side: 'nav' | 'contents'
  /** Distance from the layout's left edge to this divider, in pixels. */
  width: number
  onPointerDown: (e: ReactPointerEvent) => void
  onNudge: (delta: number) => void
  onReset: () => void
}

/** Pixels moved per arrow keypress; Shift makes it a coarse step. */
const NUDGE = 16
const NUDGE_COARSE = 48

/**
 * The draggable divider between two columns.
 *
 * A separator is a control, so it is exposed as one: focusable, with a label
 * and a value for assistive technology, and operable from the keyboard. Dragging
 * alone would make the layout unreachable without a mouse.
 */
export function ColumnResizer({ side, width, onPointerDown, onNudge, onReset }: ResizerProps) {
  const label = side === 'nav' ? 'Resize navigation column' : 'Resize contents column'

  const onKeyDown = (e: ReactKeyboardEvent) => {
    if (e.key === 'ArrowLeft') {
      onNudge(e.shiftKey ? -NUDGE_COARSE : -NUDGE)
    } else if (e.key === 'ArrowRight') {
      onNudge(e.shiftKey ? NUDGE_COARSE : NUDGE)
    } else if (e.key === 'Home') {
      onReset()
    } else {
      return
    }
    e.preventDefault()
  }

  return (
    <div
      className={`col-resizer col-resizer-${side}`}
      style={{ left: `${width}px` } as CSSProperties}
      role="separator"
      aria-orientation="vertical"
      aria-label={label}
      aria-valuenow={Math.round(width)}
      tabIndex={0}
      onPointerDown={onPointerDown}
      onKeyDown={onKeyDown}
      onDoubleClick={onReset}
      title={`${label} — drag, arrow keys, or double-click to reset`}
    >
      <span className="col-resizer-grip" />
    </div>
  )
}
