import { useSyncExternalStore } from 'react'

/**
 * How figures are grouped (M20.3): Indian (12,34,567.89, with L and Cr in short figures), which
 * is the default, or international (1,234,567.89, with M and B). A display preference of this
 * device, kept in this browser's storage like colour-blind mode (display.ts); storage missing or
 * blocked, the choice lasts for the visit. The shared formatter (format.ts) reads it at every
 * call, so a screen opened after the switch is drawn in the new grouping.
 */
export type NumberGrouping = 'indian' | 'international'

const STORAGE_KEY = 'sudarshan-number-grouping'

const listeners = new Set<() => void>()

function stored(): NumberGrouping {
  try {
    return localStorage.getItem(STORAGE_KEY) === 'international' ? 'international' : 'indian'
  } catch {
    return 'indian'
  }
}

let grouping = stored()

function publish(): void {
  for (const listener of listeners) listener()
}

export function numberGrouping(): NumberGrouping {
  return grouping
}

/** The locale whose digit grouping the choice asks for. */
export function numberLocale(): 'en-IN' | 'en-US' {
  return grouping === 'international' ? 'en-US' : 'en-IN'
}

export function setNumberGrouping(next: NumberGrouping): void {
  grouping = next
  try {
    if (next === 'international') localStorage.setItem(STORAGE_KEY, next)
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

/** The grouping in use, re-rendering when it changes. */
export function useNumberGrouping(): NumberGrouping {
  return useSyncExternalStore(
    subscribe,
    () => grouping,
    () => 'indian' as const
  )
}

if (typeof window !== 'undefined') {
  window.addEventListener('storage', (event) => {
    if (event.key !== STORAGE_KEY && event.key !== null) return
    grouping = stored()
    publish()
  })
}
