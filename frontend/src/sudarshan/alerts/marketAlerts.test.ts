/**
 * Market alerts in the open app (M10.7): the join on every connect with its ack, tried again
 * while no ack comes; a new page load starting from the newest event and telling nothing from
 * before it; what fired while the connection was down (one toast for one, one toast for several),
 * never lost to a push heard before the catch-up read it, a read that failed or a connection that
 * dropped; the pushed alert's toast and sound; one announcement across the app's tabs, a burst,
 * and the Alerts page's cache kept current by the state and delivery pushes. The sound itself is
 * chime.test.ts's.
 */
import { type InfiniteData, QueryClient, QueryObserver } from '@tanstack/react-query'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const toasts = vi.hoisted(() => ({ info: vi.fn(), dismiss: vi.fn() }))
vi.mock('@/utils/toast', () => ({ toast: toasts }))

const chime = vi.hoisted(() => ({ played: [] as string[] }))
vi.mock('@/sudarshan/books/chime', () => ({
  unlockSoundOnFirstTap: () => {},
  playChime: (kind: string) => {
    chime.played.push(kind)
    return true
  },
}))

vi.mock('@/sudarshan/books/sharedSocket', () => ({
  connectionCount: (socket: { count?: number }) => socket.count,
}))

const api = vi.hoisted(() => ({ state: vi.fn(), eventsSince: vi.fn() }))
vi.mock('@/api/marketAlerts', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/api/marketAlerts')>()),
  marketAlertsApi: api,
}))

import type {
  AlertEvent,
  AlertRow,
  EventsPage,
  MarketAlertPush,
  MarketAlertsState,
} from '@/api/marketAlerts'
import { useAlertStore } from '@/stores/alertStore'
import {
  ALERTS_PATH,
  bindMarketAlertsCache,
  hearingNow,
  installMarketAlerts,
  RETRY_MS,
  resetMarketAlertsForTests,
  setTabChannelForTests,
  TAB_DEFER_MS,
  type TabChannel,
  withRecentDeliveries,
} from './marketAlerts'
import { HISTORY_KEY, STATE_KEY } from './stateCache'

type Handler = (...args: any[]) => void

/** The app's shared connection, stood in for: connects, acks and pushes by hand. */
function fakeSocket() {
  const handlers = new Map<string, Set<Handler>>()
  const acks: Handler[] = []
  const socket = {
    connected: false,
    count: 0,
    joins: 0,
    on(event: string, handler: Handler) {
      handlers.set(event, (handlers.get(event) ?? new Set()).add(handler))
      return socket
    },
    off(event: string, handler: Handler) {
      handlers.get(event)?.delete(handler)
      return socket
    },
    listeners(event: string) {
      return [...(handlers.get(event) ?? [])]
    },
    timeout(_ms: number) {
      return {
        emit(event: string, ack: Handler) {
          if (event === 'sudarshan_alerts_join') socket.joins += 1
          acks.push(ack)
        },
      }
    },
    fire(event: string, ...args: unknown[]) {
      for (const handler of [...(handlers.get(event) ?? [])]) handler(...args)
    },
    connect() {
      socket.connected = true
      socket.count += 1
      socket.fire('connect')
    },
    drop() {
      socket.connected = false
      socket.fire('disconnect')
    },
    /** The server's answer to the join waiting longest. */
    ack(answer: unknown, error: unknown = null) {
      acks.shift()?.(error, answer)
    },
  }
  return socket
}

/** The browser's channel between this app's tabs, stood in for. */
function fakeChannel() {
  const sent: unknown[] = []
  const channel: TabChannel & { sent: unknown[]; say: (data: unknown) => void } = {
    sent,
    onmessage: null,
    postMessage: (message: unknown) => {
      sent.push(message)
    },
    close: () => {},
    say: (data: unknown) => channel.onmessage?.({ data } as MessageEvent),
  }
  return channel
}

/** 10:42:31 in India on 28 Oct 2026. */
const NOW = Date.UTC(2026, 9, 28, 5, 12, 31)

const flush = () => new Promise((resolve) => setTimeout(resolve, 0))

function push(patch: Partial<MarketAlertPush> = {}): MarketAlertPush {
  return {
    event_id: 345,
    alert_id: 12,
    symbol: 'SBIN',
    exchange: 'NSE',
    title: 'SBIN crossed above ₹100.00',
    detail: 'now ₹100.05',
    note: '',
    at: '2026-10-28T10:42:29+05:30',
    at_ms: NOW - 2_000,
    found_later: false,
    opening: false,
    auction: false,
    test: false,
    ...patch,
  }
}

