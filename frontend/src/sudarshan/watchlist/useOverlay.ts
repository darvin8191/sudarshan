import { useCallback, useEffect, useLayoutEffect, useRef } from 'react'
import { useLocation, useNavigate, useSearchParams } from 'react-router'
import type { OrderRequest } from './SymbolSheet'

/**
 * What the watchlist has open over the list, kept in the address so the phone's
 * Back closes it instead of leaving the page, as in a broker's app:
 *
 * - `?item=<id>`: the instrument's sheet;
 * - `?sheet=add`, `new`, `rename` or `delete`: the search, a list's name, or
 *   deleting it;
 * - `?sheet=order`: the order ticket. What it is for (the instrument, Buy or
 *   Sell, the lot size) rides in the history entry's state, not the address:
 *   a ticket only ever opens from a tap.
 */
export type Overlay =
  | { kind: 'item'; itemId: number }
  | { kind: 'add' }
  | { kind: 'new' }
  | { kind: 'rename' }
  | { kind: 'delete' }
  | { kind: 'order'; order: OrderRequest | null }

export type OverlayRequest =
  | Exclude<Overlay, { kind: 'order' }>
  | { kind: 'order'; order: OrderRequest }

const SHEETS = new Set(['add', 'new', 'rename', 'delete', 'order'])

/** The history entry's state: whether this page pushed it, and the ticket's order. */
interface EntryState {
  sdOverlay?: boolean
  order?: OrderRequest
}

function entryState(state: unknown): EntryState {
  return state !== null && typeof state === 'object' ? (state as EntryState) : {}
}

export function overlayOf(params: URLSearchParams, state: unknown): Overlay | null {
  const item = Number(params.get('item'))
  if (params.has('item') && Number.isInteger(item) && item > 0)
    return { kind: 'item', itemId: item }
  const sheet = params.get('sheet')
  if (!sheet || !SHEETS.has(sheet)) return null
  if (sheet === 'order') return { kind: 'order', order: entryState(state).order ?? null }
  return { kind: sheet } as Overlay
}

/** The state an entry for `next` carries: whether this page pushed it, and a ticket's order. */
function stateFor(next: OverlayRequest, own: boolean): EntryState {
  return { sdOverlay: own, ...(next.kind === 'order' ? { order: next.order } : {}) }
}

function withOverlay(params: URLSearchParams, next: OverlayRequest | null): URLSearchParams {
  const out = new URLSearchParams(params)
  out.delete('item')
  out.delete('sheet')
  if (next?.kind === 'item') out.set('item', String(next.itemId))
  else if (next) out.set('sheet', next.kind)
  return out
}

/**
 * The open overlay, and how to open, swap and close one.
 *
 * Opening pushes a history entry marked as this page's own; closing it (the
 * close button, a tap outside, Escape, Cancel) goes back, so Back and the close
 * button leave the same history behind. An overlay the page did not open
 * itself (a link, a restored session) is closed by replacing the entry
 * instead, so closing never leaves the app. Swapping one for another (the
 * sheet's Buy opening the ticket) replaces the entry and keeps its mark, so
 * Back from the ticket returns to the list, not to the sheet. Closing twice
 * for one entry goes back once.
 *
 * `open`, `swap` and `close` keep their identity while overlays come and go,
 * so the rows that are handed them are not redrawn each time one does.
 */
export function useOverlay(): {
  overlay: Overlay | null
  open: (next: OverlayRequest) => void
  swap: (next: OverlayRequest) => void
  close: () => void
} {
  const [params] = useSearchParams()
  const location = useLocation()
  const navigate = useNavigate()
  const overlay = overlayOf(params, location.state)
  const pushed = entryState(location.state).sdOverlay === true

  // Where the page stands, for the handlers below; set after each render, so
  // a tap (which comes after it) always reads the address it was made on.
  const here = useRef({ params, overlay, pushed, pathname: location.pathname, key: location.key })
  useLayoutEffect(() => {
    here.current = { params, overlay, pushed, pathname: location.pathname, key: location.key }
  })
  const closedFor = useRef<string | null>(null)

  const go = useCallback(
    (next: OverlayRequest | null, replace: boolean, state: EntryState | null) => {
      const { params: current, pathname } = here.current
      const search = withOverlay(current, next).toString()
      navigate({ pathname, search: search ? `?${search}` : '' }, { replace, state })
    },
    [navigate]
  )

  const open = useCallback(
    (next: OverlayRequest) => {
      // One overlay at a time: opening over another takes its place.
      const { overlay: shown, pushed: own } = here.current
      const replace = shown !== null
      go(next, replace, stateFor(next, replace ? own : true))
    },
    [go]
  )

  const swap = useCallback(
    (next: OverlayRequest) => go(next, true, stateFor(next, here.current.pushed)),
    [go]
  )

  const close = useCallback(() => {
    const { overlay: shown, pushed: own, key } = here.current
    if (shown === null || closedFor.current === key) return
    closedFor.current = key
    if (own) navigate(-1)
    else go(null, true, null)
  }, [go, navigate])

  // A ticket restored with the page (a reload, the phone bringing the app
  // back) is not reopened: a ticket opens only from a tap.
  const restoredTicket = useRef(overlay?.kind === 'order')
  useEffect(() => {
    if (!restoredTicket.current) return
    restoredTicket.current = false
    close()
  }, [close])

  return { overlay, open, swap, close }
}
