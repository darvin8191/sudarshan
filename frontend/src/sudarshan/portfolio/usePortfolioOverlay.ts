import { useCallback, useLayoutEffect, useRef } from 'react'
import { useLocation, useNavigate, useSearchParams } from 'react-router'

/**
 * What the Portfolio page has open, kept in the address so the phone's Back closes a sheet
 * before it leaves the page, by the watchlist's rules (watchlist/useOverlay.ts):
 *
 * - `?tab=<id>`: the custom tab shown; none is the Upstox tab;
 * - `?view=`: the part of the tab shown. The Upstox tab has holdings, positions, funds and
 *   history; a custom tab has overview, holdings, activity and performance;
 * - `?sheet=new-tab`, `settings`, `add`, `import`, `customize`: a sheet over the tab;
 * - `?sheet=edit&activity=<id>`: the entry's form; `?activity=<id>` alone: the entry's details;
 * - `?sheet=price&asset=<id>`: a manual asset's value; `?asset=<id>` alone: the asset's details.
 *
 * Opening pushes a history entry marked as this page's own, and closing goes back, so Back and
 * the close button leave the same history behind. One the page did not open (a link, a reload)
 * is closed by replacing the entry, so closing never leaves the app. Swapping one sheet for
 * another replaces the entry and keeps its mark. Closing twice for one entry goes back once.
 *
 * The tab and the view live in the address too, so a reload keeps them, but a move between
 * them replaces the entry: Back never walks back through tabs.
 */
export const SHEETS = ['new-tab', 'settings', 'add', 'import', 'customize'] as const
export type SheetName = (typeof SHEETS)[number]

export type PortfolioOverlay =
  | { kind: SheetName }
  | { kind: 'edit'; activityId: number }
  | { kind: 'activity'; activityId: number }
  | { kind: 'price'; assetId: number }
  | { kind: 'asset'; assetId: number }

export const UPSTOX_VIEWS = ['holdings', 'positions', 'funds', 'history'] as const
export const CUSTOM_VIEWS = ['overview', 'holdings', 'activity', 'performance'] as const
export type UpstoxView = (typeof UPSTOX_VIEWS)[number]
export type CustomView = (typeof CUSTOM_VIEWS)[number]

function idOf(params: URLSearchParams, name: string): number | null {
  const raw = params.get(name)
  if (raw === null || !/^[0-9]{1,9}$/.test(raw)) return null
  const id = Number(raw)
  return id > 0 ? id : null
}

/** The custom tab in the address; null is the Upstox tab. */
export function tabOf(params: URLSearchParams): number | null {
  return idOf(params, 'tab')
}

/** The view in the address for the tab shown; the first of its views when it names none of them. */
export function viewOf(params: URLSearchParams, tab: number | null): UpstoxView | CustomView {
  const view = params.get('view')
  const views: readonly string[] = tab === null ? UPSTOX_VIEWS : CUSTOM_VIEWS
  return (views.includes(view ?? '') ? view : views[0]) as UpstoxView | CustomView
}

export function portfolioOverlayOf(params: URLSearchParams): PortfolioOverlay | null {
  const sheet = params.get('sheet')
  const activity = idOf(params, 'activity')
  const asset = idOf(params, 'asset')
  if (sheet === null) {
    if (activity !== null) return { kind: 'activity', activityId: activity }
    if (asset !== null) return { kind: 'asset', assetId: asset }
    return null
  }
  if ((SHEETS as readonly string[]).includes(sheet)) return { kind: sheet as SheetName }
  if (sheet === 'edit') return activity === null ? null : { kind: 'edit', activityId: activity }
  if (sheet === 'price') return asset === null ? null : { kind: 'price', assetId: asset }
  return null
}

function withOverlay(params: URLSearchParams, next: PortfolioOverlay | null): URLSearchParams {
  const out = new URLSearchParams(params)
  out.delete('sheet')
  out.delete('activity')
  out.delete('asset')
  if (!next) return out
  if (next.kind === 'activity') out.set('activity', String(next.activityId))
  else if (next.kind === 'asset') out.set('asset', String(next.assetId))
  else {
    out.set('sheet', next.kind)
    if (next.kind === 'edit') out.set('activity', String(next.activityId))
    if (next.kind === 'price') out.set('asset', String(next.assetId))
  }
  return out
}

export function usePortfolioOverlay(): {
  overlay: PortfolioOverlay | null
  tab: number | null
  view: UpstoxView | CustomView
  /** Open one, or swap it for the one open, keeping that one's mark. */
  open: (next: PortfolioOverlay) => void
  close: () => void
  /** Show a tab (null is Upstox); its view starts at the first. Replaces the entry. */
  selectTab: (tab: number | null) => void
  setView: (view: UpstoxView | CustomView) => void
} {
  const [params] = useSearchParams()
  const location = useLocation()
  const navigate = useNavigate()
  const overlay = portfolioOverlayOf(params)
  const pushed = (location.state as { sdOverlay?: unknown } | null)?.sdOverlay === true
  const tab = tabOf(params)

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
    (next: PortfolioOverlay | null, replace: boolean, own: boolean) => {
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
    (next: PortfolioOverlay) => {
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

  const selectTab = useCallback(
    (next: number | null) =>
      move((after) => {
        after.delete('view')
        if (next === null) after.delete('tab')
        else after.set('tab', String(next))
      }),
    [move]
  )

  const setView = useCallback(
    (next: UpstoxView | CustomView) => move((after) => after.set('view', next)),
    [move]
  )

  return { overlay, tab, view: viewOf(params, tab), open, close, selectTab, setView }
}