function event(patch: Partial<AlertEvent> = {}): AlertEvent {
  return {
    id: 341,
    alert_id: 12,
    at: NOW - 5 * 60_000,
    trade_time: NOW - 5 * 60_000,
    symbol: 'SBIN',
    exchange: 'NSE',
    kind: 'price',
    title: 'SBIN crossed above ₹100.00',
    detail: 'now ₹100.05',
    note: '',
    line: 'Alert: SBIN crossed above ₹100.00: now ₹100.05 (NSE)',
    value: '₹100.05',
    level: '₹100.00',
    found_later: false,
    opening: false,
    test: false,
    deliveries: {
      telegram: { status: 'sent', reason: '', attempts: 1, updated_at: NOW - 299_000 },
      app: {
        status: 'not_sent',
        reason: 'No app was open',
        attempts: 1,
        updated_at: NOW - 299_000,
      },
    },
    ...patch,
  }
}

function row(patch: Partial<AlertRow> = {}): AlertRow {
  return {
    id: 12,
    version: 3,
    status: 'active',
    status_reason: '',
    kind: 'price',
    symbol: 'SBIN',
    exchange: 'NSE',
    instrument_key: 'NSE_EQ|INE062A01020',
    tick_size: '0.05',
    expiry: null,
    params: { op: 'crosses_above', level: '100', source: 'ltp' },
    frequency: 'every_time',
    candle_min: null,
    cooldown_s: 300,
    ends_at: null,
    note: '',
    channels: null,
    created_at: NOW - 86_400_000,
    updated_at: NOW - 86_400_000,
    words: { condition: 'Crosses above ₹100.00', meta: 'Every time · wait 5 min', checks_at: null },
    check: null,
    fired_today: 2,
    auction: null,
    ...patch,
  }
}

function state(patch: Partial<MarketAlertsState> = {}): MarketAlertsState {
  return {
    enabled: true,
    engine: { code: 'ok', line: 'Watching · Real-time', real_time: true },
    segments: { NSE_EQ: { code: 'ok', line: 'Watching · Real-time', real_time: true } },
    limits: { instruments: 200, instruments_used: 1, active: 500, active_used: 1 },
    prefs: { telegram: true, app: true },
    alerts: [row()],
    last_event_id: 340,
    server_time: NOW,
    ...patch,
  }
}

const page = (events: AlertEvent[], more = false): EventsPage => ({ events, more })

let channel = fakeChannel()
let navigate = vi.fn()

function install(socket = fakeSocket()) {
  installMarketAlerts(socket as never, (name, handler) => socket.on(name, handler), navigate)
  return socket
}

/** Connected, joined, and started from the newest event /state names (340). */
async function joinedSocket() {
  const socket = install()
  socket.connect()
  socket.ack({ ok: true })
  await flush()
  return socket
}

/** The connection drops and comes back, the join is acked, and the catch-up has run. */
async function reconnect(socket: ReturnType<typeof fakeSocket>) {
  socket.drop()
  socket.connect()
  socket.ack({ ok: true })
  await flush()
  await flush()
}

/** What fired while the phone was off the network: 341 to 349. */
const GAP = Array.from({ length: 9 }, (_, index) => event({ id: 341 + index }))
const sinceGap = async (after: number) => page(GAP.filter((each) => each.id > after))

/** How many events the toasts tell of: one each, or N for "N alerts fired while the app was away". */
function told(): number {
  return shown().reduce((sum, [title]) => {
    const many = /^(\d+) alerts fired while the app was away$/.exec(title)
    return sum + (many ? Number(many[1]) : 1)
  }, 0)
}

/** The toasts shown, as [title, description]. */
function shown(): Array<[string, string]> {
  return toasts.info.mock.calls.map(([title, options]) => [title, options.description])
}

beforeEach(() => {
  vi.useFakeTimers({ toFake: ['Date'] })
  vi.setSystemTime(NOW)
  resetMarketAlertsForTests()
  channel = fakeChannel()
  setTabChannelForTests(channel)
  navigate = vi.fn()
  toasts.info.mockReset()
  toasts.dismiss.mockReset()
  chime.played.length = 0
  api.state.mockReset().mockResolvedValue(state())
  api.eventsSince.mockReset().mockResolvedValue(page([]))
  localStorage.clear()
  useAlertStore.getState().resetToDefaults()
  vi.spyOn(document, 'hasFocus').mockReturnValue(true)
})

afterEach(() => {
  vi.useRealTimers()
  vi.restoreAllMocks()
  localStorage.clear()
})

