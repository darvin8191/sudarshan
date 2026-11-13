import { useCallback, useEffect, useLayoutEffect, useRef } from 'react'
import { useLocation, useNavigate, useSearchParams } from 'react-router'
import type { AlertPrefill } from '@/sudarshan/alerts/AlertForm'
import type { FollowForm } from './followRows'
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
 * - `?sheet=alert` (M10.2): the form for a market alert on the instrument,
 *   which rides in the entry's state as the ticket's order does, with the
 *   price the form starts from.
 * - `?sheet=lists` (M8.5, v2 only): the All lists sheet. A sheet of v2 is
 *   known to the address only while v2 is on (`useOverlay(true)`), so with the
 *   switch off the page reads its address exactly as it did.
 * - `?sheet=view` (M8.7 F6, v2 only): the View sheet: sort and change basis.
 * - `?sheet=copy` (M8.8 F10, v2 only): the name dialog of Make a copy on a built-in list.
 * - `?sheet=import` and `?sheet=export` (M8.12 F15, v2 only): Import from and Export for TradingView.
 * - `?sheet=to-section&ci=<item>` and `?sheet=to-list&ci=<item>` or `&section=<id>`, with `&move=1`
 *   for a move (M8.4 F5, v2 only): the section picker and the list picker.
 * - `?sheet=follow-row&fr=<row id>` (M8.10 F11, v2 only): the sheet of a row that follows the price;
 *   `?sheet=follow` is its form (add, change or remove), which rides in the entry's state as the
 *   ticket's order does, so a form is never reopened by a reload.
 * - `?sheet=strikes` (M8.18 F14, v2 only): the strike picker of an underlying, which rides in the entry's
 *   state like the form, so a picker is never reopened by a reload.
 * - `?sheet=section&section=<id>` (M8.4 F3, v2 only): a section's menu; `?sheet=section-name`
 *   names a new section, or renames the one in `&section=<id>`; `?sheet=section-remove&section=<id>`
 *   asks before removing it.
 * - `?sheet=unsaved` (M8.4, review M4, v2 only): leaving edit mode or the list with moves not saved
 *   asks first; what the tap was for stays with the page, so a reload never reopens it.
 */
export type Overlay =
  | { kind: 'item'; itemId: number }
  | { kind: 'add' }
  | { kind: 'new' }
  | { kind: 'rename' }
  | { kind: 'delete' }
  | { kind: 'order'; order: OrderRequest | null }
  | { kind: 'alert'; alert: AlertPrefill | null }
  | { kind: 'lists' }
  | { kind: 'view' } // F6
  | { kind: 'copy' } // F10
  | { kind: 'import' } // F15
  | { kind: 'export' } // F15
  | { kind: 'section'; sectionId: number }
  | { kind: 'followRow'; rowId: number } // F11
  | { kind: 'follow'; form: FollowForm | null } // F11
  | { kind: 'strikes'; pick: StrikePick | null } // F14
  | { kind: 'sectionName'; sectionId: number | null }
  | { kind: 'sectionRemove'; sectionId: number }
  | { kind: 'pickSection'; item: number }
  | { kind: 'pickList'; item: number | null; section: number | null; move: boolean }
  | { kind: 'unsaved' } // review M4

export type OverlayRequest =
  | Exclude<
      Overlay,
      { kind: 'order' } | { kind: 'alert' } | { kind: 'follow' } | { kind: 'strikes' }
    >
  | { kind: 'order'; order: OrderRequest }
  | { kind: 'alert'; alert: AlertPrefill }
  | { kind: 'follow'; form: FollowForm }
  | { kind: 'strikes'; pick: StrikePick }

/** F14: the underlying the strike picker is for, and its own exchange. */
export interface StrikePick {
  underlying: string
  exchange: string
}

const SHEETS = new Set(['add', 'new', 'rename', 'delete', 'order', 'alert'])
/** The sheets only v2 opens; later v2 sheets are added here. */
const V2_SHEETS = new Set([
  'lists',
  'view', // F6
  'copy', // F10
  'import', // F15
  'export', // F15
  'follow-row', // F11
  'follow', // F11
  'strikes', // F14
  'section',
  'section-name',
  'section-remove',
  'to-section',
  'to-list',
  'unsaved', // review M4
])
const SECTION_KINDS: Record<string, 'section' | 'sectionName' | 'sectionRemove'> = {
  section: 'section',
  'section-name': 'sectionName',
  'section-remove': 'sectionRemove',
}
const SECTION_SHEET = {
  section: 'section',
  sectionName: 'section-name',
  sectionRemove: 'section-remove',
}

