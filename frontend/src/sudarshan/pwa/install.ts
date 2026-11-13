// SUDARSHAN P6 D8 (M20.10): the "Add सुदर्शन to your home screen" prompt. Chrome offers an
// install through a `beforeinstallprompt` event, once, at a moment of its choosing; the page keeps
// that event and uses it only when the member taps. It is offered at the end of joining and again
// from Settings, never on its own. Where Chrome did not offer one (already installed, or a
// phone that never does), the screen says where the menu item is instead.
import { useSyncExternalStore } from 'react'

export const INSTALL_TITLE = 'Add सुदर्शन to your home screen'
export const INSTALL_FALLBACK = 'Chrome menu, Add to Home screen'

interface InstallEvent extends Event {
  prompt: () => Promise<void>
  userChoice: Promise<{ outcome: 'accepted' | 'dismissed' }>
}

export type InstallState = 'available' | 'installed' | 'unavailable'

let deferred: InstallEvent | null = null
let installed = false
const listeners = new Set<() => void>()

function changed(): void {
  for (const listener of [...listeners]) listener()
}

function standalone(): boolean {
  try {
    return (
      window.matchMedia?.('(display-mode: standalone)').matches === true ||
      (navigator as unknown as { standalone?: boolean }).standalone === true
    )
  } catch {
    return false
  }
}

if (typeof window !== 'undefined') {
  window.addEventListener('beforeinstallprompt', (event) => {
    // Held, not shown: Chrome's own mini bar would otherwise appear without a tap.
    event.preventDefault()
    deferred = event as InstallEvent
    changed()
  })
  window.addEventListener('appinstalled', () => {
    installed = true
    deferred = null
    changed()
  })
}

export function installState(): InstallState {
  if (installed || standalone()) return 'installed'
  return deferred ? 'available' : 'unavailable'
}

/**
 * Show Chrome's install question. Call it from a tap. Resolves to what the member answered, or
 * 'unavailable' where there was nothing to show (the event is used once, whatever the answer).
 */
export async function promptInstall(): Promise<'accepted' | 'dismissed' | 'unavailable'> {
  const event = deferred
  if (!event) return 'unavailable'
  deferred = null
  changed()
  try {
    await event.prompt()
    const choice = await event.userChoice
    if (choice.outcome === 'accepted') {
      installed = true
      changed()
    }
    return choice.outcome
  } catch {
    return 'unavailable'
  }
}

/** The install state, as a hook: it changes when Chrome offers the prompt or the app is added. */
export function useInstallState(): InstallState {
  return useSyncExternalStore(
    (listener) => {
      listeners.add(listener)
      return () => listeners.delete(listener)
    },
    installState,
    () => 'unavailable'
  )
}

/** For tests only: forget the event and the answer. */
export function resetInstallForTests(): void {
  deferred = null
  installed = false
  listeners.clear()
}
