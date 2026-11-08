import { useSyncExternalStore } from 'react'

/**
 * The switch for the dashboard's Portfolio card (M3.11, design 8.3). Off by default: owner
 * decision P5 has the card replace the Holdings card only one release after his first check of
 * it, so this release shows it beside the Holdings card, and only on a phone where the owner
 * turned it on under Display settings. A display preference of this device, kept in this
 * browser's storage like colour-blind mode (display.ts); storage missing or blocked, the choice
 * lasts for the visit.
 */
const STORAGE_KEY = 'sudarshan-portfolio-card'

const listeners = new Set<() => void>()

function stored(): boolean {
  try {
    return localStorage.getItem(STORAGE_KEY) === '1'
  } catch {
    return false
  }
}

let enabled = stored()

function publish(): void {
  for (const listener of listeners) listener()
}

export function setPortfolioCard(on: boolean): void {
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

/** Whether the Portfolio card is on, re-rendering when it changes. */
export function usePortfolioCard(): boolean {
  return useSyncExternalStore(
    subscribe,
    () => enabled,
    () => false
  )
}

if (typeof window !== 'undefined') {
  window.addEventListener('storage', (event) => {
    if (event.key !== STORAGE_KEY && event.key !== null) return
    enabled = stored()
    publish()
  })
}
