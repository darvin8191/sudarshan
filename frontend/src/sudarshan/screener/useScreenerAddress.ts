import { useCallback, useLayoutEffect, useRef } from 'react'
import { useLocation, useNavigate, useSearchParams } from 'react-router'

/**
 * What the Screener page has open, kept in the address so the phone's Back closes it before it
 * leaves the page, by the watchlist's rules (watchlist/useOverlay.ts). Two layers:
 *
 * - the screen: `?screen=<id>` with `&tab=results|history|edit`; the Library lives at
 *   `?tab=library` with no screen;
 * - a sheet over it: `?sheet=new|delete|settings|library` (a clause's or operand's sheet is the
 *   builder's own, with `&sheet=clause&c=<clause id>`), and `?sheet=run&run=<id>` for a past run's set; a screen being written is `?new=1`.
 *
 * Opening pushes a history entry marked as this page's own, and closing goes back, so Back and
 * the close button leave the same history behind; a layer the page did not open (a link, a
 * reload) is closed by replacing the entry, so closing never leaves the app. Moving between tabs
 * replaces the entry: Back never walks back through tabs. Closing twice for one entry goes back
 * once.
 */
export type ScreenTab = 'results' | 'history' | 'edit'
export type HomeTab = 'screens' | 'library'

export interface Address {
  screen: number | null
  /** A screen being written, not saved yet (`?new=1`). */
  draft: boolean
  tab: ScreenTab
  home: HomeTab
  sheet: string | null
  /** A node the sheet is about: a clause id, or `g` and a path for a group (`g.0.2`). */
  clause: string | null
  /** Which side of the clause an operand sheet edits. */
  side: 'left' | 'right' | null
  run: number | null
}

const SHEETS = new Set([
  'new',
  'delete',
  'settings',
  'clause',
  'operator',
  'operand',
  'group',
  'target',
  'alerts',
  'preview',
  'run',
])

function positive(value: string | null): number | null {
  const n = Number(value)
  return value !== null && Number.isInteger(n) && n > 0 ? n : null
}

export function addressOf(params: URLSearchParams): Address {
  const screen = positive(params.get('screen'))
  const tab = params.get('tab')
  const sheet = params.get('sheet')
  return {
    screen,
    draft: screen === null && params.get('new') === '1',
    tab: tab === 'history' || tab === 'edit' ? tab : 'results',
    home: screen === null && tab === 'library' ? 'library' : 'screens',
    sheet: sheet && SHEETS.has(sheet) ? sheet : null,
    clause: params.get('c'),
    side:
      params.get('side') === 'left' || params.get('side') === 'right'
        ? (params.get('side') as 'left' | 'right')
        : null,
    run: screen === null ? null : positive(params.get('run')),
  }
}

interface Entry {
  sdScreen?: boolean
  sdSheet?: boolean
}

const entryOf = (state: unknown): Entry =>
  state !== null && typeof state === 'object' ? (state as Entry) : {}

export function useScreenerAddress() {
  const [params] = useSearchParams()
  const location = useLocation()
  const navigate = useNavigate()
  const address = addressOf(params)
  const entry = entryOf(location.state)

  const here = useRef({ params, entry, pathname: location.pathname })
  const landed = useRef(location.key)
  useLayoutEffect(() => {
    if (landed.current === location.key) return
    landed.current = location.key
    here.current = { params, entry, pathname: location.pathname }
  })

  const go = useCallback(
    (after: URLSearchParams, replace: boolean, state: Entry | null) => {
      const { pathname } = here.current
      here.current = { params: after, entry: state ?? {}, pathname }
      const search = after.toString()
      navigate({ pathname, search: search ? `?${search}` : '' }, { replace, state })
    },
    [navigate]
  )

  /** Open a screen (pushed) or swap the one open (replaced, keeping its mark). */
  const openScreen = useCallback(
    (id: number, tab: ScreenTab = 'results') => {
      const { params: current, entry: now } = here.current
      const after = new URLSearchParams()
      after.set('screen', String(id))
      if (tab !== 'results') after.set('tab', tab)
      // From a draft just saved, or from another screen: the entry is swapped, not stacked.
      const shown = positive(current.get('screen')) !== null || current.get('new') === '1'
      go(after, shown, { sdScreen: shown ? !!now.sdScreen : true })
    },
    [go]
  )

  /** Open the editor for a new screen (pushed). */
  const openDraft = useCallback(() => {
    go(new URLSearchParams({ new: '1' }), false, { sdScreen: true })
  }, [go])

  /** Leave the open screen or the draft: back to the list. */
  const closeScreen = useCallback(() => {
    const { params: current, entry: now } = here.current
    if (positive(current.get('screen')) === null && current.get('new') !== '1') return
    if (now.sdScreen && !now.sdSheet && !current.get('sheet')) {
      here.current = { ...here.current, params: new URLSearchParams(), entry: {} }
      navigate(-1)
      return
    }
    go(new URLSearchParams(), true, null)
  }, [go, navigate])

  /** Open a sheet over what is shown (pushed), or swap the one open (replaced). */
  const openSheet = useCallback(
    (sheet: string, extra: Record<string, string> = {}) => {
      const { params: current, entry: now } = here.current
      const after = new URLSearchParams(current)
      after.delete('c')
      after.delete('side')
      after.delete('run')
      after.set('sheet', sheet)
      for (const [key, value] of Object.entries(extra)) after.set(key, value)
      const shown = current.has('sheet')
      go(after, shown, { sdScreen: !!now.sdScreen, sdSheet: shown ? !!now.sdSheet : true })
    },
    [go]
  )

  const closeSheet = useCallback(() => {
    const { params: current, entry: now } = here.current
    if (!current.has('sheet')) return
    if (now.sdSheet) {
      const after = new URLSearchParams(current)
      after.delete('sheet')
      after.delete('c')
      after.delete('side')
      here.current = { ...here.current, params: after, entry: { sdScreen: !!now.sdScreen } }
      navigate(-1)
      return
    }
    const after = new URLSearchParams(current)
    after.delete('sheet')
    after.delete('c')
    after.delete('side')
    go(after, true, now.sdScreen ? { sdScreen: true } : null)
  }, [go, navigate])

  /** A tab moves in place: Back never walks back through tabs. */
  const setTab = useCallback(
    (tab: ScreenTab | HomeTab) => {
      const { params: current, entry: now } = here.current
      const after = new URLSearchParams(current)
      after.delete('run')
      if (tab === 'results' || tab === 'screens') after.delete('tab')
      else after.set('tab', tab)
      go(after, true, now.sdScreen || now.sdSheet ? now : null)
    },
    [go]
  )

  return { address, openScreen, openDraft, closeScreen, openSheet, closeSheet, setTab }
}