describe('joining the owner’s room', () => {
  it('joins on connect and, once acked, starts a new page load from the newest event, telling nothing', async () => {
    // A device that last had the app open days ago keeps nothing from then: Telegram had it.
    localStorage.setItem('sudarshan-market-alerts-seen', '12')
    const socket = install()
    socket.connect()

    expect(socket.joins).toBe(1)
    expect(api.state).not.toHaveBeenCalled()
    socket.ack({ ok: true })
    await flush()

    expect(api.state).toHaveBeenCalledTimes(1)
    expect(api.eventsSince).not.toHaveBeenCalled()
    expect(toasts.info).not.toHaveBeenCalled()

    await reconnect(socket)
    expect(api.eventsSince).toHaveBeenCalledWith(340, 200)
  })

  it('joins again on a reconnect, and reads the page’s state and an open History again', async () => {
    const client = new QueryClient()
    client.setQueryData(STATE_KEY, state())
    const invalidate = vi.spyOn(client, 'invalidateQueries')
    bindMarketAlertsCache(client)
    const socket = await joinedSocket()
    expect(invalidate).not.toHaveBeenCalled()

    await reconnect(socket)

    expect(socket.joins).toBe(2)
    expect(api.eventsSince).toHaveBeenCalledTimes(1)
    const keys = invalidate.mock.calls.map(([filters]) => filters?.queryKey)
    expect(keys).toContainEqual(STATE_KEY)
    expect(keys).toContainEqual(['sudarshan', 'market-alerts', 'events'])
  })

  it('asks for nothing when the session is not the owner’s, or no ack comes', async () => {
    const socket = install()
    socket.connect()
    socket.ack({ ok: false, message: 'Only the account owner can manage market alerts.' })
    socket.drop()
    socket.connect()
    socket.ack(undefined, new Error('operation has timed out'))
    await flush()

    expect(api.state).not.toHaveBeenCalled()
    expect(api.eventsSince).not.toHaveBeenCalled()
  })

  it('asks to join again while no ack comes, and catches up once one does', async () => {
    vi.useFakeTimers({ toFake: ['Date', 'setTimeout', 'clearTimeout'] })
    vi.setSystemTime(NOW)
    const socket = install()
    socket.connect()
    socket.ack(undefined, new Error('operation has timed out'))
    await vi.advanceTimersByTimeAsync(RETRY_MS[0])
    expect(socket.joins).toBe(2)

    socket.ack({ ok: true })
    await vi.advanceTimersByTimeAsync(0)
    expect(api.state).toHaveBeenCalledTimes(1)
  })

  it('does not join twice when installed again on a connection already joined', async () => {
    const socket = await joinedSocket()
    // A layout swap: a new provider installs on the same live connection.
    installMarketAlerts(socket as never, (name, handler) => socket.on(name, handler), navigate)
    expect(socket.joins).toBe(1)
  })

  it('joins when installed on a connection already up that has not joined', async () => {
    const socket = fakeSocket()
    socket.connected = true
    socket.count = 1
    install(socket)
    expect(socket.joins).toBe(1)
  })

  it('leaves nothing listening once the app’s provider takes its handlers off', () => {
    const socket = fakeSocket()
    const taken: Array<[string, Handler]> = []
    installMarketAlerts(
      socket as never,
      (name, handler) => {
        taken.push([name, handler])
        socket.on(name, handler)
      },
      navigate
    )
    for (const [name, handler] of taken) socket.off(name, handler)

    socket.connect()
    expect(socket.joins).toBe(0)
  })

  it('says whether this screen hears the owner’s room: joined only from the ack to a drop', async () => {
    expect(hearingNow()).toBe('unknown')
    const socket = install()
    expect(hearingNow()).toBe('not_joined')
    socket.connect()
    expect(hearingNow()).toBe('not_joined')
    socket.ack({ ok: true })
    expect(hearingNow()).toBe('joined')
    socket.drop()
    expect(hearingNow()).toBe('not_joined')
  })
})

