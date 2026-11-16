import { useSyncExternalStore } from 'react'

/**
 * Display density and text size (M20.2, GA10.1): `<html data-density="compact">` and
 * `<html data-text="large">`, which theme.css turns into its CSS variables. Larger text scales the
 * root size, so every rem grows with it, touch targets included; compact rows take padding out of
 * the watchlist's rows and the books' cards, never their 48 dp.
 *
 * Display preferences of this device, kept in this browser's storage as colour-blind mode is
 * (display.ts); storage missing or blocked, a choice lasts for the visit. Applied when this module
 * loads, which src/sudarshan/index.ts does from main.tsx before React renders, so nothing is drawn
 * at one size and then redrawn at the other.
 */
export type Density = 'comfortable' | 'compact'
export type TextSize = 'normal' | 'large'

const DENSITY_KEY = 'sudarshan-density'
const TEXT_KEY = 'sudarshan-text'

const listeners = new Set<() => void>()

function read(key: string, value: string): boolean {
  try {
    return localStorage.getItem(key) === value
  } catch {
    return false
  }
}

let density: Density = read(DENSITY_KEY, 'compact') ? 'compact' : 'comfortable'
let textSize: TextSize = read(TEXT_KEY, 'large') ? 'large' : 'normal'

function apply(): void {
  if (typeof document === 'undefined') return
  const root = document.documentElement
  if (density === 'compact') root.setAttribute('data-density', 'compact')
  else root.removeAttribute('data-density')
  if (textSize === 'large') root.setAttribute('data-text', 'large')
  else root.removeAttribute('data-text')
}

function publish(): void {
  apply()
  for (const listener of listeners) listener()
}

/** Kept only when it is not the default, so the default stays whatever this build makes it. */
function keep(key: string, value: string | null): void {
  try {
    if (value === null) localStorage.removeItem(key)
    else localStorage.setItem(key, value)
  } catch {
    // Storage refused: the choice holds until the page is closed.
  }
}

export function setDensity(next: Density): void {
  density = next
  keep(DENSITY_KEY, next === 'compact' ? next : null)
  publish()
}

export function setTextSize(next: TextSize): void {
  textSize = next
  keep(TEXT_KEY, next === 'large' ? next : null)
  publish()
}

function subscribe(listener: () => void): () => void {
  listeners.add(listener)
  return () => listeners.delete(listener)
}

export function useDensity(): Density {
  return useSyncExternalStore(
    subscribe,
    () => density,
    () => 'comfortable' as const
  )
}

export function useTextSize(): TextSize {
  return useSyncExternalStore(
    subscribe,
    () => textSize,
    () => 'normal' as const
  )
}

apply()

// Another tab of the app changed one: follow, so two tabs never disagree.
if (typeof window !== 'undefined') {
  window.addEventListener('storage', (event) => {
    if (event.key !== null && event.key !== DENSITY_KEY && event.key !== TEXT_KEY) return
    density = read(DENSITY_KEY, 'compact') ? 'compact' : 'comfortable'
    textSize = read(TEXT_KEY, 'large') ? 'large' : 'normal'
    publish()
  })
}
