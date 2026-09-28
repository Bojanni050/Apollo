import { useCallback, useEffect, useState } from 'react'

/** Where the choice is kept. A theme is a per-machine preference, not app data. */
const STORAGE_KEY = 'apollo.theme'

export type Theme = 'default' | 'calm'

/**
 * The theme a reader gets who has never been asked.
 *
 * Dark, for the reason the Calm palette was built in the first place: a large
 * white field at full brightness is what tires the eyes in a long session with
 * documents. This is only a default -- a stored choice always wins, including a
 * stored light one. The default speaks for a reader who has not had an opinion
 * yet, and quietly overriding somebody who has is the one thing it must not do.
 */
const DEFAULT_THEME: Theme = 'calm'

function readStoredTheme(): Theme {
  // Guard the whole read: a storage that throws (private mode, disabled
  // cookies) must not take the app down with it.
  try {
    const stored = window.localStorage.getItem(STORAGE_KEY)
    // Anything unrecognised falls through to the default rather than to light.
    return stored === 'default' || stored === 'calm' ? stored : DEFAULT_THEME
  } catch {
    return DEFAULT_THEME
  }
}

function applyTheme(theme: Theme) {
  const root = document.documentElement
  if (theme === 'calm') {
    root.setAttribute('data-theme', 'calm')
  } else {
    root.removeAttribute('data-theme')
  }
}

/**
 * Read and change the colour theme.
 *
 * Calm Mode repaints the app in the dark, warm palette used on
 * intro.higaia.nl: a warm near-black page instead of pure white, soft cream
 * text, and gold accents. It is the default, and it is applied as a data
 * attribute on <html> so the CSS variables cascade to every component without
 * any of them knowing that themes exist.
 */
export function useTheme(): [Theme, (theme: Theme) => void] {
  const [theme, setThemeState] = useState<Theme>(readStoredTheme)

  useEffect(() => {
    applyTheme(theme)
  }, [theme])

  const setTheme = useCallback((next: Theme) => {
    setThemeState(next)
    try {
      window.localStorage.setItem(STORAGE_KEY, next)
    } catch {
      // The theme still applies for this session; it just will not be
      // remembered. Not worth interrupting the user over.
    }
  }, [])

  return [theme, setTheme]
}

/**
 * Apply the stored theme before the first paint.
 *
 * Called from main.tsx rather than waiting for React: otherwise the app would
 * paint a whole screen in the light theme and then flip to the dark one, which
 * is exactly the flash a theme toggle exists to prevent.
 */
export function applyStoredThemeBeforePaint() {
  applyTheme(readStoredTheme())
}
