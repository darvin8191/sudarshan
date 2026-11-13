/**
 * The symbols last added from the search sheet on this device (M8.18, F13). A per-device convenience,
 * so they live in this browser only: every read and write is wrapped, and a refused storage (a private
 * window, blocked site data) leaves the sheet working with no recents and no error.
 */
export const RECENTS_KEY = 'sudarshan-watchlist-recents'

/** How many recents are kept. */
export const RECENTS_MAX = 20

export interface Recent {
  symbol: string
  exchange: string
  /** The trader's words the sheet showed ("NIFTY 25,900 CE" over "NFO · 24 Nov 2026 · lot 65"). */
  title: string
  detail: string
}

const text = (value: unknown): value is string => typeof value === 'string' && value !== ''

function valid(value: unknown): value is Recent {
  if (value === null || typeof value !== 'object') return false
  const r = value as Record<string, unknown>
  return text(r.symbol) && text(r.exchange) && text(r.title) && typeof r.detail === 'string'
}

/** The recents, newest first; nothing at all when storage is refused or holds something else. */
export function readRecents(): Recent[] {
  try {
    const parsed: unknown = JSON.parse(localStorage.getItem(RECENTS_KEY) ?? '[]')
    return Array.isArray(parsed) ? parsed.filter(valid).slice(0, RECENTS_MAX) : []
  } catch {
    return []
  }
}

function save(list: readonly Recent[]): void {
  try {
    localStorage.setItem(RECENTS_KEY, JSON.stringify(list))
  } catch {
    // Storage refused: the recents last for this visit only, and nothing is said.
  }
}

/** Put a symbol first (once), keep the last 20, and return what is now held. */
export function pushRecent(current: readonly Recent[], entry: Recent): Recent[] {
  const same = (r: Recent) => r.symbol === entry.symbol && r.exchange === entry.exchange
  const next = [entry, ...current.filter((r) => !same(r))].slice(0, RECENTS_MAX)
  save(next)
  return next
}

export function clearRecents(): void {
  try {
    localStorage.removeItem(RECENTS_KEY)
  } catch {
    // Nothing to remove from a storage that refuses.
  }
}