describe('what fired while the app was away', () => {
  it('does not start from a state that is switched off', async () => {
    api.state.mockResolvedValue(
      state({
        enabled: false,
        alerts: [],
        last_event_id: 0,
        engine: { code: 'off', line: 'Switched off on the server', real_time: false },
      })
    )
    const socket = await joinedSocket()
    api.state.mockResolvedValue(state({ last_event_id: 341 }))

    await reconnect(socket)
    expect(api.eventsSince).not.toHaveBeenCalled()
    await reconnect(socket)
    expect(api.eventsSince).toHaveBeenCalledWith(341, 200)
  })

  it('gives one missed alert the toast a pushed one gets, silent once a minute old', async () => {
    const socket = await joinedSocket()
    api.eventsSince.mockResolvedValue(page([event({ note: 'book half' })]))
    await reconnect(socket)

    expect(shown()).toEqual([
      ['Alert · SBIN crossed above ₹100.00', '10:37:31 · now ₹100.05 · Note: book half'],
    ])
    expect(chime.played).toEqual([])

    toasts.info.mock.calls[0][1].action.onClick()
    expect(navigate).toHaveBeenCalledWith(`${ALERTS_PATH}?alert=12`)
  })

  it.each([
    [59_000, ['market']],
    [60_000, ['market']],
    [61_000, []],
  ])('sounds a missed alert only when it fired in the last minute: %i ms ago', async (age, played) => {
    const socket = await joinedSocket()
    api.eventsSince.mockResolvedValue(page([event({ at: NOW - age })]))
    await reconnect(socket)
    expect(chime.played).toEqual(played)
  })

  it('says the day of a missed alert from an earlier day', async () => {
    const socket = await joinedSocket()
    api.eventsSince.mockResolvedValue(page([event({ at: NOW - 86_400_000 })]))
    await reconnect(socket)
    expect(shown()[0][1]).toBe('27 Oct 10:42:31 · now ₹100.05')
  })

  it('gives several one toast, with View to History, leaving out tests and the app turned off', async () => {
    const socket = await joinedSocket()
    api.eventsSince.mockResolvedValue(
      page([
        event({ id: 341 }),
        event({ id: 342, alert_id: 0, test: true, title: 'Test alert' }),
        event({
          id: 343,
          deliveries: {
            app: {
              status: 'off',
              reason: 'Off in your alert settings',
              attempts: 0,
              updated_at: NOW,
            },
          },
        }),
        event({ id: 344, alert_id: 13, title: 'NIFTY crossed below 25,800.00', at: NOW - 120_000 }),
      ])
    )
    await reconnect(socket)

    expect(shown()).toEqual([
      ['2 alerts fired while the app was away', 'Latest 10:40:31 · NIFTY crossed below 25,800.00'],
    ])
    expect(chime.played).toEqual([])
    toasts.info.mock.calls[0][1].action.onClick()
    expect(navigate).toHaveBeenCalledWith(`${ALERTS_PATH}?tab=history`)

    api.eventsSince.mockClear()
    await reconnect(socket)
    expect(api.eventsSince).toHaveBeenCalledWith(344, 200)
  })

  it('reads page after page while the server says there is more', async () => {
    const socket = await joinedSocket()
    api.eventsSince
      .mockResolvedValueOnce(page([event({ id: 341 }), event({ id: 342 })], true))
      .mockResolvedValueOnce(page([event({ id: 343 })]))
    await reconnect(socket)

    expect(api.eventsSince.mock.calls).toEqual([
      [340, 200],
      [342, 200],
    ])
    expect(shown()[0][0]).toBe('3 alerts fired while the app was away')
  })

  it('does not tell again what was pushed and shown here', async () => {
    const socket = await joinedSocket()
    socket.fire('sudarshan_market_alert', push({ event_id: 341 }))
    toasts.info.mockReset()
    api.eventsSince.mockResolvedValue(page([event({ id: 341 })]))

    await reconnect(socket)
    expect(toasts.info).not.toHaveBeenCalled()
  })

  it('tells once an alert pushed while the catch-up that also brings it is on its way', async () => {
    const socket = await joinedSocket()
    let answer: (value: EventsPage) => void = () => {}
    api.eventsSince.mockReturnValueOnce(
      new Promise<EventsPage>((resolve) => {
        answer = resolve
      })
    )
    socket.drop()
    socket.connect()
    socket.ack({ ok: true })
    socket.fire('sudarshan_market_alert', push({ event_id: 345 }))
    answer(page([event({ id: 344 }), event({ id: 345 })]))
    await flush()

    expect(told()).toBe(2)
  })

  it('tells once an alert pushed while a later page of the catch-up is on its way', async () => {
    const socket = await joinedSocket()
    let second: (value: EventsPage) => void = () => {}
    api.eventsSince
      .mockResolvedValueOnce(page([event({ id: 344 }), event({ id: 345 })], true))
      .mockReturnValueOnce(
        new Promise<EventsPage>((resolve) => {
          second = resolve
        })
      )
    socket.drop()
    socket.connect()
    socket.ack({ ok: true })
    await flush()
    socket.fire('sudarshan_market_alert', push({ event_id: 345 }))
    second(page([]))
    await flush()

    expect(told()).toBe(2)
  })

  it('tells once, on a hidden screen, an alert pushed while the catch-up that brings it is on its way', async () => {
    const socket = await joinedSocket()
    const visibility = vi.spyOn(document, 'visibilityState', 'get').mockReturnValue('hidden')
    let answer: (value: EventsPage) => void = () => {}
    api.eventsSince.mockReturnValueOnce(
      new Promise<EventsPage>((resolve) => {
        answer = resolve
      })
    )
    socket.drop()
    socket.connect()
    socket.ack({ ok: true })
    socket.fire('sudarshan_market_alert', push({ event_id: 344 })) // waits for another tab
    answer(page([event({ id: 341 }), event({ id: 342 }), event({ id: 343 }), event({ id: 344 })]))
    await flush()
    await new Promise((resolve) => setTimeout(resolve, TAB_DEFER_MS + 100))
    visibility.mockRestore()

    expect(told()).toBe(4)
  })

  it('marks the events a summary told, so a push for one of them is not told again', async () => {
    const socket = await joinedSocket()
    api.eventsSince.mockResolvedValue(page([event({ id: 341 }), event({ id: 342 })]))
    await reconnect(socket)
    expect(told()).toBe(2)

    socket.fire('sudarshan_market_alert', push({ event_id: 342 }))
    expect(toasts.info).toHaveBeenCalledTimes(1)
  })

  it('notes nothing when the connection drops during the read, so the next connect tells it', async () => {
    const socket = await joinedSocket()
    let answer: (value: EventsPage) => void = () => {}
    api.eventsSince.mockReturnValueOnce(
      new Promise<EventsPage>((resolve) => {
        answer = resolve
      })
    )
    socket.drop()
    socket.connect()
    socket.ack({ ok: true })
    socket.drop()
    answer(page([event()]))
    await flush()
    expect(toasts.info).not.toHaveBeenCalled()

    api.eventsSince.mockResolvedValue(page([event()]))
    socket.connect()
    socket.ack({ ok: true })
    await flush()
    expect(api.eventsSince).toHaveBeenLastCalledWith(340, 200)
    expect(toasts.info).toHaveBeenCalledTimes(1)
  })

  it('reads again while connected when a read fails, and tells what it finds', async () => {
    vi.useFakeTimers({ toFake: ['Date', 'setTimeout', 'clearTimeout'] })
    vi.setSystemTime(NOW)
    const socket = install()
    socket.connect()
    socket.ack({ ok: true })
    await vi.advanceTimersByTimeAsync(0)
    api.eventsSince
      .mockRejectedValueOnce(new Error('The alert service is not answering.'))
      .mockResolvedValue(page([event()]))
    socket.drop()
    socket.connect()
    socket.ack({ ok: true })
    await vi.advanceTimersByTimeAsync(0)
    expect(toasts.info).not.toHaveBeenCalled()

    await vi.advanceTimersByTimeAsync(RETRY_MS[0])
    expect(api.eventsSince).toHaveBeenCalledTimes(2)
    expect(api.eventsSince).toHaveBeenLastCalledWith(340, 200)
    expect(toasts.info).toHaveBeenCalledTimes(1)
  })
})

