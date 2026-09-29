import { useCallback, useEffect, useRef, useState, type CSSProperties, type PointerEvent as ReactPointerEvent, type KeyboardEvent as ReactKeyboardEvent } from 'react'

/** Where the widths are kept. A layout preference, like the theme.

 * Versioned (`v3`) because the defaults changed shape. v2 stored a fixed pixel
 * pair (414/522) and v1 an older one; either would pin the layout to those exact
 * pixels on load and make the new proportional defaults look like they had not
 * taken effect -- a silent failure that reads as a broken change. Discarding the
 * stored pair once is cheaper than debugging it.
 *
 * v3 also records whether the widths were chosen by hand; see `customised`.
 */
const STORAGE_KEY = 'apollo.column-widths.v3'

export const MIN_NAV = 170
export const MIN_CONTENTS = 220

/**
 * Narrowest the document column may be squeezed to.
 *
 * The document is the reason the app exists, so it is the one column that is
 * never allowed to become a sliver. Every other width here is measured against
 * what this has to keep.
 */
export const MIN_DOCUMENT = 200

/**
 * The context panel as a share of the viewport: 380/2560, the width it had as a
 * fixed 380px on the 2560px layout this is modelled on.
 *
 * It scales with the window like the list columns do, but between a floor and a
 * ceiling. The floor is CONTEXT_MIN = 380, not something smaller, because the
 * panel is also the AI Chat: its mode names, their explanations and the
 * suggested prompts need 380px to stay readable, and at 300px they wrapped into
 * short stubs. The ceiling stops the panel competing with the document once it
 * is wide enough to be comfortable.
 *
 * So the distribution is proportional down to about 2560px and floored below
 * that. That floor is the one deliberate exception, and it costs the document
 * 80px on a 1440 screen -- a trade made knowingly, because a chat pane nobody
 * can read is worse than a slightly narrower article.
 */
export const CONTEXT_FRACTION = 0.14844
export const CONTEXT_MIN = 380
export const CONTEXT_MAX = 520

/** The context panel's width at this viewport. Mirrors the CSS clamp() in app.css. */
export function contextWidth(viewportWidth: number): number {
  return Math.round(Math.min(CONTEXT_MAX, Math.max(CONTEXT_MIN, viewportWidth * CONTEXT_FRACTION)))
}

/**
 * Width the two list columns must leave alone on the right: the context panel
 * plus a document column wide enough to actually read.
 *
 * Both parts move with the viewport now that the context panel scales too. At
 * 1440 this is 380 + 200 = 580, so the lists may take at most 860px between
 * them -- the context panel keeps its readable width and the document keeps a
 * readable one, and the two list columns get what is left.
 */
export function reservedRight(viewportWidth: number): number {
  return contextWidth(viewportWidth) + MIN_DOCUMENT
}

/**
 * Ceiling for the two list columns COMBINED, as a fraction of the viewport.
 *
 * The document column is the reason the app exists, and it is the only flexible
 * one (1fr), so the two lists are capped both as a share of the screen and by
 * the space they must leave on the right.
 */
export const MAX_COMBINED_FRACTION = 0.75

export interface ColumnWidths {
  nav: number
  contents: number
  /**
   * The context sidebar's width. Unlike the two list columns this one has a
   * hard floor (CONTEXT_MIN: the AI Chat needs 380px to stay readable) and a
   * ceiling (CONTEXT_MAX), so a drag cannot collapse the chat or crowd out
   * the document entirely. Zero means "not customised": the panel then
   * follows the clamp() default from app.css.
   */
  context: number
}

/**
 * The two list columns as shares of the viewport, taken from the 2560px layout:
 * 414/2560 and 522/2560.
 *
 * Fractions rather than pixels, so the distribution is the same at every
 * resolution instead of the same pixel count. Fixed 414/522 gave the right
 * balance on a 2560 screen, a 124px sliver for the document on 1440, and a
 * comically wide pair on a 3440 screen. The ratio between the two columns is
 * preserved exactly (414:522), so this changes the size, never the shape.
 */
const NAV_FRACTION = 0.16172
const CONTENTS_FRACTION = 0.20391

/** The default layout at this viewport. */
export function defaultWidths(viewportWidth: number): ColumnWidths {
  return clampWidths(
    {
      nav: Math.round(viewportWidth * NAV_FRACTION),
      contents: Math.round(viewportWidth * CONTENTS_FRACTION),
      // Zero: not customised, so the panel follows the CSS clamp() default.
      context: 0,
    },
    viewportWidth,
  )
}

interface StoredLayout {
  widths: ColumnWidths
  /**
   * True once the user has dragged, nudged or reset a column. While this is
   * false the layout is recomputed from the fractions on every resize, so
   * changing resolution keeps the proportions; a hand-picked pixel width cannot
   * scale like that, so from then on it is only re-clamped, never re-derived.
   */
  customised: boolean
}

