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
  getMultiQuotes: vi.fn(),
  closePosition: vi.fn(),
  closeAllPositions: vi.fn(),
  contractOf: vi.fn(),
}))
vi.mock('@/api/trading', () => ({
  tradingApi: {
    getPositions: (...args: unknown[]) => api.getPositions(...args),
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

import { pass, renderBook, settle } from '@/test/bookPages'
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
    await open(LONG, PUT)
    await exitOnce(tap)
    expect(await exitOffered(tap)).toBe(false)

    await pass(25_000)
    expect(await exitOffered(tap)).toBe(false)
    expect(exitAll()).toBeDisabled()
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
    expect(exitAll()).toBeEnabled()

    // Refused in the body of an answer, the same.
    api.closePosition.mockResolvedValueOnce({ status: 'error', message: 'Market is closed' })
    await exitOnce(tap)
    expect(toast.error).toHaveBeenLastCalledWith('Market is closed', 'positions')
    expect(await exitOffered(tap)).toBe(true)
  })
})

describe('c: no answer is "not confirmed"', () => {
  it.each([
    ['the network dropped', () => new AxiosError('Network Error', AxiosError.ERR_NETWORK)],
    ['the request timed out', () => new AxiosError('timeout', AxiosError.ECONNABORTED)],
    ['a gateway answered', () => gateway(504)],
  ])('%s: held as if sent, said on the card, and no toast says it failed', async (_, error) => {
    const tap = user()
    api.closePosition.mockRejectedValueOnce(error())
    await open(LONG, PUT)
    await exitOnce(tap)

    expect(await exitOffered(tap)).toBe(false)
    expect(exitAll()).toBeDisabled()
    expect(card()).toHaveTextContent('Not confirmed: check Orders before sending again')
    expect(toast.error).not.toHaveBeenCalled()
    expect(toast.warning).toHaveBeenCalledTimes(1)
    const [said] = toast.warning.mock.calls[0]
    expect(said).toMatch(/not confirmed/)
    expect(said).not.toMatch(/fail/i)

    // Held 15 s from the answer, like one that went.
    await pass(14_000)
    expect(await exitOffered(tap)).toBe(false)
    await pass(1_500)
    expect(await exitOffered(tap)).toBe(true)
    expect(api.closePosition).toHaveBeenCalledTimes(1)
  })
})

describe('c: an answer that is not the app’s own', () => {
  it('(a page from something in between) leaves the exit not confirmed too', async () => {
    const tap = user()
    api.closePosition.mockResolvedValueOnce('<html><body>Bad Gateway</body></html>')
    await open(LONG, PUT)
    await exitOnce(tap)
    expect(await exitOffered(tap)).toBe(false)
    expect(card()).toHaveTextContent('Not confirmed: check Orders before sending again')
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
    expect(exitAll()).toBeDisabled()
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
    expect(exitAll()).toBeEnabled()
  })
})

describe('e: when its time runs out', () => {
  it('waits while a pushed order on the closing side works, then offers Exit with a warning', async () => {
    const tap = user()
    const { push } = await open(LONG, PUT)
    await exitOnce(tap)
    const exitOrder = {
      orderid: '251020000611111',
      symbol: CALL,
      exchange: 'NFO',
      product: 'MIS',
      action: 'SELL',
      quantity: 130,
      pricetype: 'MARKET',
    }
    push({ ...exitOrder, order_status: 'open' })

    await pass(16_000)
    expect(await exitOffered(tap)).toBe(false)

    // The order stops working (cancelled at the exchange): the hold has run its time.
    push({ ...exitOrder, order_status: 'cancelled' })
    await settle()
    expect(await exitOffered(tap)).toBe(true)

    await tap.click(card())
    await tap.click(within(screen.getByRole('dialog')).getByRole('button', { name: /^Exit/ }))
    expect(screen.getByRole('dialog', { name: 'Exit NIFTY 25900 CE?' })).toHaveTextContent(
      /The exit sent at 10:42:2\d has not shown in the book yet\. If that order is still open in Orders, this sends a second one\./
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
    expect(exitAll()).toBeDisabled()

    // Its clock is the answer's, not the reload's.
    await pass(15_500)
    expect(await exitOffered(tap)).toBe(true)
  })

  it('a reload with the exit still in flight brings it back as not confirmed', async () => {
    const tap = user()
    api.closePosition.mockReturnValue(new Promise(() => {}))
    const { view } = await open(LONG, PUT)
    await exitOnce(tap)
    view.unmount()

    const reloaded = await load()
    renderBook('/positions', reloaded.page)
    await settle()
    expect(await exitOffered(tap)).toBe(false)
    expect(card()).toHaveTextContent('Not confirmed: check Orders before sending again')
    await pass(15_500)
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
    expect(exitAll()).toBeDisabled()

    // Ten seconds on, half the call has closed and the put has not moved.
    await pass(10_000)
    api.getPositions.mockResolvedValue(book({ ...LONG, quantity: 65, pnl: 650 } as Position, PUT))
    await reread(tap)
    expect(exitAll()).toBeDisabled()

    // The put's time is up; the call's clock started again at its change.
    await pass(6_000)
    expect(exitAll()).toBeDisabled()
    expect(putCard()).not.toHaveTextContent('Exit sent')
    expect(card()).toHaveTextContent('Exit sent · 65 of 130 left')
    await pass(10_000)
    expect(exitAll()).toBeEnabled()
    expect(api.closeAllPositions).toHaveBeenCalledTimes(1)
  })

  it('with no answer holds every one as not confirmed', async () => {
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
    expect(exitAll()).toBeDisabled()
    expect(toast.error).not.toHaveBeenCalled()
    expect(card()).toHaveTextContent('Not confirmed: check Orders before sending again')
    expect(putCard()).toHaveTextContent('Not confirmed: check Orders before sending again')
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
    expect(exitAll()).toBeDisabled()
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
    expect(exitAll()).toBeDisabled()
    expect(card()).toHaveTextContent('Not confirmed: check Orders before sending again')
    expect(putCard()).toHaveTextContent('Not confirmed: check Orders before sending again')
    expect(toast.error).not.toHaveBeenCalled()
  })
})
