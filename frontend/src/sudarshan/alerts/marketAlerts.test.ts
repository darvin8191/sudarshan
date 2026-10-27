/**
 * Market alerts in the open app (M10.7): the join on every connect with its ack, what fired
 * while the app was away (one toast for one, one toast for several, from the newest when the
 * device has seen none), the pushed alert's toast and sound (none for one a minute old, none with
 * the master switch off), one announcement across the app's tabs, a burst, and the Alerts page's
 * cache kept current by the state and delivery pushes. The sound itself is chime.test.ts's.
 */
import { type InfiniteData, QueryClient } from '@tanstack/react-query'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const toasts = vi.hoisted(() => ({ info: vi.fn() }))
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
  installMarketAlerts,
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
const SEEN_KEY = 'sudarshan-market-alerts-seen'

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

/** Connected, joined, and caught up. */
async function joinedSocket() {
  const socket = install()
  socket.connect()
  socket.ack({ ok: true })
  await flush()
  return socket
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
  chime.played.length = 0
  api.state.mockReset()
  api.eventsSince.mockReset().mockResolvedValue(page([]))
  localStorage.clear()
  localStorage.setItem(SEEN_KEY, '340')
  useAlertStore.getState().resetToDefaults()
  vi.spyOn(document, 'hasFocus').mockReturnValue(true)
})

afterEach(() => {
  vi.useRealTimers()
  vi.restoreAllMocks()
  localStorage.clear()
})

describe('joining the owner’s room', () => {
  it('joins on connect, and asks for what fired after the last alert seen here once acked', async () => {
    const socket = install()
    socket.connect()

    expect(socket.joins).toBe(1)
    expect(api.eventsSince).not.toHaveBeenCalled()
    socket.ack({ ok: true })
    await flush()

    expect(api.eventsSince).toHaveBeenCalledWith(340, 200)
  })

  it('joins again on a reconnect, and reads the page’s state and an open History again', async () => {
    const client = new QueryClient()
    client.setQueryData(STATE_KEY, state())
    const invalidate = vi.spyOn(client, 'invalidateQueries')
    bindMarketAlertsCache(client)
    const socket = await joinedSocket()
    expect(invalidate).not.toHaveBeenCalled()

    socket.drop()
    socket.connect()
    socket.ack({ ok: true })
    await flush()

    expect(socket.joins).toBe(2)
    expect(api.eventsSince).toHaveBeenCalledTimes(2)
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

    expect(api.eventsSince).not.toHaveBeenCalled()
  })

  it('does not join twice when installed again on a connection already joined', async () => {
    const socket = await joinedSocket()
    // A layout swap: a new provider installs on the same live connection.
    installMarketAlerts(socket as never, (name, handler) => socket.on(name, handler), navigate)
    expect(socket.joins).toBe(1)
  })
})

