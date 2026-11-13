import { useCallback, useEffect, useLayoutEffect, useRef } from 'react'
import { useLocation, useNavigate, useSearchParams } from 'react-router'
import type { Mode } from './types'

/**
 * What the Journal page has open, kept in the address so the phone's Back closes a sheet before
 * it leaves the page, by the watchlist's rules (watchlist/useOverlay.ts). No child paths:
 *
 * - `?view=trades` (the default), `analytics` or `playbooks`: the part shown;
 * - `?mode=sandbox`: Sandbox trades; none is Live. A move between parts or modes replaces the
 *   entry, so Back never walks back through them;
 * - `?trade=<id>`: the trade's sheet;
 * - `?sheet=playbook` with `&playbook=<id>` to change one, or `?sheet=new-playbook`: the playbook sheet;
 * - `?sheet=filters`: the analytics filters.
 *
 * Opening pushes a history entry marked as this page's own and closing goes back, so Back and the
 * close button leave the same history behind. A sheet the page did not open (a link, a reload)
 * is closed by replacing the entry, so closing never leaves the app. A reload never reopens a
 * form: the playbook sheet and the filters are closed when the page finds one open on arrival,
 * as the watchlist does with its ticket. The trade's sheet is a read of a trade, with its notes
 * to edit, and the address alone names it, so a link to a trade (from a report) still opens it.
 * Opening over another sheet takes its place and its mark. Closing twice for one entry goes
 * back once.
 */
export const VIEWS = ['trades', 'analytics', 'playbooks'] as const
export type JournalView = (typeof VIEWS)[number]

export type JournalOverlay =
  | { kind: 'trade'; tradeId: number }
  | { kind: 'playbook'; playbookId: number | null }
  | { kind: 'filters' }

function idOf(params: URLSearchParams, name: string): number | null {
  const raw = params.get(name)
  if (raw === null || !/^[0-9]{1,9}$/.test(raw)) return null
  const id = Number(raw)
  return id > 0 ? id : null
}

export function viewOf(params: URLSearchParams): JournalView {
  const view = params.get('view')
  return (VIEWS as readonly string[]).includes(view ?? '') ? (view as JournalView) : 'trades'
}

export function modeOf(params: URLSearchParams): Mode {
  return params.get('mode') === 'sandbox' ? 'sandbox' : 'live'
}

export function journalOverlayOf(params: URLSearchParams): JournalOverlay | null {
  const sheet = params.get('sheet')
  if (sheet === null) {
    const trade = idOf(params, 'trade')
    return trade === null ? null : { kind: 'trade', tradeId: trade }
  }
  if (sheet === 'filters') return { kind: 'filters' }
  if (sheet === 'new-playbook') return { kind: 'playbook', playbookId: null }
  if (sheet === 'playbook') {
    const id = idOf(params, 'playbook')
    return id === null ? null : { kind: 'playbook', playbookId: id }
  }
  return null
}

function withOverlay(params: URLSearchParams, next: JournalOverlay | null): URLSearchParams {
  const out = new URLSearchParams(params)
  out.delete('trade')
  out.delete('sheet')
  out.delete('playbook')
  if (!next) return out
  if (next.kind === 'trade') out.set('trade', String(next.tradeId))
  else if (next.kind === 'filters') out.set('sheet', 'filters')
  else if (next.playbookId === null) out.set('sheet', 'new-playbook')
  else {
    out.set('sheet', 'playbook')
    out.set('playbook', String(next.playbookId))
  }
  return out
}

export function useJournalOverlay(): {
  overlay: JournalOverlay | null
  view: JournalView
  mode: Mode
  /** Open one, or swap it for the one open, keeping that one's mark. */
  open: (next: JournalOverlay) => void
  close: () => void
  setView: (view: JournalView) => void
  setMode: (mode: Mode) => void
} {
  const [params] = useSearchParams()
  const location = useLocation()
  const navigate = useNavigate()
  const overlay = journalOverlayOf(params)
  const pushed = (location.state as { sdOverlay?: unknown } | null)?.sdOverlay === true

  // Where the page stands, for the handlers below: set after each render and as each handler
  // moves the address, so a double tap pushes one entry and a second close goes back once.
  const here = useRef({ params, overlay, pushed, pathname: location.pathname })
  const landed = useRef(location.key)
  useLayoutEffect(() => {
    if (landed.current === location.key) return
    landed.current = location.key
    here.current = { params, overlay, pushed, pathname: location.pathname }
  })

  const go = useCallback(
    (next: JournalOverlay | null, replace: boolean, own: boolean) => {
      const { params: current, pathname } = here.current
      const after = withOverlay(current, next)
      here.current = { params: after, overlay: next, pushed: next ? own : false, pathname }
      const search = after.toString()
      navigate(
        { pathname, search: search ? `?${search}` : '' },
        { replace, state: next ? { sdOverlay: own } : null }
      )
    },
    [navigate]
  )

  const open = useCallback(
    (next: JournalOverlay) => {
      const { overlay: shown, pushed: own } = here.current
      go(next, shown !== null, shown !== null ? own : true)
    },
    [go]
  )

  const close = useCallback(() => {
    const { overlay: shown, pushed: own, params: current, pathname } = here.current
    if (shown === null) return
    if (own) {
      here.current = { params: withOverlay(current, null), overlay: null, pushed: false, pathname }
      navigate(-1)
    } else {
      go(null, true, false)
    }
  }, [go, navigate])

  const move = useCallback(
    (change: (after: URLSearchParams) => void) => {
      const { params: current, pathname, overlay: shown, pushed: own } = here.current
      const after = new URLSearchParams(current)
      change(after)
      here.current = { ...here.current, params: after }
      const search = after.toString()
      navigate(
        { pathname, search: search ? `?${search}` : '' },
        { replace: true, state: shown ? { sdOverlay: own } : null }
      )
    },
    [navigate]
  )

  const setView = useCallback(
    (next: JournalView) =>
      move((after) => {
        if (next === 'trades') after.delete('view')
        else after.set('view', next)
      }),
    [move]
  )

  const setMode = useCallback(
    (next: Mode) =>
      move((after) => {
        if (next === 'live') after.delete('mode')
        else after.set('mode', next)
      }),
    [move]
  )

  // A form restored with the page (a reload, the phone bringing the app back) is not reopened.
  const restored = useRef(overlay !== null && overlay.kind !== 'trade')
  useEffect(() => {
    if (!restored.current) return
    restored.current = false
    go(null, true, false)
  }, [go])

  return { overlay, view: viewOf(params), mode: modeOf(params), open, close, setView, setMode }
}
