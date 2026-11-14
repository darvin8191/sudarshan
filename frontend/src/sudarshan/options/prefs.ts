/**
 * What the options screen remembers on this phone (decision 8): the last underlying and expiry
 * opened. Browser storage only, read defensively: a private window or blocked storage gives the
 * default, never an error.
 */
export interface LastChain {
  exchange: 'NFO' | 'BFO'
  underlying: string
  expiry: string | null
}

/** What a bare /options opens when nothing was opened on this phone before. */
export const DEFAULT_CHAIN: LastChain = { exchange: 'NFO', underlying: 'NIFTY', expiry: null }

const KEY = 'sudarshan-options-last'

type Store = Pick<Storage, 'getItem' | 'setItem'>

function store(): Store | null {
  try {
    return globalThis.localStorage ?? null
  } catch {
    return null
  }
}

export function readLastChain(storage: Store | null = store()): LastChain {
  try {
    const saved = JSON.parse(storage?.getItem(KEY) ?? 'null') as Partial<LastChain> | null
    const exchange = saved?.exchange
    const underlying = saved?.underlying
    if ((exchange !== 'NFO' && exchange !== 'BFO') || typeof underlying !== 'string')
      return DEFAULT_CHAIN
    if (!/^[A-Z0-9&_-]{1,40}$/.test(underlying)) return DEFAULT_CHAIN
    const expiry =
      typeof saved?.expiry === 'string' && /^\d{4}-\d{2}-\d{2}$/.test(saved.expiry)
        ? saved.expiry
        : null
    return { exchange, underlying, expiry }
  } catch {
    return DEFAULT_CHAIN
  }
}

export function saveLastChain(last: LastChain, storage: Store | null = store()): void {
  try {
    storage?.setItem(KEY, JSON.stringify(last))
  } catch {
    /* kept for this visit only */
  }
}