function readStored(): StoredLayout | null {
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY)
    if (!raw) return null
    const parsed = JSON.parse(raw)
    // isFinite rather than a bare typeof check: a hand-edited or corrupted
    // value must fall back to the defaults, and anything that reaches
    // clampWidths as NaN would poison the grid track and silently collapse the
    // layout on every load.
    if (!Number.isFinite(parsed?.nav) || !Number.isFinite(parsed?.contents)) return null
    return {
      widths: {
        nav: parsed.nav,
        contents: parsed.contents,
        // v4 added the context width; older stores have none, and 0 means
        // "follow the CSS clamp() default" rather than a literal zero.
        context: Number.isFinite(parsed?.context) ? parsed.context : 0,
      },
      customised: parsed.customised === true,
    }
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
    Math.min(
      Math.floor(viewportWidth * MAX_COMBINED_FRACTION),
      viewportWidth - reservedRight(viewportWidth),
    ),
  )
  const nav = Math.max(MIN_NAV, Math.round(widths.nav))
  const contents = Math.max(MIN_CONTENTS, Math.round(widths.contents))
  // The context panel: 0 means "not customised" and keeps the CSS clamp()
  // default; any explicit width is held between the chat's readable floor and
  // the ceiling that stops it crowding the document.
  const context = widths.context <= 0 ? 0 : Math.min(CONTEXT_MAX, Math.max(CONTEXT_MIN, Math.round(widths.context)))
  if (nav + contents <= ceiling) return { nav, contents, context }

  const excess = nav + contents - ceiling
  // Shrink the navigation first: it is the column with the least to lose, and
  // the contents list benefits from more room the longer it gets.
  const navAfter = Math.max(MIN_NAV, nav - excess)
  if (navAfter + contents <= ceiling) return { nav: navAfter, contents, context }
  // The contents column cannot give back the rest, so take it from there.
  const contentsAfter = Math.max(MIN_CONTENTS, contents - excess)
  if (nav + contentsAfter <= ceiling) return { nav, contents: contentsAfter, context }
  // Both are at their minimum. `ceiling` is at least MIN_NAV + MIN_CONTENTS,
  // so this is only reachable on a very narrow window.
  return { nav: MIN_NAV, contents: Math.max(MIN_CONTENTS, ceiling - MIN_NAV), context }
}

type Handle = 'nav' | 'contents' | 'context'

interface DragState {
  handle: Handle
  startX: number
  startNav: number
  startContents: number
  startContext: number
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
  // `customised` is kept in a ref, not state: it decides what a resize does, not
  // what is painted, so toggling it must not trigger a render or a re-save.
  const initial = useRef(readStored()).current
  const customised = useRef(initial?.customised ?? false)
  const [widths, setWidths] = useState<ColumnWidths>(() =>
    clampWidths(
      initial?.customised ? initial.widths : defaultWidths(window.innerWidth),
      window.innerWidth,
    ),
  )
  const drag = useRef<DragState | null>(null)

  /** Record a width the user asked for, as opposed to one the fractions produced. */
  const setChosen = useCallback(
    (next: ColumnWidths | ((w: ColumnWidths) => ColumnWidths)) => {
      customised.current = true
      setWidths(next)
    },
    [],
  )

  // React to a window change. Two different things, and conflating them is what
  // made fixed pixel defaults look wrong at every resolution except the one they
  // were typed on:
  //
  //   - Not customised: re-derive from the fractions, so the distribution holds
  //     at the new size instead of the columns keeping the same pixel count.
  //   - Customised: only re-clamp. A width the user dragged to on a 3440 screen
  //     is an absolute choice, and scaling it would silently undo their drag.
  useEffect(() => {
    const onResize = () =>
      setWidths((w) =>
        clampWidths(customised.current ? w : defaultWidths(window.innerWidth), window.innerWidth),
      )
    window.addEventListener('resize', onResize)
    return () => window.removeEventListener('resize', onResize)
  }, [])

  useEffect(() => {
    try {
      window.localStorage.setItem(
        STORAGE_KEY,
        JSON.stringify({ ...widths, customised: customised.current }),
      )
    } catch {
      // Persisting is a nicety; a storage that refuses must not stop the drag.
    }
  }, [widths])

  useEffect(() => {
    const onMove = (e: PointerEvent) => {
      const d = drag.current
      if (!d) return
      const delta = e.clientX - d.startX
      // Dragging the context divider left makes the panel wider, so the
      // delta's sign flips relative to the two left-side handles.
      const proposed =
        d.handle === 'nav'
          ? { nav: d.startNav + delta, contents: d.startContents, context: d.startContext }
          : d.handle === 'contents'
            ? { nav: d.startNav, contents: d.startContents + delta, context: d.startContext }
            : { nav: d.startNav, contents: d.startContents, context: d.startContext - delta }
      setChosen(clampWidths(proposed, window.innerWidth))
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
      drag.current = { handle, startX: e.clientX, startNav: widths.nav, startContents: widths.contents, startContext: widths.context }
      document.body.classList.add('is-col-resizing')
    },
    [widths.nav, widths.contents, widths.context],
  )

  const nudge = useCallback(
    (handle: Handle, delta: number) =>
      setChosen((w) =>
        clampWidths(
          handle === 'nav'
            ? { ...w, nav: w.nav + delta }
            : handle === 'contents'
              ? { ...w, contents: w.contents + delta }
              : { ...w, context: (w.context || contextWidth(window.innerWidth)) - delta },
          window.innerWidth,
        ),
      ),
    [setChosen],
  )

  /**
   * Back to the proportional layout for the current window.
   *
   * This also clears `customised`, so the columns track the viewport again from
   * here on. Resetting to fixed pixels while still claiming to be "proportional"
   * would leave the layout stuck at one size until the next manual drag.
   */
  const reset = useCallback(() => {
    customised.current = false
    setWidths(defaultWidths(window.innerWidth))
  }, [])

  return { widths, startDrag, nudge, reset }
}

interface ResizerProps {
  /** Which column grows or shrinks. */
  side: 'nav' | 'contents' | 'context'
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
  const label =
    side === 'nav'
      ? 'Resize navigation column'
      : side === 'contents'
        ? 'Resize contents column'
        : 'Resize context panel'

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
