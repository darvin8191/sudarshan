import { useSyncExternalStore } from 'react'

/**
 * Colour-blind (CVD) mode (M1.13): `<html data-cvd="1">`, which theme.css reads
 * to move up and down from green and red to blue and orange.
 *
 * A display preference of this device, so it lives in this browser's storage
 * and not on the server: it changes how this screen draws, not what the
 * account holds. Storage can be missing or blocked (a private window, cleared
 * site data), and then the choice simply lasts for the visit.
 *
 * Applied when this module loads, which src/sudarshan/index.ts does from
 * main.tsx before React renders, so no chart or number is ever drawn in the
 * wrong pair first.
 */
const STORAGE_KEY = 'sudarshan-cvd'

const listeners = new Set<() => void>()

function stored(): boolean {
  try {
    return localStorage.getItem(STORAGE_KEY) === '1'
  } catch {
    return false
  }
}

let enabled = stored()

function apply(): void {
  if (typeof document === 'undefined') return
  if (enabled) document.documentElement.setAttribute('data-cvd', '1')
  else document.documentElement.removeAttribute('data-cvd')
}

function publish(): void {
  apply()
  for (const listener of listeners) listener()
}

export function setCvdMode(on: boolean): void {
  enabled = on
  try {
    if (on) localStorage.setItem(STORAGE_KEY, '1')
    else localStorage.removeItem(STORAGE_KEY)
  } catch {
    // Storage refused: the choice holds until the page is closed.
  }
  publish()
}

function subscribe(listener: () => void): () => void {
  listeners.add(listener)
  return () => listeners.delete(listener)
}

/** Whether colour-blind mode is on, re-rendering when it changes. */
export function useCvdMode(): boolean {
  return useSyncExternalStore(
    subscribe,
    () => enabled,
    () => false
  )
}

apply()

// Another tab of the app changed it: follow, so two tabs never disagree.
if (typeof window !== 'undefined') {
  window.addEventListener('storage', (event) => {
    if (event.key !== STORAGE_KEY && event.key !== null) return
    enabled = stored()
    publish()
  })
}