/** The history entry's state: whether this page pushed it, the ticket's order, the alert's instrument. */
interface EntryState {
  sdOverlay?: boolean
  order?: OrderRequest
  alert?: AlertPrefill
  follow?: FollowForm
  pick?: StrikePick
}

function entryState(state: unknown): EntryState {
  return state !== null && typeof state === 'object' ? (state as EntryState) : {}
}

export function overlayOf(params: URLSearchParams, state: unknown, v2 = false): Overlay | null {
  const item = Number(params.get('item'))
  if (params.has('item') && Number.isInteger(item) && item > 0)
    return { kind: 'item', itemId: item }
  const sheet = params.get('sheet')
  if (!sheet || !(SHEETS.has(sheet) || (v2 && V2_SHEETS.has(sheet)))) return null
  if (sheet === 'order') return { kind: 'order', order: entryState(state).order ?? null }
  if (sheet === 'alert') return { kind: 'alert', alert: entryState(state).alert ?? null }
  if (sheet === 'strikes') return { kind: 'strikes', pick: entryState(state).pick ?? null }
  if (sheet === 'follow') return { kind: 'follow', form: entryState(state).follow ?? null }
  if (sheet === 'follow-row') {
    const row = Number(params.get('fr'))
    return Number.isInteger(row) && row > 0 ? { kind: 'followRow', rowId: row } : null
  }
  const ci = Number(params.get('ci'))
  const pick = Number.isInteger(ci) && ci > 0 ? ci : null
  const sec = Number(params.get('section'))
  const section = Number.isInteger(sec) && sec > 0 ? sec : null
  if (sheet === 'to-section') return pick === null ? null : { kind: 'pickSection', item: pick }
  if (sheet === 'to-list') {
    if ((pick === null) === (section === null)) return null
    return { kind: 'pickList', item: pick, section, move: params.get('move') === '1' }
  }
  const kind = SECTION_KINDS[sheet]
  if (kind) {
    const id = Number(params.get('section'))
    const sectionId = params.has('section') && Number.isInteger(id) && id > 0 ? id : null
    if (kind !== 'sectionName' && sectionId === null) return null
    return { kind, sectionId } as Overlay
  }
  return { kind: sheet } as Overlay
}

/** The state an entry for `next` carries: whether this page pushed it, a ticket's order, an alert's. */
function stateFor(next: OverlayRequest, own: boolean): EntryState {
  return {
    sdOverlay: own,
    ...(next.kind === 'order' ? { order: next.order } : {}),
    ...(next.kind === 'alert' ? { alert: next.alert } : {}),
    ...(next.kind === 'follow' ? { follow: next.form } : {}),
    ...(next.kind === 'strikes' ? { pick: next.pick } : {}),
  }
}

function withOverlay(params: URLSearchParams, next: OverlayRequest | null): URLSearchParams {
  const out = new URLSearchParams(params)
  out.delete('item')
  out.delete('sheet')
  out.delete('section')
  out.delete('ci')
  out.delete('move')
  out.delete('fr')
  if (next?.kind === 'followRow') {
    out.set('sheet', 'follow-row')
    out.set('fr', String(next.rowId))
  } else if (next?.kind === 'pickSection') {
    out.set('sheet', 'to-section')
    out.set('ci', String(next.item))
  } else if (next?.kind === 'pickList') {
    out.set('sheet', 'to-list')
    if (next.item !== null) out.set('ci', String(next.item))
    if (next.section !== null) out.set('section', String(next.section))
    if (next.move) out.set('move', '1')
  } else if (next?.kind === 'item') out.set('item', String(next.itemId))
  else if (next && 'sectionId' in next) {
    out.set('sheet', SECTION_SHEET[next.kind])
    if (next.sectionId !== null) out.set('section', String(next.sectionId))
  } else if (next) out.set('sheet', next.kind)
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
export function useOverlay(v2 = false): {
  overlay: Overlay | null
  open: (next: OverlayRequest) => void
  swap: (next: OverlayRequest) => void
  close: () => void
} {
  const [params] = useSearchParams()
  const location = useLocation()
  const navigate = useNavigate()
  const overlay = overlayOf(params, location.state, v2)
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

  // A ticket or an alert form restored with the page (a reload, the phone
  // bringing the app back) is not reopened: each opens only from a tap.
  const restoredTicket = useRef(
    overlay?.kind === 'order' ||
      overlay?.kind === 'alert' ||
      overlay?.kind === 'follow' ||
      overlay?.kind === 'strikes'
  )
  useEffect(() => {
    if (!restoredTicket.current) return
    restoredTicket.current = false
    close()
  }, [close])

  return { overlay, open, swap, close }
}
