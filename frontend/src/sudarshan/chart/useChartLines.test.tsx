import { QueryClientProvider } from '@tanstack/react-query'
import { act, renderHook, waitFor } from '@testing-library/react'
import type { ReactNode } from 'react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { createQueryClient } from '@/app/providers'
import { useThemeStore } from '@/stores/themeStore'
import { resetOrderMemoryForTests } from '@/sudarshan/books/orderFold'
import { useChartBooks, useChartLines } from './useChartLines'

const api = vi.hoisted(() => ({
  positions: vi.fn(),
  orders: vi.fn(),
  alerts: vi.fn(),
}))
vi.mock('@/api/trading', () => ({
  tradingApi: { getPositions: api.positions, getOrders: api.orders },
}))
vi.mock('@/api/marketAlerts', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/api/marketAlerts')>()),
  marketAlertsApi: { state: api.alerts },
}))

const SYMBOL = 'NIFTY18NOV2625900CE'
const held = {
  symbol: SYMBOL,
  exchange: 'NFO',
  product: 'MIS',
  quantity: 65,
  average_price: 131.2,
  ltp: 140,
  pnl: 572,
  pnlpercent: 6.7,
}
const stop = {
  symbol: SYMBOL,
  exchange: 'NFO',
  action: 'SELL',
  quantity: 65,
  price: 0,
  trigger_price: 120,
  pricetype: 'SL-M',
  product: 'MIS',
  orderid: 'A1',
  order_status: 'trigger pending',
  timestamp: '2026-11-16 10:41:51',
}
const ok = <T,>(data: T) => ({ status: 'success', data })

const wrapper = ({ children }: { children: ReactNode }) => (
  <QueryClientProvider client={createQueryClient()}>{children}</QueryClientProvider>
)

function useLines(enabled = true) {
  const books = useChartBooks({ apiKey: 'test-key', enabled, marketOpen: () => null })
  return { books, drawn: useChartLines({ symbol: SYMBOL, exchange: 'NFO', decimals: 2, books }) }
}

beforeEach(() => {
  vi.clearAllMocks()
  resetOrderMemoryForTests()
  useThemeStore.setState({ appMode: 'live' })
  api.positions.mockResolvedValue(ok([held]))
  api.orders.mockResolvedValue(ok({ orders: [stop], statistics: {} }))
  api.alerts.mockResolvedValue({
    enabled: true,
    alerts: [
      {
        id: 7,
        status: 'active',
        kind: 'price',
        symbol: SYMBOL,
        exchange: 'NFO',
        params: { op: 'at_or_above', level: '150' },
      },
    ],
  })
})

describe("the books behind the chart's lines", () => {
  it('reads positions and orders once when the chart opens, and draws the position, the order and the alert', async () => {
    const { result } = renderHook(() => useLines(), { wrapper })
    await waitFor(() => expect(result.current.drawn.lines).toHaveLength(3))
    expect(result.current.drawn.lines.map((line) => line.label)).toEqual([
      'Buy 65 @ 131.20',
      'SELL 65 SL-M 120.00',
      'Alert ≥ 150.00',
    ])
    expect(api.positions).toHaveBeenCalledTimes(1)
    expect(api.orders).toHaveBeenCalledTimes(1)
    expect(result.current.books.failed).toBe(false)
  })

  it('reads nothing for an index, which is never traded', async () => {
    const { result } = renderHook(() => useLines(false), { wrapper })
    await waitFor(() => expect(api.alerts).toHaveBeenCalled())
    expect(api.positions).not.toHaveBeenCalled()
    expect(api.orders).not.toHaveBeenCalled()
    expect(result.current.books.loading).toBe(true)
  })

  it('keeps the lines of the last good read when a read fails, grey with when they were read', async () => {
    const { result } = renderHook(() => useLines(), { wrapper })
    await waitFor(() => expect(result.current.drawn.lines).toHaveLength(3))
    api.orders.mockRejectedValue(new Error('down'))
    await act(() => result.current.books.refresh())
    expect(result.current.books.failed).toBe(true)
    const [position, order, alert] = result.current.drawn.lines
    expect(position.stale).toBe(true)
    expect(position.note).toMatch(/^as of \d\d:\d\d:\d\d$/)
    expect(order.stale).toBe(true)
    expect(alert.stale).toBeUndefined()
    // The next good read clears it.
    api.orders.mockResolvedValue(ok({ orders: [stop], statistics: {} }))
    await act(() => result.current.books.refresh())
    expect(result.current.drawn.lines[0].stale).toBeUndefined()
  })

  it('draws no book line when the first read fails: nothing is shown that was never read', async () => {
    api.positions.mockResolvedValue({ status: 'error', message: 'no session' })
    const { result } = renderHook(() => useLines(), { wrapper })
    await waitFor(() => expect(result.current.books.failed).toBe(true))
    expect(result.current.drawn.lines.map((line) => line.kind)).toEqual(['alert'])
  })

  it('drops the lines when the mode changes, until the other mode has been read', async () => {
    const { result } = renderHook(() => useLines(), { wrapper })
    await waitFor(() => expect(result.current.drawn.lines).toHaveLength(3))
    let answer: (value: unknown) => void = () => {}
    api.positions.mockReturnValue(
      new Promise((resolve) => {
        answer = resolve
      })
    )
    api.orders.mockResolvedValue(ok({ orders: [], statistics: {} }))
    act(() => useThemeStore.getState().setAppMode('analyzer'))
    await waitFor(() =>
      expect(result.current.drawn.lines.map((line) => line.kind)).toEqual(['alert'])
    )
    await act(async () => answer(ok([{ ...held, quantity: -65 }])))
    await waitFor(() => expect(result.current.drawn.lines[0]?.label).toBe('Sell 65 @ 131.20'))
  })
})
