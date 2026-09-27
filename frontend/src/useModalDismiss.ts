import { useEffect } from 'react'

/**
 * Dismiss a dialog with Escape.
 *
 * Escape is the only keyboard dismissal, so it has to work: without this a
 * dialog that no longer closes on a backdrop click would have no keyboard way
 * out at all, which is a trap for anyone not using a mouse.
 *
 * Bound on the window rather than the dialog so it fires without the dialog
 * having to hold focus, and only while `isOpen` so two dialogs can never both
 * react to one keypress.
 */
export function useModalDismiss(isOpen: boolean, onDismiss: () => void) {
  useEffect(() => {
    if (!isOpen) return
    const onKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        e.stopPropagation()
        onDismiss()
      }
    }
    window.addEventListener('keydown', onKeyDown)
    return () => window.removeEventListener('keydown', onKeyDown)
  }, [isOpen, onDismiss])
}
