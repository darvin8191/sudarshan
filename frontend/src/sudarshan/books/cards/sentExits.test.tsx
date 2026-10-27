/**
 * How long a sent exit holds the phone's Exit (M6.9), through the real
 * Positions page. Upstox's position book can lag an exit, and /close_position
 * sizes its order from that book, so a second exit in that moment reverses the
 * position instead of closing it.
 *
 * Every test loads the app's modules afresh, as a reload does: a hold kept
 * anywhere but app-wide and in the tab's storage is lost the way a reload
 * loses it. Nothing here imports the hold itself, so these run against any
 * version of the cards.
 */
import { act, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { AxiosError, AxiosHeaders } from 'axios'
import type { ReactElement } from 'react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { Position } from '@/types/trading'

vi.mock('@/lib/MarketDataManager', () => import('@/test/marketDataHarness'))

const api = vi.hoisted(() => ({
  getPositions: vi.fn(),
  getOrders: vi.fn(),
  getMultiQuotes: vi.fn(),
  closePosition: vi.fn(),
  closeAllPositions: vi.fn(),
  contractOf: vi.fn(),
}))
vi.mock('@/api/trading', () => ({
  tradingApi: {
    getPositions: (...args: unknown[]) => api.getPositions(...args),
    getOrders: (...args: unknown[]) => api.getOrders(...args),
    getMultiQuotes: (...args: unknown[]) => api.getMultiQuotes(...args),
    closePosition: (...args: unknown[]) => api.closePosition(...args),
    closeAllPositions: (...args: unknown[]) => api.closeAllPositions(...args),
  },
}))
vi.mock('@/sudarshan/watchlist/api', () => ({
  contractOf: (...args: unknown[]) => api.contractOf(...args),
}))
vi.mock('@/stores/authStore', () => ({
  useAuthStore: () => ({ apiKey: 'test-api-key', user: { broker: 'upstox' } }),
}))
const theme = vi.hoisted(() => ({ appMode: 'live' }))
vi.mock('@/stores/themeStore', () => ({
  onModeChange: () => () => {},
  useThemeStore: (pick: (state: { appMode: string }) => unknown) => pick(theme),
}))
const toast = vi.hoisted(() => ({
  success: vi.fn(),
  error: vi.fn(),
  info: vi.fn(),
  warning: vi.fn(),
}))
vi.mock('@/utils/toast', () => ({ showToast: toast }))
vi.mock('@/hooks/useSupportedExchanges', () => ({
  useSupportedExchanges: () => ({ isCrypto: false }),
}))
vi.mock('@/hooks/useMarketStatus', () => ({
  useMarketStatus: () => ({
    isMarketOpen: () => true,
    isAnyMarketOpen: () => true,
    isLoading: false,
    error: null,
  }),
}))
vi.mock('@/components/socket/SocketProvider', () => ({
  useSocketContext: () => ({
    playAlertSound: () => {},
    socket: { on: () => undefined, off: () => undefined },
  }),
}))
vi.mock('@/components/trading/PlaceOrderDialog', () => ({ PlaceOrderDialog: () => null }))

import { expectAvailable, expectUnavailable, pass, renderBook, settle } from '@/test/bookPages'
import { fakeClock, frames } from '@/test/frames'
import { fakeScreen } from '@/test/screenWidth'

vi.setConfig({ testTimeout: 30_000 })

const CALL = 'NIFTY27OCT2625900CE'
/** Long two lots of a NIFTY call: 130. */
const LONG = {
  symbol: CALL,
  exchange: 'NFO',
  product: 'MIS',
  quantity: 130,
  average_price: 142.5,
  ltp: 152.5,
  pnl: 1300,
  pnlpercent: 7.02,
  realised: 0,
  unrealised: 1300,
} as Position
/** Short one lot of a BANKNIFTY put: -30. */
const PUT = {
  symbol: 'BANKNIFTY27OCT2657500PE',
  exchange: 'NFO',
  product: 'NRML',
  quantity: -30,
  average_price: 228.4,
  ltp: 212.3,
  pnl: 483,
  pnlpercent: 7.05,
  realised: 0,
  unrealised: 483,
} as Position

const book = (...positions: Position[]) => ({ status: 'success', data: positions })
const card = () => screen.getByRole('button', { name: /NIFTY 25900 CE/ })
const putCard = () => screen.getByRole('button', { name: /BANKNIFTY 57500 PE/ })
const exitAll = () => screen.getByRole('button', { name: 'Exit all' })
const user = () => userEvent.setup({ advanceTimers: vi.advanceTimersByTime })
type Tap = ReturnType<typeof user>

/** An answer from the app itself, refusing. */
function refusal(status: number, message: string) {
  return new AxiosError('Request failed', 'ERR_BAD_REQUEST', undefined, undefined, {
    status,
    statusText: '',
    headers: {},
    config: { headers: new AxiosHeaders() },
    data: { status: 'error', message },
  })
}

/** An answer from the app that may have reached the broker: a 5xx, or one it marks so. */
function notConfirmedAnswer(status: number, data: object) {
  return new AxiosError('Request failed', 'ERR_BAD_RESPONSE', undefined, undefined, {
    status,
    statusText: '',
    headers: {},
    config: { headers: new AxiosHeaders() },
    data,
  })
}

/** The exit's own order, as the broker pushes it and its order book lists it. */
const EXIT_ORDER = {
  orderid: '251020000611111',
  symbol: CALL,
  exchange: 'NFO',
  product: 'MIS',
  action: 'SELL',
  quantity: 130,
  price: 0,
  trigger_price: 0,
  pricetype: 'MARKET',
  timestamp: '20-Oct-2026 10:42:22',
}
const orderBook = (...orders: object[]) => ({
  status: 'success',
  data: { orders, statistics: {} },
})

/** An answer from something between the phone and the app: a gateway's page. */
function gateway(status: number) {
  return new AxiosError('Request failed', 'ERR_BAD_RESPONSE', undefined, undefined, {
    status,
    statusText: '',
    headers: {},
    config: { headers: new AxiosHeaders() },
    data: '<html><body>Gateway Time-out</body></html>',
  })
}

/**
 * The page as a fresh load of the app has it: every module evaluated again,
 * the tab's storage kept. Also the shell's order memory of that load, which
 * the shell's listener feeds with every frame pushed.
 */
async function load(): Promise<{ page: ReactElement; push: (frame: object) => void }> {
  vi.resetModules()
  // The fresh tap guard, off as the setup turns it off (where there is one).
  const guardPath = '@/sudarshan/watchlist/openGuard'
  const guard = await import(/* @vite-ignore */ guardPath).catch(() => null)
  if (guard) guard.OPEN_GUARD.ms = 0
  const { default: Positions } = await import('@/pages/Positions')
  const { notePushedFrame } = await import('@/sudarshan/books/orderFold')
  return {
    page: <Positions />,
    push: (frame) => act(() => notePushedFrame({ mode: 'live', ...frame })),
  }
}

async function open(...positions: Position[]) {
  api.getPositions.mockResolvedValue(book(...positions))
  const loaded = await load()
  const view = renderBook('/positions', loaded.page)
  await settle()
  return { ...loaded, view }
}

/** Exit the call: its card, the sheet's Exit, the confirmation's Exit. */
async function exitOnce(tap: Tap) {
  await tap.click(card())
  await tap.click(within(screen.getByRole('dialog')).getByRole('button', { name: /^Exit/ }))
  await tap.click(
    within(screen.getByRole('dialog', { name: 'Exit NIFTY 25900 CE?' })).getByRole('button', {
      name: 'Exit',
    })
  )
  await settle()
}

/** Whether the call's sheet offers Exit now (the sheet is opened and shut again). */
async function exitOffered(tap: Tap): Promise<boolean> {
  await tap.click(card())
  const exit = within(screen.getByRole('dialog')).getByRole('button', { name: /^Exit/ })
  const offered = !(exit as HTMLButtonElement).disabled
  await tap.click(within(screen.getByRole('dialog')).getByRole('button', { name: 'Close' }))
  await settle()
  return offered
}

async function reread(tap: Tap) {
  await tap.click(screen.getByRole('button', { name: 'Refresh' }))
  await settle()
}

beforeEach(() => {
  fakeClock()
  theme.appMode = 'live'
  // 10:42:21 in India.
  vi.setSystemTime(new Date('2026-10-20T05:12:21Z'))
  frames.install()
  fakeScreen('phone')
  sessionStorage.clear()
  localStorage.clear()
  api.getPositions.mockReset()
  api.getMultiQuotes.mockReset().mockResolvedValue({ status: 'success', results: [] })
  api.closePosition.mockReset().mockResolvedValue({ status: 'success', message: 'Closed' })
  api.closeAllPositions.mockReset().mockResolvedValue({ status: 'success' })
  api.getOrders.mockReset().mockResolvedValue(orderBook())
  api.contractOf.mockReset().mockResolvedValue({ lotSize: 65, tickSize: 0.05 })
  for (const each of Object.values(toast)) each.mockReset()
})

afterEach(() => {
  vi.useRealTimers()
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
})

describe('a: the hold starts at the tap', () => {
  it('and never runs out while the exit is still in flight, however long', async () => {
    const tap = user()
    let answer: (value: unknown) => void = () => {}
    api.closePosition.mockReturnValue(
      new Promise((resolve) => {
        answer = resolve
      })
    )
    const { push } = await open(LONG, PUT)
    await exitOnce(tap)
    expect(await exitOffered(tap)).toBe(false)

    await pass(25_000)
    // News of another order 25 s after the tap: the exit in flight still holds.
    push({
      ...EXIT_ORDER,
      orderid: '251020000699999',
      symbol: 'SBIN',
      exchange: 'NSE',
      order_status: 'complete',
    })
    await settle()
    expect(await exitOffered(tap)).toBe(false)
    expectUnavailable(exitAll())
    expect(card()).toHaveTextContent('Sending exit')

    // The answer, at last: the clock starts now, not at the tap.
    await act(async () => answer({ status: 'success', message: 'Closed' }))
    await settle()
    expect(card()).toHaveTextContent('Exit sent')
    await pass(14_000)
    expect(await exitOffered(tap)).toBe(false)
    await pass(1_500)
    expect(await exitOffered(tap)).toBe(true)
    expect(api.closePosition).toHaveBeenCalledTimes(1)
  })
})

describe('b: a refusal from the server', () => {
  it('lets go at once and shows the reason it gave', async () => {
    const tap = user()
    api.closePosition.mockRejectedValueOnce(refusal(400, 'Exit is not allowed on this product now'))
    await open(LONG, PUT)
    await exitOnce(tap)
    expect(toast.error).toHaveBeenCalledWith('Exit is not allowed on this product now', 'positions')
    expect(card()).not.toHaveTextContent('Exit sent')
    expect(await exitOffered(tap)).toBe(true)
    expectAvailable(exitAll())

    // Refused in the body of an answer, the same.
    api.closePosition.mockResolvedValueOnce({ status: 'error', message: 'Market is closed' })
    await exitOnce(tap)
    expect(toast.error).toHaveBeenLastCalledWith('Market is closed', 'positions')
    expect(await exitOffered(tap)).toBe(true)
  })
})

const NO_ANSWER_EXIT =
  'No answer came for this exit: it may still be going through. Wait for Positions or Orders to show it before sending another.'

describe('c: no answer at all', () => {
  it.each([
    ['the network dropped', () => new AxiosError('Network Error', AxiosError.ERR_NETWORK)],
    ['the request timed out', () => new AxiosError('timeout', AxiosError.ECONNABORTED)],
    ['a gateway answered', () => gateway(504)],
  ])('%s: held 150 s from the tap, said on the card, and no toast says it failed', async (_, error) => {
    const tap = user()
    api.closePosition.mockRejectedValueOnce(error())
    await open(LONG, PUT)
    await exitOnce(tap)

    expect(await exitOffered(tap)).toBe(false)
    expectUnavailable(exitAll())
    expect(card()).toHaveTextContent('No answer yet: it may still be going through')
    expect(toast.error).not.toHaveBeenCalled()
    expect(toast.warning).toHaveBeenCalledTimes(1)
    expect(toast.warning).toHaveBeenCalledWith(NO_ANSWER_EXIT, 'positions')

    // Not 15 s: the server may still be placing it (one Upstox call can take 120 s).
    await pass(60_000)
    expect(await exitOffered(tap)).toBe(false)
    await pass(88_000)
    expect(await exitOffered(tap)).toBe(false)
    await pass(2_500)
    expect(await exitOffered(tap)).toBe(true)
    expect(api.closePosition).toHaveBeenCalledTimes(1)
  })

  it('lets go sooner when the book shows the position closed', async () => {
    const tap = user()
    api.closePosition.mockRejectedValueOnce(new AxiosError('timeout', AxiosError.ECONNABORTED))
    await open(LONG, PUT)
    await exitOnce(tap)
    await pass(20_000)
    api.getPositions.mockResolvedValue(book({ ...LONG, quantity: 0, pnl: 1300 } as Position, PUT))
    await reread(tap)
    expect(screen.queryByText(/No answer yet/)).toBeNull()
    expectAvailable(exitAll())
  })
})

describe('c: an answer that is not the app’s own', () => {
  it('(a page from something in between) holds as no answer too', async () => {
    const tap = user()
    api.closePosition.mockResolvedValueOnce('<html><body>Bad Gateway</body></html>')
    await open(LONG, PUT)
    await exitOnce(tap)
    expect(await exitOffered(tap)).toBe(false)
    expect(card()).toHaveTextContent('No answer yet: it may still be going through')
    expect(toast.error).not.toHaveBeenCalled()
  })
})

describe('d: after it went, only flat, gone or turned lets it go', () => {
  it('a part closing keeps the hold, starts its clock again, and says what is left', async () => {
    const tap = user()
    await open(LONG, PUT)
    await exitOnce(tap)
    await pass(10_000)

    // 65 of the 130 filled (price protection left the rest working).
    api.getPositions.mockResolvedValue(book({ ...LONG, quantity: 65, pnl: 650 } as Position, PUT))
    await reread(tap)
    expect(await exitOffered(tap)).toBe(false)
    expectUnavailable(exitAll())
    expect(card()).toHaveTextContent('Exit sent · 65 of 130 left')

    // 23 s after the answer: still held, 13 s after the change.
    await pass(13_000)
    expect(await exitOffered(tap)).toBe(false)
    await pass(2_500)
    expect(await exitOffered(tap)).toBe(true)
    expect(api.closePosition).toHaveBeenCalledTimes(1)
  })

  it.each([
    ['flat', [{ ...LONG, quantity: 0, pnl: 1300 }, PUT]],
    ['gone from the book', [PUT]],
    ['turned the other way', [{ ...LONG, quantity: -65 }, PUT]],
  ])('%s lets it go at once', async (_, after) => {
    const tap = user()
    await open(LONG, PUT)
    await exitOnce(tap)
    expect(card()).toHaveTextContent('Exit sent')

    api.getPositions.mockResolvedValue(book(...(after as Position[])))
    await reread(tap)
    expect(screen.queryByText(/Exit sent/)).toBeNull()
    expectAvailable(exitAll())
  })
})

describe('e: when its time runs out', () => {
  it('waits while a pushed order on the closing side works, then offers Exit with a warning', async () => {
    const tap = user()
    const { push } = await open(LONG, PUT)
    await exitOnce(tap)
    push({ ...EXIT_ORDER, order_status: 'open' })
    api.getOrders.mockResolvedValue(orderBook({ ...EXIT_ORDER, order_status: 'open' }))

    await pass(16_000)
    expect(await exitOffered(tap)).toBe(false)

    // The order stops working (cancelled at the exchange): the hold has run its time.
    push({ ...EXIT_ORDER, order_status: 'cancelled' })
    await settle()
    expect(await exitOffered(tap)).toBe(true)

    await tap.click(card())
    await tap.click(within(screen.getByRole('dialog')).getByRole('button', { name: /^Exit/ }))
    expect(screen.getByRole('dialog', { name: 'Exit NIFTY 25900 CE?' })).toHaveTextContent(
      /Orders shows the exit sent at 10:42:2\d cancelled: it closed nothing\. This sends a new exit\./
    )
  })
})

describe('f: the hold lives app-wide', () => {
  it('a turn of the phone, a remount, and a reload inside it keep it', async () => {
    const tap = user()
    const screenWidth = fakeScreen('phone')
    const { page, view } = await open(LONG, PUT)
    await exitOnce(tap)

    act(() => screenWidth.turn('wide'))
    await settle()
    act(() => screenWidth.turn('phone'))
    await settle()
    expect(await exitOffered(tap)).toBe(false)

    // To Orders and back: the page goes and comes again.
    view.unmount()
    const again = renderBook('/positions', page)
    await settle()
    expect(await exitOffered(tap)).toBe(false)
    again.unmount()
    // A reload: every module evaluated again, the tab's storage what is left.
    const reloaded = await load()
    renderBook('/positions', reloaded.page)
    await settle()
    expect(card()).toHaveTextContent('Exit sent')
    expect(await exitOffered(tap)).toBe(false)
    expectUnavailable(exitAll())

    // Its clock is the answer's, not the reload's.
    await pass(15_500)
    expect(await exitOffered(tap)).toBe(true)
  })

  it('a reload with the exit still in flight brings it back as no answer, 150 s from the tap', async () => {
    const tap = user()
    api.closePosition.mockReturnValue(new Promise(() => {}))
    const { view } = await open(LONG, PUT)
    await exitOnce(tap)
    await pass(10_000)
    view.unmount()

    const reloaded = await load()
    renderBook('/positions', reloaded.page)
    await settle()
    expect(await exitOffered(tap)).toBe(false)
    expect(card()).toHaveTextContent('No answer yet: it may still be going through')
    await pass(15_500)
    expect(await exitOffered(tap)).toBe(false)
    await pass(126_000)
    expect(await exitOffered(tap)).toBe(true)
  })

  it('still holds in memory when the tab refuses its storage', async () => {
    const tap = user()
    const refuse = () => {
      throw new DOMException('The quota has been exceeded.', 'QuotaExceededError')
    }
    vi.stubGlobal('sessionStorage', { getItem: refuse, setItem: refuse, removeItem: refuse })
    await open(LONG, PUT)
    await exitOnce(tap)
    expect(card()).toHaveTextContent('Exit sent')
    expect(await exitOffered(tap)).toBe(false)
  })
})

describe('the two books', () => {
  it('a switch of mode alone says nothing about the other book’s exits', async () => {
    const tap = user()
    // In Analyze mode: the sandbox's book, and an exit from it.
    theme.appMode = 'analyzer'
    await open(LONG, PUT)
    await exitOnce(tap)

    // Live: the broker's book, which has no such call.
    theme.appMode = 'live'
    api.getPositions.mockResolvedValue(book(PUT))
    await reread(tap)
    expect(screen.queryByRole('button', { name: /NIFTY 25900 CE/ })).toBeNull()

    // Back to Analyze, its book still on the way: the broker's book on screen
    // must not count as the sandbox's.
    theme.appMode = 'analyzer'
    let answer: (value: unknown) => void = () => {}
    api.getPositions.mockReturnValue(
      new Promise((resolve) => {
        answer = resolve
      })
    )
    await reread(tap)
    await act(async () => answer(book(LONG, PUT)))
    await settle()
    expect(await exitOffered(tap)).toBe(false)
    expect(card()).toHaveTextContent('Exit sent')
  })
})

describe('h: Exit all', () => {
  it('holds every open position, each by the same rules', async () => {
    const tap = user()
    await open(LONG, PUT)
    await tap.click(exitAll())
    await tap.click(
      within(screen.getByRole('dialog', { name: 'Exit all positions?' })).getByRole('button', {
        name: 'Exit all',
      })
    )
    await settle()
    expect(api.closeAllPositions).toHaveBeenCalledTimes(1)
    expect(card()).toHaveTextContent('Exit sent')
    expect(putCard()).toHaveTextContent('Exit sent')
    expectUnavailable(exitAll())

    // Ten seconds on, half the call has closed and the put has not moved.
    await pass(10_000)
    api.getPositions.mockResolvedValue(book({ ...LONG, quantity: 65, pnl: 650 } as Position, PUT))
    await reread(tap)
    expectUnavailable(exitAll())

    // The put's time is up; the call's clock started again at its change.
    await pass(6_000)
    expectUnavailable(exitAll())
    expect(putCard()).not.toHaveTextContent('Exit sent')
    expect(card()).toHaveTextContent('Exit sent · 65 of 130 left')
    await pass(10_000)
    expectAvailable(exitAll())
    expect(api.closeAllPositions).toHaveBeenCalledTimes(1)
  })

  it('with no answer holds every one as no answer yet', async () => {
    const tap = user()
    api.closeAllPositions.mockRejectedValueOnce(
      new AxiosError('Network Error', AxiosError.ERR_NETWORK)
    )
    await open(LONG, PUT)
    await tap.click(exitAll())
    await tap.click(
      within(screen.getByRole('dialog', { name: 'Exit all positions?' })).getByRole('button', {
        name: 'Exit all',
      })
    )
    await settle()
    expectUnavailable(exitAll())
    expect(toast.error).not.toHaveBeenCalled()
    expect(card()).toHaveTextContent('No answer yet: it may still be going through')
    expect(putCard()).toHaveTextContent('No answer yet: it may still be going through')
  })
})

describe('the wide table on the same page', () => {
  it('sends no second exit while the phone’s is held: a phone on its side shows the table', async () => {
    const tap = user()
    const screenWidth = fakeScreen('phone')
    await open(LONG, PUT)
    await exitOnce(tap)
    expect(api.closePosition).toHaveBeenCalledTimes(1)

    act(() => screenWidth.turn('wide'))
    await settle()
    await tap.click(screen.getByRole('button', { name: `Close ${CALL} position` }))
    await settle()
    expect(api.closePosition).toHaveBeenCalledTimes(1)
    expect(toast.warning).toHaveBeenLastCalledWith(
      expect.stringMatching(/^An exit for NIFTY 25900 CE was sent at 10:42:2\d and has not shown/),
      'positions'
    )

    await tap.click(screen.getByRole('button', { name: /Close All/ }))
    await tap.click(screen.getByRole('button', { name: 'Close All' }))
    await settle()
    expect(api.closeAllPositions).not.toHaveBeenCalled()
  })
})

/** The confirmation of the call's exit, opened from its sheet. */
async function openExitConfirmation(tap: Tap): Promise<HTMLElement> {
  await tap.click(card())
  await tap.click(within(screen.getByRole('dialog')).getByRole('button', { name: /^Exit/ }))
  return screen.getByRole('dialog', { name: 'Exit NIFTY 25900 CE?' })
}

const EXIT_NOT_CONFIRMED =
  'This exit is not confirmed: it may have gone through. Check Orders before sending it again.'

describe('i: an answer that may have reached the broker is not confirmed', () => {
  it.each([
    [
      'the app’s own JSON 500 (Upstox took it, then the read timed out)',
      () => notConfirmedAnswer(500, { status: 'error', message: 'The read operation timed out' }),
    ],
    [
      'the app’s 502: Upstox took it without naming an order',
      () =>
        notConfirmedAnswer(502, {
          status: 'error',
          message: EXIT_NOT_CONFIRMED,
          not_confirmed: true,
        }),
    ],
  ])('%s: held, the toast in the app’s words, and no second exit', async (_, error) => {
    const tap = user()
    api.closePosition.mockRejectedValueOnce(error())
    await open(LONG, PUT)
    await exitOnce(tap)

    expect(card()).toHaveTextContent('Not confirmed: check Orders before sending again')
    expect(await exitOffered(tap)).toBe(false)
    expectUnavailable(exitAll())
    expect(toast.error).not.toHaveBeenCalled()
    expect(toast.warning).toHaveBeenCalledTimes(1)
    expect(toast.warning).toHaveBeenCalledWith(EXIT_NOT_CONFIRMED, 'positions')
    expect(api.closePosition).toHaveBeenCalledTimes(1)
  })

  it('Exit all that sent some exits before the rest were refused holds every one', async () => {
    const tap = user()
    api.closeAllPositions.mockRejectedValueOnce(
      notConfirmedAnswer(429, {
        status: 'error',
        message: '1 of 2 open positions were not squared off.',
        not_confirmed: true,
      })
    )
    await open(LONG, PUT)
    await tap.click(exitAll())
    await tap.click(
      within(screen.getByRole('dialog', { name: 'Exit all positions?' })).getByRole('button', {
        name: 'Exit all',
      })
    )
    await settle()
    expectUnavailable(exitAll())
    expect(card()).toHaveTextContent('Not confirmed: check Orders before sending again')
    expect(putCard()).toHaveTextContent('Not confirmed: check Orders before sending again')
    expect(toast.error).not.toHaveBeenCalled()
  })
})

describe('j: a tab restored after the hold’s time', () => {
  it('offers Exit at once, saying the earlier exit has not shown', async () => {
    const tap = user()
    const { view } = await open(LONG, PUT)
    await exitOnce(tap)
    await pass(2_000)
    view.unmount()

    // Discarded 2 s after the answer, restored 75 s later: no timer of the old page ran.
    vi.setSystemTime(Date.now() + 75_000)
    const reloaded = await load()
    renderBook('/positions', reloaded.page)
    await settle()
    expect(card()).not.toHaveTextContent('Exit sent')
    expectAvailable(exitAll())
    expect(await openExitConfirmation(tap)).toHaveTextContent(
      /The exit sent at 10:42:2\d has not shown in the book yet\./
    )
    expect(api.closePosition).toHaveBeenCalledTimes(1)
  })

  it('keeps an exit its own order holds, until a read of the order book says how it stands', async () => {
    const tap = user()
    const { view, push } = await open(LONG, PUT)
    await exitOnce(tap)
    push({ ...EXIT_ORDER, order_status: 'open' })
    await settle()
    view.unmount()

    vi.setSystemTime(Date.now() + 75_000)
    let answer: (value: unknown) => void = () => {}
    api.getOrders.mockReturnValue(
      new Promise((resolve) => {
        answer = resolve
      })
    )
    const reloaded = await load()
    renderBook('/positions', reloaded.page)
    await settle()
    // The reload forgot every push: until a read says otherwise, its order counts as working.
    expect(card()).toHaveTextContent('Exit order open · check Orders')
    expect(await exitOffered(tap)).toBe(false)
    expect(api.getOrders).toHaveBeenCalledTimes(1)

    await act(async () => answer(orderBook({ ...EXIT_ORDER, order_status: 'rejected' })))
    await settle()
    expect(card()).not.toHaveTextContent('Exit order open')
    expect(await openExitConfirmation(tap)).toHaveTextContent(
      /Orders shows the exit sent at 10:42:2\d rejected: it closed nothing\. This sends a new exit\./
    )
  })
})

describe('k: an exit held by its own order, still working', () => {
  it('says so, reads the order book at 15 s and every 30 s, and a read settles it', async () => {
    const tap = user()
    const { push } = await open(LONG, PUT)
    await exitOnce(tap)
    push({ ...EXIT_ORDER, order_status: 'open' })
    api.getOrders.mockResolvedValue(orderBook({ ...EXIT_ORDER, order_status: 'open' }))
    await pass(14_000)
    expect(card()).toHaveTextContent('Exit sent')
    expect(api.getOrders).not.toHaveBeenCalled()

    await pass(1_500)
    expect(card()).toHaveTextContent('Exit order open · check Orders')
    await tap.click(card())
    const sheetExit = within(screen.getByRole('dialog')).getByRole('button', { name: /^Exit/ })
    expect(sheetExit).toBeDisabled()
    expect(sheetExit).toHaveTextContent('Exit order open · check Orders')
    await tap.click(within(screen.getByRole('dialog')).getByRole('button', { name: 'Close' }))
    await settle()
    expect(api.getOrders).toHaveBeenCalledTimes(1)
    await pass(29_000)
    expect(api.getOrders).toHaveBeenCalledTimes(1)
    await pass(1_500)
    expect(api.getOrders).toHaveBeenCalledTimes(2)

    // A price-protected market order can rest unfilled: no clock cuts the hold short.
    await pass(5 * 60_000)
    expect(await exitOffered(tap)).toBe(false)
    expectUnavailable(exitAll())
    expect(card()).toHaveTextContent('Exit order open · check Orders')

    // Its "rejected" push was lost in a reconnect; the next read has it.
    api.getOrders.mockResolvedValue(orderBook({ ...EXIT_ORDER, order_status: 'rejected' }))
    await pass(30_000)
    expect(card()).not.toHaveTextContent('Exit order open')
    expect(await exitOffered(tap)).toBe(true)
    expect(api.closePosition).toHaveBeenCalledTimes(1)
  })
})

describe('l: the wide table', () => {
  const close = () => screen.getByRole('button', { name: `Close ${CALL} position` })

  it('tells the hold every book it reads: Close All goes once a Close shows flat', async () => {
    const tap = user()
    fakeScreen('wide')
    await open(LONG, PUT)
    await tap.click(close())
    await settle()
    expect(api.closePosition).toHaveBeenCalledTimes(1)

    api.getPositions.mockResolvedValue(book({ ...LONG, quantity: 0, pnl: 1300 } as Position, PUT))
    await reread(tap)
    await tap.click(screen.getByRole('button', { name: /Close All/ }))
    await tap.click(screen.getByRole('button', { name: 'Close All' }))
    await settle()
    expect(api.closeAllPositions).toHaveBeenCalledTimes(1)
    expect(toast.warning).not.toHaveBeenCalled()
  })

  it('offers Close again after a part close asks first, with the phone’s warning', async () => {
    const tap = user()
    fakeScreen('wide')
    await open(LONG, PUT)
    await tap.click(close())
    await settle()
    api.getPositions.mockResolvedValue(book({ ...LONG, quantity: 65, pnl: 650 } as Position, PUT))
    await reread(tap)
    await pass(16_000)

    await tap.click(close())
    await settle()
    expect(api.closePosition).toHaveBeenCalledTimes(1)
    const asked = screen.getByRole('dialog', { name: 'Close NIFTY 25900 CE?' })
    expect(asked).toHaveTextContent(
      /The exit sent at 10:42:2\d has closed 65 of 130 so far\. If the rest is still open in Orders, this sends a second order for it\./
    )
    expect(asked).toHaveTextContent(/Sells 65 \(MIS\) at market price/)
    await tap.click(within(asked).getByRole('button', { name: 'Close' }))
    await settle()
    expect(api.closePosition).toHaveBeenCalledTimes(2)
  })

  it('shuts that question once the book shows the position closed, sending nothing', async () => {
    const tap = user()
    fakeScreen('wide')
    await open(LONG, PUT)
    await tap.click(close())
    await settle()
    await pass(16_000)
    await tap.click(close())
    await settle()
    expect(screen.getByRole('dialog', { name: 'Close NIFTY 25900 CE?' })).toBeInTheDocument()

    // The page's next read: the earlier exit filled after all.
    api.getPositions.mockResolvedValue(book({ ...LONG, quantity: 0, pnl: 1300 } as Position, PUT))
    await pass(46_000)
    expect(screen.queryByRole('dialog', { name: 'Close NIFTY 25900 CE?' })).toBeNull()
    expect(api.closePosition).toHaveBeenCalledTimes(1)

    // A new position in the same contract later: the question never comes back by itself.
    api.getPositions.mockResolvedValue(book(LONG, PUT))
    await pass(46_000)
    expect(close()).toBeInTheDocument()
    expect(screen.queryByRole('dialog', { name: 'Close NIFTY 25900 CE?' })).toBeNull()
  })

  it('says in Close All’s confirmation what became of an exit whose time ran out', async () => {
    const tap = user()
    fakeScreen('wide')
    await open(LONG, PUT)
    await tap.click(close())
    await settle()
    await pass(16_000)
    await tap.click(screen.getByRole('button', { name: /Close All/ }))
    expect(screen.getByRole('dialog', { name: 'Close All Positions?' })).toHaveTextContent(
      /The exit sent at 10:42:2\d has not shown in the book yet\. If that order is still open in Orders, this sends a second one\./
    )
  })
})

describe('m: a confirmation tapped once its position is held again', () => {
  it('sends nothing, and says why', async () => {
    const tap = user()
    await open(LONG, PUT)
    await exitOnce(tap)
    await pass(16_000)
    const confirmation = await openExitConfirmation(tap)
    expect(confirmation).toHaveTextContent(/has not shown in the book yet/)

    // While it is open, the page's next read shows the earlier exit working: held again.
    api.getPositions.mockResolvedValue(book({ ...LONG, quantity: 65, pnl: 650 } as Position, PUT))
    await pass(46_000)
    // The card, behind the open confirmation.
    expect(screen.getByRole('button', { name: /NIFTY 25900 CE/, hidden: true })).toHaveTextContent(
      'Exit sent · 65 of 130 left'
    )
    await tap.click(within(confirmation).getByRole('button', { name: 'Exit' }))
    await settle()
    expect(api.closePosition).toHaveBeenCalledTimes(1)
    expect(toast.warning).toHaveBeenLastCalledWith(
      expect.stringMatching(
        /^An exit for NIFTY 25900 CE was sent at 10:42:2\d and has closed 65 of 130 so far\. Check Orders before sending another\.$/
      ),
      'positions'
    )
  })
})

describe('m: Exit all’s confirmation tapped once an exit is held again', () => {
  it('sends nothing, and says why', async () => {
    const tap = user()
    await open(LONG, PUT)
    await exitOnce(tap)
    await pass(16_000)
    await tap.click(exitAll())
    const confirmation = screen.getByRole('dialog', { name: 'Exit all positions?' })

    api.getPositions.mockResolvedValue(book({ ...LONG, quantity: 65, pnl: 650 } as Position, PUT))
    await pass(46_000)
    await tap.click(within(confirmation).getByRole('button', { name: 'Exit all' }))
    await settle()
    expect(api.closeAllPositions).not.toHaveBeenCalled()
    expect(toast.warning).toHaveBeenLastCalledWith(
      expect.stringMatching(/^An exit for NIFTY 25900 CE was sent at 10:42:2\d and has closed 65/),
      'positions'
    )
  })
})

describe('n: an exit whose answer named its order', () => {
  it('is held by that order alone, not by a stop-loss moved on its side', async () => {
    const tap = user()
    api.closePosition.mockResolvedValueOnce({
      status: 'success',
      message: 'Closed',
      orderid: EXIT_ORDER.orderid,
    })
    const { push } = await open(LONG, PUT)
    await exitOnce(tap)
    push({ ...EXIT_ORDER, order_status: 'complete' })
    // The trader's stop-loss on the same contract, moved after the exit went, and still
    // working in the order book: taken for the exit's own, it would hold the card.
    const stopLoss = {
      ...EXIT_ORDER,
      orderid: '251020000699001',
      pricetype: 'SL-M',
      order_status: 'trigger pending',
    }
    push(stopLoss)
    api.getOrders.mockResolvedValue(
      orderBook({ ...EXIT_ORDER, order_status: 'complete' }, stopLoss)
    )
    await pass(16_000)
    await pass(31_000) // an order book read, had anything held it by its order
    expect(card()).not.toHaveTextContent('Exit order open')
    expect(await exitOffered(tap)).toBe(true)
  })
})

describe('o: an order book read that failed', () => {
  it('says nothing about the exit’s own order: it stays held', async () => {
    const tap = user()
    const { push } = await open(LONG, PUT)
    await exitOnce(tap)
    push({ ...EXIT_ORDER, order_status: 'open' })
    api.getOrders.mockResolvedValue({ status: 'error', message: 'Could not read orders' })
    await pass(16_000)
    expect(api.getOrders).toHaveBeenCalled()
    expect(card()).toHaveTextContent('Exit order open · check Orders')
    expect(await exitOffered(tap)).toBe(false)
  })
})

describe('p: an answer the app marks not confirmed, whatever its code', () => {
  it('a 2xx marked so holds the exit as not confirmed, in the app’s words', async () => {
    const tap = user()
    const words =
      'Upstox took this exit without naming its order: it may have gone through. Check Orders before sending it again.'
    api.closePosition.mockResolvedValueOnce({
      status: 'error',
      message: words,
      not_confirmed: true,
    })
    await open(LONG, PUT)
    await exitOnce(tap)
    expect(card()).toHaveTextContent('Not confirmed: check Orders before sending again')
    expect(toast.warning).toHaveBeenCalledWith(words, 'positions')
    expect(await exitOffered(tap)).toBe(false)
    expect(toast.error).not.toHaveBeenCalled()
  })
})

describe('q: Exit all’s result shows once', () => {
  it('its refusal shares the toast the server’s close event shows for it', async () => {
    const tap = user()
    api.closeAllPositions.mockRejectedValueOnce(
      notConfirmedAnswer(424, {
        status: 'error',
        message:
          'Nothing was sent: the positions could not be read from Upstox. Check Positions and try again.',
      })
    )
    await open(LONG, PUT)
    await tap.click(exitAll())
    await tap.click(
      within(screen.getByRole('dialog', { name: 'Exit all positions?' })).getByRole('button', {
        name: 'Exit all',
      })
    )
    await settle()
    expect(toast.error).toHaveBeenCalledWith(
      'Nothing was sent: the positions could not be read from Upstox. Check Positions and try again.',
      'positions',
      { id: 'sudarshan-exit-all-result' }
    )
    expectAvailable(exitAll())
  })
})

describe('r: the wide table’s question, once its position is held again', () => {
  it('sends nothing, and says why', async () => {
    const tap = user()
    fakeScreen('wide')
    await open(LONG, PUT)
    const close = () => screen.getByRole('button', { name: `Close ${CALL} position` })
    await tap.click(close())
    await settle()
    await pass(16_000)
    await tap.click(close())
    await settle()
    const asked = screen.getByRole('dialog', { name: 'Close NIFTY 25900 CE?' })

    // While it asks, the page's next read shows the earlier exit working: held again.
    api.getPositions.mockResolvedValue(book({ ...LONG, quantity: 65, pnl: 650 } as Position, PUT))
    await pass(46_000)
    await tap.click(within(asked).getByRole('button', { name: 'Close' }))
    await settle()
    expect(api.closePosition).toHaveBeenCalledTimes(1)
    expect(toast.warning).toHaveBeenLastCalledWith(
      expect.stringMatching(
        /^An exit for NIFTY 25900 CE was sent at 10:42:2\d and has closed 65 of 130 so far\./
      ),
      'positions'
    )
  })
})

const STILL_PLACING =
  'An exit for this position is still being placed. Wait for it, then check Orders.'
const EXIT_ALL_STILL_PLACING = 'Exit all is still being placed. Wait for it, then check Orders.'

describe('s: the app is still placing an earlier exit (its 409)', () => {
  it('holds the row again as that exit, says so, and warns at the next confirmation', async () => {
    const tap = user()
    // An exit answered without naming its order; its hold runs out with the book unchanged.
    await open(LONG, PUT)
    await exitOnce(tap)
    await pass(16_000)
    expect(await exitOffered(tap)).toBe(true)

    api.closePosition.mockRejectedValueOnce(refusal(409, STILL_PLACING))
    await exitOnce(tap)
    expect(toast.error).toHaveBeenLastCalledWith(STILL_PLACING, 'positions')
    expect(card()).toHaveTextContent('Still being placed: wait for it, then check Orders')
    expect(await exitOffered(tap)).toBe(false)
    expectUnavailable(exitAll())

    // Its hold over, Exit is offered again with a warning that says what the app said.
    await pass(16_000)
    expect(await exitOffered(tap)).toBe(true)
    await tap.click(card())
    await tap.click(within(screen.getByRole('dialog')).getByRole('button', { name: /^Exit/ }))
    expect(screen.getByRole('dialog', { name: 'Exit NIFTY 25900 CE?' })).toHaveTextContent(
      /An exit for this position was still being placed at 10:42:\d\d and has not shown in the book yet\./
    )
    expect(api.closePosition).toHaveBeenCalledTimes(2)
  })

  it('holds every row again when Exit all is refused for one still being placed', async () => {
    const tap = user()
    api.closeAllPositions.mockRejectedValueOnce(
      new AxiosError('Network Error', AxiosError.ERR_NETWORK)
    )
    await open(LONG, PUT)
    const exitAllOnce = async () => {
      await tap.click(exitAll())
      await tap.click(
        within(screen.getByRole('dialog', { name: 'Exit all positions?' })).getByRole('button', {
          name: 'Exit all',
        })
      )
      await settle()
    }
    await exitAllOnce()
    await pass(151_000)
    expectAvailable(exitAll())

    api.closeAllPositions.mockRejectedValueOnce(refusal(409, EXIT_ALL_STILL_PLACING))
    await exitAllOnce()
    expect(toast.error).toHaveBeenLastCalledWith(EXIT_ALL_STILL_PLACING, 'positions', {
      id: 'sudarshan-exit-all-result',
    })
    expect(card()).toHaveTextContent('Still being placed')
    expect(putCard()).toHaveTextContent('Still being placed')
    expectUnavailable(exitAll())
    await pass(16_000)
    expectAvailable(exitAll())
    expect(api.closeAllPositions).toHaveBeenCalledTimes(2)
  })

  it('holds a row the app is placing an exit for, though this phone sent none', async () => {
    const tap = user()
    // Telegram's /closeall, say, is being placed when the phone's Exit arrives.
    api.closePosition.mockRejectedValueOnce(refusal(409, EXIT_ALL_STILL_PLACING))
    await open(LONG, PUT)
    await exitOnce(tap)
    expect(card()).toHaveTextContent('Still being placed')
    expect(await exitOffered(tap)).toBe(false)
    await pass(16_000)
    expect(await exitOffered(tap)).toBe(true)
  })

  it('a refusal for another reason puts the earlier exit back as it was, warning and all', async () => {
    const tap = user()
    await open(LONG, PUT)
    await exitOnce(tap)
    await pass(16_000)

    api.closePosition.mockRejectedValueOnce(refusal(400, 'Market is closed'))
    await exitOnce(tap)
    expect(toast.error).toHaveBeenLastCalledWith('Market is closed', 'positions')
    expect(card()).not.toHaveTextContent('Still being placed')
    expect(await exitOffered(tap)).toBe(true)
    await tap.click(card())
    await tap.click(within(screen.getByRole('dialog')).getByRole('button', { name: /^Exit/ }))
    expect(screen.getByRole('dialog', { name: 'Exit NIFTY 25900 CE?' })).toHaveTextContent(
      /The exit sent at 10:42:2\d has not shown in the book yet\./
    )
  })
})

describe('t: an exit the broker sliced', () => {
  it('holds until the last of its orders has ended', async () => {
    const tap = user()
    const SLICE = { ...EXIT_ORDER, orderid: '251020000611112', quantity: 65 }
    api.closePosition.mockResolvedValueOnce({
      status: 'success',
      message: 'Closed',
      orderid: EXIT_ORDER.orderid,
      orderids: [EXIT_ORDER.orderid, SLICE.orderid],
    })
    const { push } = await open(LONG, PUT)
    await exitOnce(tap)
    push({ ...EXIT_ORDER, quantity: 65, order_status: 'complete' })
    push({ ...SLICE, order_status: 'open' })
    api.getOrders.mockResolvedValue(
      orderBook(
        { ...EXIT_ORDER, quantity: 65, order_status: 'complete' },
        { ...SLICE, order_status: 'open' }
      )
    )
    await pass(16_000)
    expect(card()).toHaveTextContent('Exit order open')
    expect(await exitOffered(tap)).toBe(false)

    push({ ...SLICE, order_status: 'complete' })
    await settle()
    expect(await exitOffered(tap)).toBe(true)
  })
})

describe('u: Exit all says what became of each position', () => {
  const PUT_STOP =
    'BANKNIFTY 57500 PE has a BUY 30 SL-M order (trigger 240.00, order ending 3456, placed 10:42:20) working to close it, so no exit was sent for it. Cancel it in Orders, then exit.'
  const CALL_LATE =
    'Upstox was slow to answer, so Exit all stopped before sending an exit for it. Exit it again.'
  const each = (call: object, put: object) => [
    { exchange: 'NFO', symbol: CALL, product: 'MIS', ...call },
    { exchange: 'NFO', symbol: PUT.symbol, product: 'NRML', ...put },
  ]

  async function exitAllTap(tap: Tap) {
    await tap.click(exitAll())
    await tap.click(
      within(screen.getByRole('dialog', { name: 'Exit all positions?' })).getByRole('button', {
        name: 'Exit all',
      })
    )
    await settle()
  }

  /** Whether a card's sheet offers Exit now (the sheet is opened and shut again). */
  async function offered(tap: Tap, which: () => HTMLElement): Promise<boolean> {
    await tap.click(which())
    const exit = within(screen.getByRole('dialog')).getByRole('button', { name: /^Exit/ })
    const free = !(exit as HTMLButtonElement).disabled
    await tap.click(within(screen.getByRole('dialog')).getByRole('button', { name: 'Close' }))
    await settle()
    return free
  }

  it('one held back by a working order lets go and says why; the one sent holds', async () => {
    const tap = user()
    const message = `Exit all did not close every position. Of 2 exits, 1 was sent and 1 was not sent. ${PUT_STOP}`
    api.closeAllPositions.mockRejectedValueOnce(
      notConfirmedAnswer(409, {
        status: 'error',
        message,
        not_confirmed: true,
        positions: each(
          { outcome: 'sent', orderids: [EXIT_ORDER.orderid] },
          { outcome: 'not_sent', reason: PUT_STOP }
        ),
      })
    )
    await open(LONG, PUT)
    await exitAllTap(tap)
    expect(toast.warning).toHaveBeenLastCalledWith(message, 'positions', {
      id: 'sudarshan-exit-all-result',
    })
    expect(card()).toHaveTextContent('Exit sent')
    expect(putCard()).toHaveTextContent(PUT_STOP)
    expect(putCard()).not.toHaveTextContent('Still being placed')
    expect(putCard().querySelector('[data-sent="not-sent"]')).not.toBeNull()
    expect(await offered(tap, putCard)).toBe(true)
    expect(await exitOffered(tap)).toBe(false)
    expectUnavailable(exitAll()) // the call's exit holds it
  })

  it('nothing sent for any lets every one go, each with its own reason', async () => {
    const tap = user()
    api.closeAllPositions.mockRejectedValueOnce(
      refusalWith(409, {
        status: 'error',
        message: `Exit all did not close every position. Of 2 exits, 1 was not sent and 1 was not sent in time. ${PUT_STOP}`,
        positions: each(
          { outcome: 'not_sent', reason: CALL_LATE },
          { outcome: 'not_sent', reason: PUT_STOP }
        ),
      })
    )
    await open(LONG, PUT)
    await exitAllTap(tap)
    expect(card()).toHaveTextContent(CALL_LATE)
    expect(putCard()).toHaveTextContent(PUT_STOP)
    expect(card()).not.toHaveTextContent('Still being placed')
    expect(putCard()).not.toHaveTextContent('Still being placed')
    expectAvailable(exitAll())
    expect(await exitOffered(tap)).toBe(true)
    expect(await offered(tap, putCard)).toBe(true)

    // The reasons are for a while: a minute on, the cards are as before.
    await pass(61_000)
    expect(card()).not.toHaveTextContent(CALL_LATE)
    expect(putCard()).not.toHaveTextContent(PUT_STOP)
  })

  it('a new tap on a position replaces what Exit all said of it', async () => {
    const tap = user()
    api.closeAllPositions.mockRejectedValueOnce(
      refusalWith(409, {
        status: 'error',
        message: 'Exit all did not close every position.',
        positions: each(
          { outcome: 'not_sent', reason: CALL_LATE },
          { outcome: 'not_sent', reason: PUT_STOP }
        ),
      })
    )
    await open(LONG, PUT)
    await exitAllTap(tap)
    await exitOnce(tap)
    expect(card()).not.toHaveTextContent(CALL_LATE)
    expect(card()).toHaveTextContent('Exit sent')
    expect(putCard()).toHaveTextContent(PUT_STOP)

    // A tap the app refuses lets go too, and what Exit all said of that row does not come back.
    api.closePosition.mockRejectedValueOnce(refusal(400, 'Market is closed'))
    await tap.click(putCard())
    await tap.click(within(screen.getByRole('dialog')).getByRole('button', { name: /^Exit/ }))
    await tap.click(
      within(screen.getByRole('dialog', { name: 'Exit BANKNIFTY 57500 PE?' })).getByRole('button', {
        name: 'Exit',
      })
    )
    await settle()
    expect(toast.error).toHaveBeenLastCalledWith('Market is closed', 'positions')
    expect(putCard()).not.toHaveTextContent(PUT_STOP)
  })

  it('a success names each position’s orders: each is held by its own', async () => {
    const tap = user()
    api.closeAllPositions.mockResolvedValueOnce({
      status: 'success',
      message: 'All Open Positions Squared Off',
      positions: each(
        { outcome: 'sent', orderids: [EXIT_ORDER.orderid] },
        { outcome: 'sent', orderids: ['251020000622222'] }
      ),
    })
    const { push } = await open(LONG, PUT)
    await exitAllTap(tap)
    expect(card()).toHaveTextContent('Exit sent')
    expect(putCard()).toHaveTextContent('Exit sent')
    // The call's own order is still open when its time is up: it holds the call alone.
    push({ ...EXIT_ORDER, order_status: 'open' })
    api.getOrders.mockResolvedValue(orderBook({ ...EXIT_ORDER, order_status: 'open' }))
    await pass(16_000)
    expect(card()).toHaveTextContent('Exit order open')
    expect(putCard()).not.toHaveTextContent('Exit')
  })

  it('a success pins each position to its own order: a stop moved on its side holds nothing', async () => {
    const tap = user()
    api.closeAllPositions.mockResolvedValueOnce({
      status: 'success',
      message: 'All Open Positions Squared Off',
      positions: each(
        { outcome: 'sent', orderids: [EXIT_ORDER.orderid] },
        { outcome: 'sent', orderids: ['251020000622222'] }
      ),
    })
    const { push } = await open(LONG, PUT)
    await exitAllTap(tap)
    push({ ...EXIT_ORDER, order_status: 'complete' })
    const stopLoss = {
      ...EXIT_ORDER,
      orderid: '251020000699001',
      pricetype: 'SL-M',
      order_status: 'trigger pending',
    }
    push(stopLoss)
    api.getOrders.mockResolvedValue(
      orderBook({ ...EXIT_ORDER, order_status: 'complete' }, stopLoss)
    )
    await pass(16_000)
    await pass(31_000)
    expect(card()).not.toHaveTextContent('Exit order open')
    expect(await exitOffered(tap)).toBe(true)
  })

  it('one not confirmed holds as not confirmed, the other as sent', async () => {
    const tap = user()
    api.closeAllPositions.mockRejectedValueOnce(
      notConfirmedAnswer(502, {
        status: 'error',
        message:
          'Exit all did not close every position. Of 2 exits, 1 was sent and 1 is not confirmed and may have gone through.',
        not_confirmed: true,
        positions: each(
          { outcome: 'not_confirmed', reason: EXIT_NOT_CONFIRMED },
          { outcome: 'sent', orderids: ['251020000622222'] }
        ),
      })
    )
    await open(LONG, PUT)
    await exitAllTap(tap)
    expect(card()).toHaveTextContent('Not confirmed: check Orders before sending again')
    expect(putCard()).toHaveTextContent('Exit sent')
    expectUnavailable(exitAll())
  })

  it('a position the answer does not name settles by the answer as a whole', async () => {
    const tap = user()
    api.closeAllPositions.mockRejectedValueOnce(
      refusalWith(409, {
        status: 'error',
        message: 'Exit all did not close every position.',
        positions: [{ exchange: 'NFO', symbol: CALL, product: 'MIS', outcome: 'not_sent' }],
      })
    )
    await open(LONG, PUT)
    await exitAllTap(tap)
    // Nothing went: both let go; the call had no reason given, so none is shown.
    expectAvailable(exitAll())
    expect(card().querySelector('[data-sent="not-sent"]')).toBeNull()
    expect(putCard().querySelector('[data-sent]')).toBeNull()
  })

  it('a tap after an unanswered Exit all’s hold, while the server still places it, says so', async () => {
    const tap = user()
    api.closeAllPositions.mockRejectedValueOnce(
      new AxiosError('Network Error', AxiosError.ERR_NETWORK)
    )
    await open(LONG, PUT)
    await exitAllTap(tap)
    await pass(151_000) // the phone holds an unanswered Exit all 150 s; the server may run 277 s
    expect(await exitOffered(tap)).toBe(true)
    api.closePosition.mockRejectedValueOnce(refusal(409, EXIT_ALL_STILL_PLACING))
    await exitOnce(tap)
    expect(toast.error).toHaveBeenLastCalledWith(EXIT_ALL_STILL_PLACING, 'positions')
    expect(card()).toHaveTextContent('Still being placed')
    expect(api.closePosition).toHaveBeenCalledTimes(1)
  })
})

/** A refusal from the app with a body of its own (Exit all's answer). */
function refusalWith(status: number, data: object) {
  return new AxiosError('Request failed', 'ERR_BAD_REQUEST', undefined, undefined, {
    status,
    statusText: '',
    headers: {},
    config: { headers: new AxiosHeaders() },
    data,
  })
}
