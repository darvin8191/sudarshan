/**
 * What the options screen has open over the chain, kept as browser history entries so the phone's
 * Back closes it instead of leaving the page, as in a broker's app (the phone chart's rule). One
 * already open is swapped, not stacked. An order ticket only ever opens from a tap: one restored
 * with the page (a reload, the phone bringing the app back) is closed at once.
 */
import { useCallback, useEffect, useRef } from 'react'
import { useLocation, useNavigate } from 'react-router'
import type { AlertPrefill } from '@/sudarshan/alerts/AlertForm'
import type { OrderRequest } from '@/sudarshan/watchlist/SymbolSheet'

export type Sheet =
  | { sheet: 'underlying' }
  | { sheet: 'strike'; strike: number; side: 'ce' | 'pe' }
  /** One leg's own sheet (chart, alert, depth), reached from the strike sheet. */
  | { sheet: 'leg'; strike: number; side: 'ce' | 'pe' }
  | { sheet: 'alert'; alert: AlertPrefill }
  | { sheet: 'ticket'; order: OrderRequest }

export function readSheet(state: unknown): Sheet | null {
  if (!state || typeof state !== 'object') return null
  const value = state as {
    sheet?: unknown
    strike?: unknown
    side?: unknown
    order?: unknown
    alert?: unknown
  }
  if (value.sheet === 'underlying') return { sheet: 'underlying' }
  if (
    (value.sheet === 'strike' || value.sheet === 'leg') &&
    typeof value.strike === 'number' &&
    (value.side === 'ce' || value.side === 'pe')
  )
    return { sheet: value.sheet, strike: value.strike, side: value.side }
  if (value.sheet === 'alert' && value.alert && typeof value.alert === 'object')
    return { sheet: 'alert', alert: value.alert as AlertPrefill }
  if (value.sheet === 'ticket' && value.order && typeof value.order === 'object')
    return { sheet: 'ticket', order: value.order as OrderRequest }
  return null
}

export function useSheets(): {
  sheet: Sheet | null
  open: (next: Sheet) => void
  close: () => void
} {
  const location = useLocation()
  const navigate = useNavigate()
  const sheet = readSheet(location.state)
  const closing = useRef(false)
  const open = useCallback(
    (next: Sheet) =>
      navigate(location.pathname + location.search, {
        state: next,
        replace: readSheet(location.state) !== null,
      }),
    [location.pathname, location.search, location.state, navigate]
  )
  const close = useCallback(() => {
    if (!readSheet(location.state) || closing.current) return
    closing.current = true
    navigate(-1)
  }, [location.state, navigate])
  // Every move through history settles what is open.
  // biome-ignore lint/correctness/useExhaustiveDependencies: the key is the trigger
  useEffect(() => {
    closing.current = false
  }, [location.key])
  const restored = useRef(sheet?.sheet === 'ticket')
  useEffect(() => {
    if (!restored.current) return
    restored.current = false
    close()
  }, [close])
  return { sheet, open, close }
}