describe('nothing missed is lost for good (review H1)', () => {
  it('a push heard while a newer catch-up waits behind a slow one does not skip the gap', async () => {
    const socket = await joinedSocket()
    let answer: (value: EventsPage) => void = () => {}
    api.eventsSince.mockReturnValueOnce(
      new Promise<EventsPage>((resolve) => {
        answer = resolve
      })
    )
    socket.drop()
    socket.connect()
    socket.ack({ ok: true }) // catch-up A reads since 340, slowly
    socket.drop() // 341 to 349 fire while the phone is off the network
    socket.connect()
    socket.ack({ ok: true }) // catch-up B waits behind A
    expect(api.eventsSince).toHaveBeenCalledTimes(1)
    socket.fire('sudarshan_market_alert', push({ event_id: 350 })) // pushed after the rejoin
    api.eventsSince.mockImplementation(sinceGap)
    answer(page([])) // A read before anything fired
    await flush()
    await flush()

    expect(api.eventsSince.mock.calls.map(([after]) => after)).toEqual([340, 340])
    expect(told()).toBe(1 + 9)
  })

  it('a read that failed, then a push, then a reconnect: the gap is still told', async () => {
    const socket = await joinedSocket()
    api.eventsSince.mockRejectedValueOnce(new Error('The alert service is not answering.'))
    socket.drop()
    socket.connect()
    socket.ack({ ok: true })
    await flush()
    socket.fire('sudarshan_market_alert', push({ event_id: 350 }))
    api.eventsSince.mockImplementation(sinceGap)

    await reconnect(socket)
    expect(api.eventsSince.mock.calls.at(-1)?.[0]).toBe(340)
    expect(told()).toBe(1 + 9)
  })

  it('a join whose ack came too late (the room was joined): the gap is still told', async () => {
    const socket = await joinedSocket()
    socket.drop()
    socket.connect()
    socket.ack(undefined, new Error('operation has timed out'))
    await flush()
    socket.fire('sudarshan_market_alert', push({ event_id: 350 })) // the server had joined it
    api.eventsSince.mockImplementation(sinceGap)

    await reconnect(socket)
    expect(told()).toBe(1 + 9)
  })

  it('the connection drops during the catch-up read after a push: the gap is still told', async () => {
    const socket = await joinedSocket()
    let answer: (value: EventsPage) => void = () => {}
    api.eventsSince.mockReturnValueOnce(
      new Promise<EventsPage>((resolve) => {
        answer = resolve
      })
    )
    socket.drop()
    socket.connect()
    socket.ack({ ok: true })
    socket.fire('sudarshan_market_alert', push({ event_id: 350 }))
    socket.drop()
    answer(page(GAP))
    await flush()
    api.eventsSince.mockImplementation(sinceGap)

    socket.connect()
    socket.ack({ ok: true })
    await flush()
    expect(told()).toBe(1 + 9)
  })

  it('a push heard before a new page load has started counts once the start is read', async () => {
    let answer: (value: MarketAlertsState) => void = () => {}
    api.state.mockReturnValueOnce(
      new Promise<MarketAlertsState>((resolve) => {
        answer = resolve
      })
    )
    const socket = install()
    socket.connect()
    socket.ack({ ok: true })
    socket.fire('sudarshan_market_alert', push({ event_id: 352 }))
    answer(state({ last_event_id: 351 }))
    await flush()

    await reconnect(socket)
    expect(api.eventsSince).toHaveBeenCalledWith(352, 200)
  })
})