describe('what fired while the app was away', () => {
  it('starts from the newest on a device that has seen none, telling nothing', async () => {
    localStorage.clear()
    api.state.mockResolvedValue(state({ last_event_id: 341 }))
    await joinedSocket()

    expect(localStorage.getItem(SEEN_KEY)).toBe('341')
    expect(api.eventsSince).not.toHaveBeenCalled()
    expect(toasts.info).not.toHaveBeenCalled()
  })

  it('does not start from a state that is switched off or starting', async () => {
    localStorage.clear()
    api.state.mockResolvedValue(
      state({
        enabled: false,
        alerts: [],
        last_event_id: 0,
        engine: { code: 'off', line: 'Switched off on the server', real_time: false },
      })
    )
    await joinedSocket()

    expect(localStorage.getItem(SEEN_KEY)).toBeNull()
  })

  it('gives one missed alert the toast a pushed one gets, silent once a minute old', async () => {
    api.eventsSince.mockResolvedValue(page([event({ note: 'book half' })]))
    await joinedSocket()

    expect(shown()).toEqual([
      ['Alert · SBIN crossed above ₹100.00', '10:37:31 · now ₹100.05 · Note: book half'],
    ])
    expect(chime.played).toEqual([])
    expect(localStorage.getItem(SEEN_KEY)).toBe('341')

    toasts.info.mock.calls[0][1].action.onClick()
    expect(navigate).toHaveBeenCalledWith(`${ALERTS_PATH}?alert=12`)
  })

  it('sounds a missed alert that fired in the last minute', async () => {
    api.eventsSince.mockResolvedValue(page([event({ at: NOW - 20_000 })]))
    await joinedSocket()
    expect(chime.played).toEqual(['market'])
  })

  it('says the day of a missed alert from an earlier day', async () => {
    api.eventsSince.mockResolvedValue(page([event({ at: NOW - 86_400_000 })]))
    await joinedSocket()
    expect(shown()[0][1]).toBe('27 Oct 10:42:31 · now ₹100.05')
  })

  it('gives several one toast, with View to History, leaving out tests and the app turned off', async () => {
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
    await joinedSocket()

    expect(shown()).toEqual([
      ['2 alerts fired while the app was away', 'Latest 10:40:31 · NIFTY crossed below 25,800.00'],
    ])
    expect(chime.played).toEqual([])
    expect(localStorage.getItem(SEEN_KEY)).toBe('344')
    toasts.info.mock.calls[0][1].action.onClick()
    expect(navigate).toHaveBeenCalledWith(`${ALERTS_PATH}?tab=history`)
  })

  it('reads page after page while the server says there is more', async () => {
    api.eventsSince
      .mockResolvedValueOnce(page([event({ id: 341 }), event({ id: 342 })], true))
      .mockResolvedValueOnce(page([event({ id: 343 })]))
    await joinedSocket()

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

    socket.drop()
    socket.connect()
    socket.ack({ ok: true })
    await flush()

    expect(toasts.info).not.toHaveBeenCalled()
  })

  it('notes nothing when the connection drops during the read, so the next connect tells it', async () => {
    let answer: (value: EventsPage) => void = () => {}
    api.eventsSince.mockReturnValueOnce(
      new Promise<EventsPage>((resolve) => {
        answer = resolve
      })
    )
    const socket = install()
    socket.connect()
    socket.ack({ ok: true })
    socket.drop()
    answer(page([event()]))
    await flush()
    expect(toasts.info).not.toHaveBeenCalled()
    expect(localStorage.getItem(SEEN_KEY)).toBe('340')

    api.eventsSince.mockResolvedValue(page([event()]))
    socket.connect()
    socket.ack({ ok: true })
    await flush()
    expect(toasts.info).toHaveBeenCalledTimes(1)
  })

  it('stays quiet when the read fails, and asks again on the next connect', async () => {
    api.eventsSince.mockRejectedValueOnce(new Error('The alert service is not answering.'))
    const socket = await joinedSocket()
    expect(toasts.info).not.toHaveBeenCalled()

    api.eventsSince.mockResolvedValue(page([event()]))
    socket.drop()
    socket.connect()
    socket.ack({ ok: true })
    await flush()

    expect(api.eventsSince).toHaveBeenLastCalledWith(340, 200)
    expect(toasts.info).toHaveBeenCalledTimes(1)
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
    expect(localStorage.getItem(SEEN_KEY)).toBe('345')
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

  it('makes no sound for an alert that fired more than a minute ago', async () => {
    const socket = await joinedSocket()
    socket.fire('sudarshan_market_alert', push({ at_ms: NOW - 61_000 }))
    expect(toasts.info).toHaveBeenCalledTimes(1)
    expect(chime.played).toEqual([])
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
  })

  it('shows the first three of a burst and one toast for the rest', async () => {
    const socket = await joinedSocket()
    for (const id of [351, 352, 353, 354, 355]) {
      socket.fire('sudarshan_market_alert', push({ event_id: id, title: `Alert ${id}` }))
    }

    const titles = shown().map(([title]) => title)
    expect(titles.slice(0, 3)).toEqual([
      'Alert · Alert 351',
      'Alert · Alert 352',
      'Alert · Alert 353',
    ])
    expect(titles.slice(3)).toEqual(['1 more alert fired', '2 more alerts fired'])
    const ids = toasts.info.mock.calls.map(([, options]) => options.id)
    expect(ids[3]).toBe(ids[4])
    expect(shown()[4][1]).toBe('Latest 10:42:29 · Alert 355')
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
    socket.drop()
    socket.connect()
    socket.ack({ ok: true })
    await flush()

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

  it('drops an alert deleted on another screen', async () => {
    const { socket, cached } = await bound(state({ alerts: [row(), row({ id: 13 })] }))
    socket.fire('sudarshan_market_alerts_state', {
      engine: state().engine,
      alerts: [],
      removed: [12],
      server_time: NOW,
    })
    expect(cached()?.alerts.map((each) => each.id)).toEqual([13])
  })

  it('counts each pushed alert once toward its fires today, and a test never', async () => {
    const { socket, cached } = await bound()
    socket.fire('sudarshan_market_alert', push())
    socket.fire('sudarshan_market_alert', push())
    socket.fire('sudarshan_market_alert', push({ event_id: 346, alert_id: 0, test: true }))

    expect(cached()?.alerts[0].fired_today).toBe(3)
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

    const history = client.getQueryData<InfiniteData<EventsPage>>(HISTORY_KEY)
    expect(history?.pages[0].events).toHaveLength(1)
    expect(history?.pages[0].events[0].deliveries.telegram).toEqual(sent)
    expect(withRecentDeliveries(page([pending])).events[0].deliveries.telegram).toEqual(sent)
  })
})
