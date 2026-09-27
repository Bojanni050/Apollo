import { useCallback, useEffect, useState } from 'react'

/** Where the choice is kept. A theme is a per-machine preference, not app data. */
const STORAGE_KEY = 'apollo.theme'

export type Theme = 'default' | 'calm'

function readStoredTheme(): Theme {
  // Guard the whole read: a storage that throws (private mode, disabled
  // cookies) must not take the app down with it.
  try {
    return window.localStorage.getItem(STORAGE_KEY) === 'calm' ? 'calm' : 'default'
  } catch {
    return 'default'
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
 * text, and gold accents. It is applied as a data attribute on <html> so the
 * CSS variables cascade to every component without any of them knowing that
 * themes exist.
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
 * paint a whole screen in the default theme and then flip to Calm, which is
 * exactly the flash a theme toggle exists to prevent.
 */
export function applyStoredThemeBeforePaint() {
  applyTheme(readStoredTheme())
}