describe('a pushed alert', () => {
  it('shows its title, time, detail and note, and sounds the market alert sound', async () => {
    const socket = await joinedSocket()
    socket.fire('sudarshan_market_alert', push({ note: 'book half' }))

    expect(shown()).toEqual([
      ['Alert · SBIN crossed above ₹100.00', '10:42:29 · now ₹100.05 · Note: book half'],
    ])
    expect(toasts.info.mock.calls[0][1]).toMatchObject({ id: 'e:345', duration: 8000 })
    expect(chime.played).toEqual(['market'])

    await reconnect(socket)
    expect(api.eventsSince).toHaveBeenCalledWith(345, 200)
  })

  it('opens History from a test’s View', async () => {
    const socket = await joinedSocket()
    socket.fire(
      'sudarshan_market_alert',
      push({
        alert_id: 0,
        test: true,
        title: 'Test alert',
        detail: 'sent from the Alerts page',
        symbol: '',
        exchange: '',
      })
    )
    expect(shown()).toEqual([['Alert · Test alert', '10:42:29 · sent from the Alerts page']])
    toasts.info.mock.calls[0][1].action.onClick()
    expect(navigate).toHaveBeenCalledWith(`${ALERTS_PATH}?tab=history`)
  })

  it('sounds a pushed alert whatever the phone’s clock says of its time', async () => {
    const socket = await joinedSocket()
    // A phone clock 61 s fast: the alert fired now, by the server.
    vi.setSystemTime(NOW + 61_000)
    socket.fire('sudarshan_market_alert', push({ at_ms: NOW }))
    expect(toasts.info).toHaveBeenCalledTimes(1)
    expect(chime.played).toEqual(['market'])
  })

  it('follows the app’s master switches: no sound with sounds off, nothing with alerts off', async () => {
    const socket = await joinedSocket()
    useAlertStore.getState().setSoundEnabled(false)
    socket.fire('sudarshan_market_alert', push({ event_id: 346 }))
    expect(toasts.info).toHaveBeenCalledTimes(1)
    expect(chime.played).toEqual([])

    useAlertStore.getState().setToastsEnabled(false)
    socket.fire('sudarshan_market_alert', push({ event_id: 347 }))
    expect(toasts.info).toHaveBeenCalledTimes(1)

    // Alerts off with sounds on: still no sound.
    useAlertStore.getState().setSoundEnabled(true)
    socket.fire('sudarshan_market_alert', push({ event_id: 348 }))
    expect(chime.played).toEqual([])
  })

  it('shows three of a burst each, then one toast for the whole burst in their place', async () => {
    const socket = await joinedSocket()
    for (const id of [351, 352, 353]) {
      socket.fire('sudarshan_market_alert', push({ event_id: id, title: `Alert ${id}` }))
    }
    expect(shown().map(([title]) => title)).toEqual([
      'Alert · Alert 351',
      'Alert · Alert 352',
      'Alert · Alert 353',
    ])
    expect(toasts.dismiss).not.toHaveBeenCalled()

    socket.fire('sudarshan_market_alert', push({ event_id: 354, title: 'Alert 354' }))
    socket.fire('sudarshan_market_alert', push({ event_id: 355, title: 'Alert 355' }))

    // The toaster draws three: the fourth would hide the first, so one toast says them all.
    expect(toasts.dismiss.mock.calls.map(([id]) => id)).toEqual(['e:351', 'e:352', 'e:353'])
    expect(shown().slice(3)).toEqual([
      ['4 alerts fired', 'Latest 10:42:29 · Alert 354'],
      ['5 alerts fired', 'Latest 10:42:29 · Alert 355'],
    ])
    const ids = toasts.info.mock.calls.map(([, options]) => options.id)
    expect(ids[3]).toBe(ids[4])
    toasts.info.mock.calls[4][1].action.onClick()
    expect(navigate).toHaveBeenCalledWith(`${ALERTS_PATH}?tab=history`)
  })

  /** Four alerts, `gap` ms apart: whether they made one burst (one toast for all four). */
  async function oneBurst(gap: number) {
    const socket = await joinedSocket()
    for (const [index, id] of [361, 362, 363, 364].entries()) {
      vi.setSystemTime(NOW + index * gap)
      socket.fire('sudarshan_market_alert', push({ event_id: id, title: `Alert ${id}` }))
    }
    return shown().some(([title]) => title === '4 alerts fired')
  }

  it('takes alerts 100 ms apart as one burst', async () => {
    expect(await oneBurst(100)).toBe(true)
  })

  it('takes alerts exactly 300 ms apart as one burst: the window slides with each alert', async () => {
    expect(await oneBurst(300)).toBe(true)
  })

  it('takes alerts 301 ms apart as separate alerts, each with its own toast', async () => {
    expect(await oneBurst(301)).toBe(false)
    expect(toasts.info).toHaveBeenCalledTimes(4)
  })
})

