/**
 * "Alerts today" on the dashboard (M3.8, D5): today's latest fires read once and again on the
 * alerts push (one read per burst) and after a reconnect, the active count, and the words while
 * this screen does not hear the owner's room.
 */
import { act } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { AlertEvent, MarketAlertsState } from '@/api/marketAlerts'

const api = vi.hoisted(() => ({
  state: vi.fn(),
  events: vi.fn(),
}))
vi.mock('@/api/marketAlerts', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/api/marketAlerts')>()),
  marketAlertsApi: api,
}))

const socket = vi.hoisted(() => {
  const handlers = new Map<string, Set<(...args: unknown[]) => void>>()
  return {
    on(event: string, handler: (...args: unknown[]) => void) {
      handlers.set(event, (handlers.get(event) ?? new Set()).add(handler))
    },
    off(event: string, handler: (...args: unknown[]) => void) {
      handlers.get(event)?.delete(handler)
    },
    fire(event: string, ...args: unknown[]) {
      for (const handler of [...(handlers.get(event) ?? [])]) handler(...args)
    },
    listeners: (event: string) => handlers.get(event)?.size ?? 0,
  }
})
vi.mock('@/components/socket/SocketProvider', () => ({
  useSocketContext: () => ({ playAlertSound: () => {}, socket }),
}))

const hearing = vi.hoisted(() => ({ current: 'joined' as 'joined' | 'not_joined' | 'unknown' }))
vi.mock('@/sudarshan/alerts/useMarketAlertsState', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/sudarshan/alerts/useMarketAlertsState')>()),
  useAlertsHearing: () => hearing.current,
}))

import { EVENTS_KEY } from '@/sudarshan/alerts/stateCache'
import { alertEvent, alertRow, alertsState, NOW } from '@/test/marketAlerts'
import { render, screen, waitFor, within } from '@/test/test-utils'
import { AlertsCard } from './AlertsCard'

function serve(state: MarketAlertsState, ...pages: AlertEvent[][]) {
  api.state.mockResolvedValue(state)
  let read = 0
  api.events.mockImplementation(async () => {
    const events = pages[Math.min(read, pages.length - 1)]
    read += 1
    return { events, more: false }
  })
}

beforeEach(() => {
  vi.useFakeTimers({ now: NOW, toFake: ['Date'] })
  hearing.current = 'joined'
})

afterEach(() => {
  vi.useRealTimers()
  api.state.mockReset()
  api.events.mockReset()
})

describe('the alerts card', () => {
  it("lists today's latest fires with their times, the active count, and links to Alerts", async () => {
    serve(
      alertsState({
        alerts: [alertRow(), alertRow({ id: 13 }), alertRow({ id: 14, status: 'paused' })],
      }),
      [
        alertEvent({ id: 342, title: 'NIFTY 50 fell 1% from close', at: NOW - 60_000 }),
        alertEvent(),
        alertEvent({ id: 300, title: 'Yesterday', at: NOW - 86_400_000 }),
      ]
    )
    render(<AlertsCard />)

    const list = await screen.findByRole('list', { name: 'Latest fires' })
    const rows = within(list).getAllByRole('link')
    expect(rows.map((row) => row.textContent)).toEqual([
      'NIFTY 50 fell 1% from close10:41:31',
      'SBIN crossed above ₹100.0010:37:31',
    ])
    expect(rows[1]).toHaveAttribute('href', '/market-alerts?alert=12')
    expect(screen.getByText(/^2 active/)).toHaveTextContent('2 active· Hearing alerts')
    expect(screen.getByRole('link', { name: 'See all' })).toHaveAttribute('href', '/market-alerts')
    expect(api.events).toHaveBeenCalledWith({ limit: 3 }, expect.anything())
  })

  it('reads again once after a burst of pushed fires, and after a reconnect', async () => {
    serve(alertsState(), [alertEvent()], [alertEvent({ id: 342, title: 'Second' }), alertEvent()])
    const { unmount } = render(<AlertsCard />)
    await screen.findByText('SBIN crossed above ₹100.00')
    expect(socket.listeners('sudarshan_market_alert')).toBe(1)

    vi.useFakeTimers({ now: NOW, toFake: ['Date', 'setTimeout', 'clearTimeout'] })
    for (let i = 0; i < 4; i += 1)
      act(() => socket.fire('sudarshan_market_alert', { event_id: 342 }))
    await act(async () => {
      await vi.advanceTimersByTimeAsync(500)
    })
    vi.useFakeTimers({ now: NOW, toFake: ['Date'] })
    expect(await screen.findByText('Second')).toBeVisible()
    expect(api.events).toHaveBeenCalledTimes(2) // one read for the burst
    unmount()
    expect(socket.listeners('sudarshan_market_alert')).toBe(0)
  })

  it("is read again by the Alerts page's reconnect, which re-reads every events page", async () => {
    serve(alertsState(), [alertEvent()], [alertEvent({ id: 342, title: 'Missed while away' })])
    let client: import('@tanstack/react-query').QueryClient | null = null
    const { useQueryClient } = await import('@tanstack/react-query')
    function Spy() {
      client = useQueryClient()
      return null
    }
    render(
      <>
        <Spy />
        <AlertsCard />
      </>
    )
    await screen.findByText('SBIN crossed above ₹100.00')
    await act(async () => {
      await client?.invalidateQueries({ queryKey: EVENTS_KEY })
    })
    expect(await screen.findByText('Missed while away')).toBeVisible()
  })

  it('says the figures are from the last read while this screen does not hear the room', async () => {
    hearing.current = 'not_joined'
    serve(alertsState(), [alertEvent()])
    render(<AlertsCard />)
    expect(
      await screen.findByText('Not receiving updates · figures are from the last read')
    ).toBeVisible()
    expect(screen.queryByText(/Hearing alerts/)).toBeNull()
  })

  it("gives the service's own line while it watches no ticks, and says when nothing fired today", async () => {
    serve(
      alertsState({
        engine: { code: 'feed_down', line: 'Feed down · alerts wait for it', real_time: false },
      }),
      []
    )
    render(<AlertsCard />)
    expect(await screen.findByText('Feed down · alerts wait for it')).toBeVisible()
    await waitFor(() => expect(screen.getByText('Nothing has fired today')).toBeVisible())
  })

  it('draws nothing while market alerts are off', async () => {
    serve(alertsState({ enabled: false, alerts: [] }), [])
    const { container } = render(<AlertsCard />)
    await waitFor(() => expect(api.state).toHaveBeenCalled())
    expect(container).toBeEmptyDOMElement()
    expect(api.events).not.toHaveBeenCalled()
  })
})
