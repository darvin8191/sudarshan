import { useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react'
import { useLocation, useNavigate, useSearchParams } from 'react-router'

/**
 * What a phone book has open over its cards, kept in the address so the
 * phone's Back closes it before it leaves the page, as in a broker's app (M6.9).
 * The rules are the watchlist's (`sudarshan/watchlist/useOverlay`):
 *
 * - `?row=<id>`: the sheet of one card (a position, an order, a holding);
 * - `?act=<what>&row=<id>`: a step taken from that sheet (the exit or cancel
 *   confirmation, the order ticket, the modify dialog);
 * - `?act=exitall` or `?act=cancelall`: the confirmation for the whole book.
 *
 * Opening pushes a history entry marked as this page's own; closing it (the
 * close button, a tap outside, Escape, Cancel) goes back, so Back and the close
 * button leave the same history behind. One the page did not open itself (a
 * link, a reload) is closed by replacing the entry, so closing never leaves
 * the app. A step taken from a sheet replaces the sheet's entry and keeps its
 * mark, so Back from it returns to the cards, not to the sheet.
 *
 * A sheet may open from the address (a reload keeps it). A step never does: a
 * confirmation, a ticket or a dialog opens only from a tap on this page, and
 * one met any other way (a reload, Forward to an entry already closed) is
 * closed at once rather than shown.
 */
export type BookAct =
  | 'exit'
  | 'exitall'
  | 'add'
  | 'modify'
  | 'cancel'
  | 'cancelall'
  | 'buy'
  | 'sell'

export type BookOverlay = { act: 'sheet'; row: string } | { act: BookAct; row: string | null }

const ACTS = new Set<string>([
  'exit',
  'exitall',
  'add',
  'modify',
  'cancel',
  'cancelall',
  'buy',
  'sell',
])

/** The history entry's state: whether this page pushed it, and on which visit. */
interface EntryState {
  sdOverlay?: boolean
  sdVisit?: string
}

function entryState(state: unknown): EntryState {
  return state !== null && typeof state === 'object' ? (state as EntryState) : {}
}

export function bookOverlayOf(params: URLSearchParams): BookOverlay | null {
  const act = params.get('act')
  const row = params.get('row')
  if (act !== null) return ACTS.has(act) ? { act: act as BookAct, row } : null
  return row ? { act: 'sheet', row } : null
}

function withOverlay(params: URLSearchParams, next: BookOverlay | null): URLSearchParams {
  const out = new URLSearchParams(params)
  out.delete('act')
  out.delete('row')
  if (next && next.act !== 'sheet') out.set('act', next.act)
  if (next?.row) out.set('row', next.row)
  return out
}

let visits = 0

/** A name for one visit to a page, for the entries it pushes. */
function nextVisit(): string {
  visits += 1
  return `v${visits}`
}

/**
 * The open overlay, and how to open, swap and close one. `open`, `swap` and
 * `close` keep their identity while overlays come and go, so the cards that are
 * handed them are not redrawn each time one does.
 */
export function useBookOverlay(): {
  overlay: BookOverlay | null
  open: (next: BookOverlay) => void
  swap: (next: BookOverlay) => void
  close: () => void
} {
  const [params] = useSearchParams()
  const location = useLocation()
  const navigate = useNavigate()
  const [visit] = useState(nextVisit)
  const closed = useRef(new Set<string>())

  const raw = bookOverlayOf(params)
  const state = entryState(location.state)
  const pushed = state.sdOverlay === true
  // A step shows only on the entry this visit made for it, and only until it is closed.
  const foreign =
    raw !== null &&
    raw.act !== 'sheet' &&
    (state.sdVisit !== visit || closed.current.has(location.key))
  const overlay = foreign ? null : raw

  // Where the page stands, for the handlers below; set after each render, so
  // a tap (which comes after it) always reads the address it was made on.
  const here = useRef({ params, raw, pushed, pathname: location.pathname, key: location.key })
  useLayoutEffect(() => {
    here.current = { params, raw, pushed, pathname: location.pathname, key: location.key }
  })

  const go = useCallback(
    (next: BookOverlay | null, replace: boolean, own: boolean) => {
      const { params: current, pathname } = here.current
      const search = withOverlay(current, next).toString()
      navigate(
        { pathname, search: search ? `?${search}` : '' },
        { replace, state: next ? { sdOverlay: own, sdVisit: visit } : null }
      )
    },
    [navigate, visit]
  )

  const open = useCallback(
    (next: BookOverlay) => {
      // One overlay at a time: opening over another takes its place.
      const { raw: shown, pushed: own } = here.current
      const replace = shown !== null
      go(next, replace, replace ? own : true)
    },
    [go]
  )

  const swap = useCallback(
    (next: BookOverlay) => {
      const { raw: shown, pushed: own } = here.current
      // Nothing to swap: a step opened straight from a card is opened.
      if (shown === null) go(next, false, true)
      else go(next, true, own)
    },
    [go]
  )

  const close = useCallback(() => {
    const { raw: shown, pushed: own, key } = here.current
    if (shown === null || closed.current.has(key)) return
    closed.current.add(key)
    if (own) navigate(-1)
    else go(null, true, false)
  }, [go, navigate])

  // A step met without a tap (a reload, Forward) is taken out of the address in place.
  const cleaned = useRef<string | null>(null)
  useEffect(() => {
    if (!foreign || cleaned.current === location.key) return
    cleaned.current = location.key
    go(null, true, false)
  }, [foreign, location.key, go])

  return { overlay, open, swap, close }
}

/**
 * Keep a dialog the page itself owns (the order book's modify dialog, the
 * holdings' order ticket) in step with its entry in the address: Back closes
 * the dialog, and the dialog closing on its own (sent, cancelled) takes its
 * entry away. The router moves the address in a transition, so the page's
 * dialog can open a render before its entry arrives; `opening`, called just
 * before the dialog is opened from here, keeps that moment from reading as a
 * Back.
 */
export function usePageDialog({
  entered,
  open,
  setOpen,
  close,
}: {
  /** The address has the dialog's entry. */
  entered: boolean
  /** The page's dialog is open. */
  open: boolean
  setOpen: (open: boolean) => void
  close: () => void
}): () => void {
  const awaiting = useRef(false)
  const seen = useRef(false)
  useEffect(() => {
    if (entered) {
      awaiting.current = false
      if (open) {
        seen.current = true
      } else if (seen.current) {
        seen.current = false
        close()
      }
    } else if (open && !awaiting.current) {
      seen.current = false
      setOpen(false)
    }
  }, [entered, open, setOpen, close])
  return useCallback(() => {
    awaiting.current = true
  }, [])
}