describe('one announcement across the app’s tabs', () => {
  it('tells the other tabs what it showed', async () => {
    const socket = await joinedSocket()
    socket.fire('sudarshan_market_alert', push())
    expect(channel.sent).toContainEqual({ key: 'e:345', ids: [345] })
  })

  it('shows nothing another tab already showed, pushed or missed', async () => {
    const socket = await joinedSocket()
    channel.say({ key: 'e:345', ids: [345] })
    socket.fire('sudarshan_market_alert', push())

    channel.say({ key: 'm:350', ids: [349, 350] })
    api.eventsSince.mockResolvedValue(page([event({ id: 349 }), event({ id: 350 })]))
    await reconnect(socket)

    expect(toasts.info).not.toHaveBeenCalled()
  })

  it('waits, when another tab is the one in use, and stays quiet if that tab shows it', async () => {
    vi.mocked(document.hasFocus).mockReturnValue(false)
    const socket = await joinedSocket()
    channel.say({ inUse: 'other-tab' })

    socket.fire('sudarshan_market_alert', push())
    expect(toasts.info).not.toHaveBeenCalled()
    channel.say({ key: 'e:345', ids: [345] })
    await new Promise((resolve) => setTimeout(resolve, TAB_DEFER_MS + 50))
    expect(toasts.info).not.toHaveBeenCalled()

    socket.fire('sudarshan_market_alert', push({ event_id: 346 }))
    await new Promise((resolve) => setTimeout(resolve, TAB_DEFER_MS + 50))
    expect(toasts.info).toHaveBeenCalledTimes(1)
  })
})

