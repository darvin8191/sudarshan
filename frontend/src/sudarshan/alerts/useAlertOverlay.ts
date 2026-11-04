import { useCallback, useLayoutEffect, useRef } from 'react'
import { useLocation, useNavigate, useSearchParams } from 'react-router'

/**
 * What the Alerts page has open over its cards, kept in the address so the phone's Back closes
 * it before it leaves the page, by the watchlist's rules (watchlist/useOverlay.ts):
 *
 * - `?alert=<id>`: an alert's details sheet (a toast's View opens it);
 * - `?sheet=new`: the form for a new alert; `?sheet=edit&alert=<id>`: the form for that alert;
 * - `?sheet=settings`: where alerts are sent, and the test.
 *
 * Opening pushes a history entry marked as this page's own, and closing it goes back, so Back and
 * the close button leave the same history behind; one the page did not open (a link, a reload) is
 * closed by replacing the entry, so closing never leaves the app. Swapping one for another (the
 * sheet's Edit opening the form) replaces the entry and keeps its mark: Back from the form
 * returns to the cards. Closing twice for one entry goes back once.
 *
 * The tab (`?tab=stopped`, `?tab=history`) lives in the address too, so a reload keeps it, but a
 * move between tabs replaces the entry: Back never walks back through tabs.
 */
export type AlertOverlay =
  | { kind: 'alert'; id: number }
  | { kind: 'new' }
  | { kind: 'edit'; id: number }
  | { kind: 'settings' }

export type AlertTab = 'active' | 'stopped' | 'history'

function idOf(params: URLSearchParams): number | null {
  const id = Number(params.get('alert'))
  return params.has('alert') && Number.isInteger(id) && id > 0 ? id : null
}

export function alertOverlayOf(params: URLSearchParams): AlertOverlay | null {
  const sheet = params.get('sheet')
  const id = idOf(params)
  if (sheet === 'new' || sheet === 'settings') return { kind: sheet }
  if (sheet === 'edit') return id === null ? null : { kind: 'edit', id }
  return sheet === null && id !== null ? { kind: 'alert', id } : null
}

export function alertTabOf(params: URLSearchParams): AlertTab {
  const tab = params.get('tab')
  return tab === 'stopped' || tab === 'history' ? tab : 'active'
}

function withOverlay(params: URLSearchParams, next: AlertOverlay | null): URLSearchParams {
  const out = new URLSearchParams(params)
  out.delete('alert')
  out.delete('sheet')
  if (next?.kind === 'alert' || next?.kind === 'edit') out.set('alert', String(next.id))
  if (next && next.kind !== 'alert') out.set('sheet', next.kind)
  return out
}

export function useAlertOverlay(): {
  overlay: AlertOverlay | null
  tab: AlertTab
  /** Open one, or swap it for the one open, keeping that one's mark. */
  open: (next: AlertOverlay) => void
  close: () => void
  setTab: (tab: AlertTab) => void
} {
  const [params] = useSearchParams()
  const location = useLocation()
  const navigate = useNavigate()
  const overlay = alertOverlayOf(params)
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
    (next: AlertOverlay | null, replace: boolean, own: boolean) => {
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
    (next: AlertOverlay) => {
      // One overlay at a time: opening over another takes its place, and its mark.
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

  const setTab = useCallback(
    (tab: AlertTab) => {
      const { params: current, pathname, overlay: shown, pushed: own } = here.current
      const after = new URLSearchParams(current)
      if (tab === 'active') after.delete('tab')
      else after.set('tab', tab)
      here.current = { ...here.current, params: after }
      const search = after.toString()
      navigate(
        { pathname, search: search ? `?${search}` : '' },
        { replace: true, state: shown ? { sdOverlay: own } : null }
      )
    },
    [navigate]
  )

  return { overlay, tab: alertTabOf(params), open, close, setTab }
}
