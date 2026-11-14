import { useSyncExternalStore } from 'react'

/**
 * Which indices the Dashboard's strip shows (M3.3, D8): the owner's choice of
 * one to six from the ones below, in this order, NIFTY 50, NIFTY BANK, INDIA
 * VIX and SENSEX until he chooses. Six is what the Dashboard's share of the
 * one pooled market-data connection allows the strip.
 *
 * A display preference of this device, like colour-blind mode (`display.ts`):
 * kept in this browser's storage, followed across tabs, and held for the visit
 * when storage is refused.
 */
export const INDEX_CHOICES = [
  { symbol: 'NIFTY', exchange: 'NSE_INDEX', label: 'NIFTY 50' },
  { symbol: 'BANKNIFTY', exchange: 'NSE_INDEX', label: 'NIFTY BANK' },
  { symbol: 'INDIAVIX', exchange: 'NSE_INDEX', label: 'INDIA VIX' },
  { symbol: 'SENSEX', exchange: 'BSE_INDEX', label: 'SENSEX' },
  { symbol: 'FINNIFTY', exchange: 'NSE_INDEX', label: 'NIFTY FIN SERVICE' },
  { symbol: 'MIDCPNIFTY', exchange: 'NSE_INDEX', label: 'NIFTY MIDCAP SELECT' },
  { symbol: 'NIFTYNXT50', exchange: 'NSE_INDEX', label: 'NIFTY NEXT 50' },
  { symbol: 'BANKEX', exchange: 'BSE_INDEX', label: 'BANKEX' },
] as const

export type IndexChoice = (typeof INDEX_CHOICES)[number]

export const MAX_INDICES = 6
export const DEFAULT_INDICES: readonly string[] = ['NIFTY', 'BANKNIFTY', 'INDIAVIX', 'SENSEX']

const STORAGE_KEY = 'sudarshan-dashboard-indices'
const listeners = new Set<() => void>()

/** The symbols as given, kept to the ones on offer, at most six, at least one; else the default. */
function clean(value: unknown): readonly string[] {
  if (!Array.isArray(value)) return DEFAULT_INDICES
  const picked = INDEX_CHOICES.map((choice) => choice.symbol as string)
    .filter((symbol) => value.includes(symbol))
    .slice(0, MAX_INDICES)
  return picked.length ? picked : DEFAULT_INDICES
}

function stored(): readonly string[] {
  try {
    const raw = localStorage.getItem(STORAGE_KEY)
    return raw === null ? DEFAULT_INDICES : clean(JSON.parse(raw))
  } catch {
    return DEFAULT_INDICES
  }
}

let chosen = stored()

function publish(next: readonly string[]): void {
  chosen = next
  for (const listener of [...listeners]) listener()
}

/** Why a change to the choice was refused; null when it was made. */
export type IndexRefusal = 'full' | 'last' | null

export const INDEX_REFUSED: Record<Exclude<IndexRefusal, null>, string> = {
  full: 'Six indices at most. Turn one off to show another.',
  last: 'One index stays on the Dashboard.',
}

/** Show or hide one index. The seventh is refused, and so is hiding the last. */
export function setDashboardIndex(symbol: string, on: boolean): IndexRefusal {
  const has = chosen.includes(symbol)
  if (on === has) return null
  if (on && chosen.length >= MAX_INDICES) return 'full'
  if (!on && chosen.length <= 1) return 'last'
  const next = clean(on ? [...chosen, symbol] : chosen.filter((one) => one !== symbol))
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(next))
  } catch {
    // Storage refused: the choice holds until the page is closed.
  }
  publish(next)
  return null
}

function subscribe(listener: () => void): () => void {
  listeners.add(listener)
  return () => listeners.delete(listener)
}

/** The chosen indices' symbols, in the order they are shown, re-rendering when the choice changes. */
export function useDashboardIndices(): readonly string[] {
  return useSyncExternalStore(
    subscribe,
    () => chosen,
    () => DEFAULT_INDICES
  )
}

/** Tests only: read the choice from storage again, as a fresh page would. */
export function resetDashboardIndicesForTests(): void {
  publish(stored())
}

// Another tab of the app changed it: follow, so two tabs never disagree.
if (typeof window !== 'undefined') {
  window.addEventListener('storage', (event) => {
    if (event.key !== STORAGE_KEY && event.key !== null) return
    publish(stored())
  })
}
