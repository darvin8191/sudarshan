import { useCallback, useLayoutEffect, useRef } from 'react'
import { useLocation, useNavigate, useSearchParams } from 'react-router'

/**
 * What the Reports page shows, kept in the address so the phone's Back closes a sheet before it
 * leaves the page (the journal's rules, journal/useJournalOverlay.ts):
 *
 * - `?view=archive` (the default) or `settings`: the part shown, replaced on a move so Back never
 *   walks through the tabs;
 * - `?report=<id>`: that report's text in a sheet. It is a read, so the address alone names it
 *   and a link to a report still opens it.
 *
 * Opening pushes an entry marked as this page's own and closing goes back; a sheet the page did
 * not open (a link, a reload) is closed by replacing the entry, so closing never leaves the app.
 */
export const REPORT_VIEWS = ['archive', 'settings'] as const
export type ReportsView = (typeof REPORT_VIEWS)[number]

export function reportsViewOf(params: URLSearchParams): ReportsView {
  return params.get('view') === 'settings' ? 'settings' : 'archive'
}

export function reportIdOf(params: URLSearchParams): number | null {
  const raw = params.get('report')
  if (raw === null || !/^[0-9]{1,9}$/.test(raw)) return null
  const id = Number(raw)
  return id > 0 ? id : null
}

export function useReportsOverlay(): {
  view: ReportsView
  reportId: number | null
  setView: (view: ReportsView) => void
  open: (id: number) => void
  close: () => void
} {
  const [params] = useSearchParams()
  const location = useLocation()
  const navigate = useNavigate()
  const reportId = reportIdOf(params)
  const pushed = (location.state as { sdOverlay?: unknown } | null)?.sdOverlay === true

  const here = useRef({ params, reportId, pushed, pathname: location.pathname })
  const landed = useRef(location.key)
  useLayoutEffect(() => {
    if (landed.current === location.key) return
    landed.current = location.key
    here.current = { params, reportId, pushed, pathname: location.pathname }
  })

  const to = useCallback(
    (after: URLSearchParams, replace: boolean, state: unknown) => {
      const search = after.toString()
      navigate(
        { pathname: here.current.pathname, search: search ? `?${search}` : '' },
        { replace, state }
      )
    },
    [navigate]
  )

  const open = useCallback(
    (id: number) => {
      const { params: current, reportId: shown, pushed: own } = here.current
      const after = new URLSearchParams(current)
      after.set('report', String(id))
      const keep = shown !== null ? own : true
      here.current = { ...here.current, params: after, reportId: id, pushed: keep }
      to(after, shown !== null, { sdOverlay: keep })
    },
    [to]
  )

  const close = useCallback(() => {
    const { params: current, reportId: shown, pushed: own } = here.current
    if (shown === null) return
    const after = new URLSearchParams(current)
    after.delete('report')
    here.current = { ...here.current, params: after, reportId: null, pushed: false }
    if (own) navigate(-1)
    else to(after, true, null)
  }, [navigate, to])

  const setView = useCallback(
    (next: ReportsView) => {
      const { params: current, reportId: shown, pushed: own } = here.current
      const after = new URLSearchParams(current)
      if (next === 'archive') after.delete('view')
      else after.set('view', next)
      here.current = { ...here.current, params: after }
      to(after, true, shown !== null ? { sdOverlay: own } : null)
    },
    [to]
  )

  return { view: reportsViewOf(params), reportId, setView, open, close }
}
