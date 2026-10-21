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
 * one met any other way (a reload, Forward to an entry the page has left) is
 * closed at once rather than shown. An entry this page pushed is closed by
 * going back to the cards it was pushed over, so the next Back leaves the
 * page: replacing it with the cards would leave two entries of cards, and a
 * Back that changes nothing. Every Back closes something or leaves.
 *
 * One exception: a dialog of the page's own still open for the step (the
 * phone was turned with the modify dialog or the order ticket open, and the
 * page kept it with what was typed into it) takes the entry as this visit's,
 * so Back closes that dialog.
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

/**
 * The history entry's state: whether this page pushed it, on which visit, and
 * the entry's own number within that visit.
 */
interface EntryState {
  sdOverlay?: boolean
  sdVisit?: string
  sdEntry?: number
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

function isStep(overlay: BookOverlay | null): boolean {
  return overlay !== null && overlay.act !== 'sheet'
}

/**
 * This page load's own name. History entries outlive a reload (Android Chrome
 * reloads a tab it has discarded) while the visit counter below starts again
 * from one, so without it a step pushed before the reload would read as this
 * visit's own and open by itself.
 */
function loadName(): string {
  let name = ''
  try {
    const bytes = new Uint32Array(2)
    globalThis.crypto.getRandomValues(bytes)
    name = Array.from(bytes, (n) => n.toString(36)).join('')
  } catch {
    // No Web Crypto: the clock and Math.random still differ from one load to the next.
  }
  return `${name}${Date.now().toString(36)}${Math.random().toString(36).slice(2, 8)}`
}

const LOAD = loadName()
let visits = 0

/** A name for one visit to a page, for the entries it pushes. */
function nextVisit(): string {
  visits += 1
  return `${LOAD}.${visits}`
}

/** How long a Back sent by `close` may take to land before a tap stops waiting for it. */
const BACK_WAIT_MS = 1000

/** The overlay as this page last set it, ahead of the router (see `useBookOverlay`). */
interface Standing {
  params: URLSearchParams
  pathname: string
  raw: BookOverlay | null
  pushed: boolean
  /** The entry's number when this visit made it, else null. */
  entry: number | null
  /** When `close` went back, until the address lands; 0 when no Back is under way. */
  backSince: number
}

/**
 * The open overlay, and how to open, swap and close one. `open`, `swap` and
 * `close` keep their identity while overlays come and go, so the cards that are
 * handed them are not redrawn each time one does.
 *
 * The router moves the address at once but lands it in a transition, a render
 * or more after the tap, so the handlers work from the overlay as this page
 * last set it rather than from the address it last rendered: a double tap
 * pushes one entry, a second close in the same moment goes back once, and a
 * tap made while a close is still going back waits for it to land.
 */
export function useBookOverlay(
  options: {
    /** A dialog of the page's own (modify, the order ticket), open or not, and its steps. */
    dialog?: { open: boolean; acts: readonly BookAct[] }
  } = {}
): {
  overlay: BookOverlay | null
  open: (next: BookOverlay) => void
  swap: (next: BookOverlay) => void
  close: () => void
} {
  const [params] = useSearchParams()
  const location = useLocation()
  const navigate = useNavigate()
  const [visit] = useState(nextVisit)
  const entries = useRef(0)
  // The step entries this visit has left (closed, Back, swapped away): none shows again.
  const left = useRef(new Set<number>())

  const raw = bookOverlayOf(params)
  const state = entryState(location.state)
  const pushed = state.sdOverlay === true
  const mine = state.sdVisit === visit && typeof state.sdEntry === 'number'
  const entry = mine ? (state.sdEntry as number) : null
  // A step shows only on an entry this visit made for it, and only until the page leaves it.
  const foreign = isStep(raw) && (entry === null || left.current.has(entry))
  const overlay = foreign ? null : raw
  // A step of another visit that the page's own dialog still shows: this visit's now.
  const adopt =
    foreign &&
    entry === null &&
    raw !== null &&
    options.dialog?.open === true &&
    options.dialog.acts.includes(raw.act as BookAct)

  const standing = useRef<Standing>({
    params,
    pathname: location.pathname,
    raw,
    pushed,
    entry,
    backSince: 0,
  })
  const landed = useRef(location.key)
  const onStep = useRef<number | null>(isStep(overlay) ? entry : null)
  const waiting = useRef<(() => void) | null>(null)

  const go = useCallback(
    (next: BookOverlay | null, replace: boolean, own: boolean) => {
      const { params: current, pathname } = standing.current
      const after = withOverlay(current, next)
      const search = after.toString()
      entries.current += 1
      const number = next ? entries.current : null
      standing.current = {
        params: after,
        pathname,
        raw: next,
        pushed: next ? own : false,
        entry: number,
        backSince: 0,
      }
      navigate(
        { pathname, search: search ? `?${search}` : '' },
        { replace, state: next ? { sdOverlay: own, sdVisit: visit, sdEntry: number } : null }
      )
    },
    [navigate, visit]
  )

  /** Run `act` now, or once a Back that `close` sent has landed. */
  const whenSettled = useCallback((act: () => void) => {
    const { backSince } = standing.current
    if (backSince !== 0 && Date.now() - backSince < BACK_WAIT_MS) {
      waiting.current = act
    } else {
      waiting.current = null
      act()
    }
  }, [])

  const open = useCallback(
    (next: BookOverlay) =>
      whenSettled(() => {
        // One overlay at a time: opening over another takes its place.
        const { raw: shown, pushed: own } = standing.current
        const replace = shown !== null
        go(next, replace, replace ? own : true)
      }),
    [go, whenSettled]
  )

  const swap = useCallback(
    (next: BookOverlay) =>
      whenSettled(() => {
        const { raw: shown, pushed: own } = standing.current
        // Nothing to swap: a step opened straight from a card is opened.
        if (shown === null) go(next, false, true)
        else go(next, true, own)
      }),
    [go, whenSettled]
  )

  const close = useCallback(() => {
    const { raw: shown, pushed: own, entry: number } = standing.current
    // Closed already, by this tap or the one before it.
    if (shown === null) return
    if (isStep(shown) && number !== null) left.current.add(number)
    if (own) {
      standing.current = {
        ...standing.current,
        raw: null,
        pushed: false,
        entry: null,
        backSince: Date.now(),
      }
      navigate(-1)
    } else {
      go(null, true, false)
    }
  }, [go, navigate])

  // Each time the address lands somewhere new: take it as it is, remember a
  // step entry the page has just left, and run a tap that waited for a Back.
  useLayoutEffect(() => {
    if (landed.current === location.key) return
    landed.current = location.key
    standing.current = { params, pathname: location.pathname, raw, pushed, entry, backSince: 0 }
    if (onStep.current !== null) left.current.add(onStep.current)
    onStep.current = isStep(overlay) ? entry : null
    const next = waiting.current
    waiting.current = null
    next?.()
  })

  // A step met without a tap (a reload, Forward) is taken out of the address:
  // gone back from when this page pushed it, else replaced in place. Not one
  // this page is already taking out (a close under way), and back once only:
  // a second dead entry straight after is replaced, so nothing can loop.
  const cleaned = useRef<string | null>(null)
  const wentBack = useRef(0)
  useEffect(() => {
    if (!foreign || cleaned.current === location.key || standing.current.raw === null) return
    cleaned.current = location.key
    if (adopt) {
      go(standing.current.raw, true, pushed)
      return
    }
    const first = (window.history.state as { idx?: unknown } | null)?.idx === 0
    if (pushed && !first && Date.now() - wentBack.current > BACK_WAIT_MS) {
      wentBack.current = Date.now()
      standing.current = {
        ...standing.current,
        raw: null,
        pushed: false,
        entry: null,
        backSince: Date.now(),
      }
      navigate(-1)
      return
    }
    go(null, true, false)
  }, [foreign, adopt, pushed, location.key, go, navigate])

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
 *
 * A dialog already open when this mounts was opened by the page's other
 * layout (the phone turned while it was open, and the page keeps the dialog in
 * one place for both): it is left open, with what was typed into it, and is
 * the page's to close.
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
  const inherited = useRef(open)
  useEffect(() => {
    if (!open) inherited.current = false
    if (entered) {
      awaiting.current = false
      // An entry of its own now (taken over after a turn): Back closes it.
      inherited.current = false
      if (open) {
        seen.current = true
      } else if (seen.current) {
        seen.current = false
        close()
      }
    } else if (open && !awaiting.current && !inherited.current) {
      seen.current = false
      setOpen(false)
    }
  }, [entered, open, setOpen, close])
  return useCallback(() => {
    awaiting.current = true
  }, [])
}