describe('the Alerts page’s cache', () => {
  async function bound(initial = state()) {
    const client = new QueryClient()
    client.setQueryData(STATE_KEY, initial)
    const invalidate = vi.spyOn(client, 'invalidateQueries')
    bindMarketAlertsCache(client)
    const socket = await joinedSocket()
    const cached = () => client.getQueryData<MarketAlertsState>(STATE_KEY)
    return { client, socket, invalidate, cached }
  }

  const stopped = {
    id: 12,
    version: 3,
    status: 'stopped' as const,
    status_reason: 'Fired once at 10:42 on 28 Oct',
    check: null,
    fired_today: 3,
  }

  it('takes a status change of the same version as it comes', async () => {
    const { socket, invalidate, cached } = await bound()
    socket.fire('sudarshan_market_alerts_state', {
      engine: { code: 'ok', line: 'Watching · Real-time', real_time: true },
      alerts: [stopped],
      removed: [],
      server_time: NOW,
    })

    expect(cached()?.alerts[0]).toMatchObject(stopped)
    expect(invalidate).not.toHaveBeenCalled()
  })

  it('reads the state again for an alert it does not hold, an edit elsewhere, or the engine line alone', async () => {
    const { socket, invalidate, cached } = await bound()
    const engine = {
      code: 'outside_hours',
      line: 'Market closed · checks from Thu 09:15',
      real_time: false,
    }

    socket.fire('sudarshan_market_alerts_state', {
      engine,
      alerts: [{ ...stopped, id: 99 }],
      removed: [],
      server_time: NOW,
    })
    socket.fire('sudarshan_market_alerts_state', {
      engine,
      alerts: [{ ...stopped, version: 4 }],
      removed: [],
      server_time: NOW,
    })
    socket.fire('sudarshan_market_alerts_state', {
      engine,
      alerts: [],
      removed: [],
      server_time: NOW,
    })

    expect(invalidate).toHaveBeenCalledTimes(3)
    expect(cached()?.engine).toEqual(engine)
    // The newer version's own fields wait for the read; the cache keeps its version.
    expect(cached()?.alerts[0]).toMatchObject({ status: 'stopped', version: 3 })
  })

  it('drops an alert deleted on another screen, without reading the state again', async () => {
    const { socket, cached, invalidate } = await bound(state({ alerts: [row(), row({ id: 13 })] }))
    socket.fire('sudarshan_market_alerts_state', {
      engine: state().engine,
      alerts: [],
      removed: [12],
      server_time: NOW,
    })
    expect(cached()?.alerts.map((each) => each.id)).toEqual([13])
    expect(invalidate).not.toHaveBeenCalledWith({ queryKey: STATE_KEY })
  })

  it('counts each pushed alert once toward its fires today, and a test never', async () => {
    const { socket, cached } = await bound()
    socket.fire('sudarshan_market_alert', push())
    socket.fire('sudarshan_market_alert', push())
    // A test names no alert of its own; even one naming a real alert is never counted.
    socket.fire('sudarshan_market_alert', push({ event_id: 346, alert_id: 12, test: true }))

    expect(cached()?.alerts[0].fired_today).toBe(3)
  })

  it('counts the fire that stops a Once alert once: the state push before it already did', async () => {
    const { socket, cached } = await bound(
      state({ alerts: [row({ frequency: 'once', fired_today: 0 })] })
    )
    // The server sends the state push for that fire before the fire's own push.
    socket.fire('sudarshan_market_alerts_state', {
      engine: state().engine,
      alerts: [{ ...stopped, fired_today: 1 }],
      removed: [],
      server_time: NOW,
    })
    socket.fire('sudarshan_market_alert', push({ event_id: 345 }))

    expect(cached()?.alerts[0]).toMatchObject({ status: 'stopped', fired_today: 1 })
  })

  it('reads the state again for a fire on an alert it does not hold', async () => {
    const { socket, invalidate } = await bound()
    socket.fire('sudarshan_market_alert', push({ event_id: 346, alert_id: 99 }))
    expect(invalidate).toHaveBeenCalledWith({ queryKey: STATE_KEY })
  })

  it('keeps a fire counted while a read of the state is on its way that predates it', async () => {
    const { client, socket, cached } = await bound(state({ alerts: [row({ fired_today: 2 })] }))
    const observer = new QueryObserver(client, {
      queryKey: STATE_KEY,
      queryFn: () => api.state(),
      staleTime: Number.POSITIVE_INFINITY,
    })
    const unsubscribe = observer.subscribe(() => {})
    let answer: (value: MarketAlertsState) => void = () => {}
    // Read again, the server counts the third fire too.
    api.state.mockResolvedValue(state({ alerts: [row({ fired_today: 3 })] }))
    api.state.mockReturnValueOnce(
      new Promise<MarketAlertsState>((resolve) => {
        answer = resolve
      })
    )
    const reading = observer.refetch()
    socket.fire('sudarshan_market_alert', push({ event_id: 360 })) // the third fire today
    answer(state({ alerts: [row({ fired_today: 2 })] }))
    await reading
    await flush()
    await flush()
    unsubscribe()

    expect(cached()?.alerts[0].fired_today).toBe(3)
  })

  it('does not let a state push older than the read the cache holds undo it', async () => {
    const { socket, cached } = await bound(
      state({ server_time: NOW, alerts: [row({ fired_today: 3 })] })
    )
    socket.fire('sudarshan_market_alerts_state', {
      engine: {
        code: 'outside_hours',
        line: 'Market closed · checks from 09:15',
        real_time: false,
      },
      alerts: [
        { id: 12, version: 3, status: 'active', status_reason: '', check: null, fired_today: 2 },
      ],
      removed: [],
      server_time: NOW - 2_000,
    })

    expect(cached()?.alerts[0].fired_today).toBe(3)
    expect(cached()?.engine.code).toBe('ok')
  })

  it('applies a settled delivery to the cached History, and to a page whose read crossed it', async () => {
    const { client, socket } = await bound()
    const pending = event({
      id: 345,
      deliveries: {
        telegram: { status: 'pending', reason: '', attempts: 0, updated_at: NOW - 1_000 },
      },
    })
    client.setQueryData<InfiniteData<EventsPage, number | null>>(HISTORY_KEY, {
      pages: [page([pending])],
      pageParams: [null],
    })
    const sent = { status: 'sent' as const, reason: '', attempts: 1, updated_at: NOW }

    socket.fire('sudarshan_market_alert_delivery', {
      event_id: 345,
      channel: 'telegram',
      delivery: sent,
    })
    socket.fire('sudarshan_market_alert_delivery', {
      event_id: 999,
      channel: 'telegram',
      delivery: sent,
    })
    socket.fire('sudarshan_market_alert_delivery', {
      event_id: 345,
      channel: 'sms',
      delivery: sent,
    })

    const history = client.getQueryData<InfiniteData<EventsPage>>(HISTORY_KEY)
    expect(history?.pages[0].events).toHaveLength(1)
    expect(history?.pages[0].events[0].deliveries).toEqual({ telegram: sent })
    expect(withRecentDeliveries(page([pending])).events[0].deliveries.telegram).toEqual(sent)
  })
})
