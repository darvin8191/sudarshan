/**
 * How long a sent cancel holds the phone's Cancel and Modify (M6.9), through
 * the real order book page: until its order shows a final status, pushed or
 * read, or the server refuses it; with no answer it is "not confirmed"; after
 * 15 s with the order still working, "Cancel again", with a short warning.
 *
 * Every test loads the app's modules afresh, as a reload does, so a hold kept
 * anywhere but app-wide is lost the way a reload loses it. Nothing here imports
 * the hold itself, so these run against any version of the cards.
 */
import { act, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { AxiosError, AxiosHeaders } from 'axios'
import type { ReactElement } from 'react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { Order } from '@/types/trading'

vi.mock('@/lib/MarketDataManager', () => import('@/test/marketDataHarness'))

const api = vi.hoisted(() => ({
  getOrders: vi.fn(),
  getMultiQuotes: vi.fn(),
  cancelOrder: vi.fn(),
  cancelAllOrders: vi.fn(),
  modifyOrder: vi.fn(),
}))
vi.mock('@/api/trading', () => ({
  tradingApi: {
    getOrders: (...args: unknown[]) => api.getOrders(...args),
    getMultiQuotes: (...args: unknown[]) => api.getMultiQuotes(...args),
    getQuotes: vi.fn().mockResolvedValue({ status: 'success', data: {} }),
    getDepth: vi.fn().mockResolvedValue({ status: 'success', data: { bids: [], asks: [] } }),
    cancelOrder: (...args: unknown[]) => api.cancelOrder(...args),
    cancelAllOrders: (...args: unknown[]) => api.cancelAllOrders(...args),
    modifyOrder: (...args: unknown[]) => api.modifyOrder(...args),
  },
}))
vi.mock('@/components/trading/GttTab', async () => {
  const { createElement } = await import('react')
  return { default: () => createElement('p', null, 'GTT book') }
})
vi.mock('@/stores/authStore', () => ({
  useAuthStore: () => ({ apiKey: 'test-api-key', user: { broker: 'upstox' } }),
}))
vi.mock('@/stores/themeStore', () => ({
  onModeChange: () => () => {},
  useThemeStore: (pick: (state: { appMode: string }) => unknown) => pick({ appMode: 'live' }),
}))
vi.mock('@/hooks/useSupportedExchanges', () => ({
  useSupportedExchanges: () => ({ isCrypto: false }),
}))
const toast = vi.hoisted(() => ({
  success: vi.fn(),
  error: vi.fn(),
  info: vi.fn(),
  warning: vi.fn(),
}))
vi.mock('@/utils/toast', () => ({ showToast: toast }))
vi.mock('@/hooks/useMarketStatus', () => ({
  useMarketStatus: () => ({
    isMarketOpen: () => true,
    isAnyMarketOpen: () => true,
    isLoading: false,
    error: null,
  }),
}))
const socket = vi.hoisted(() => {
  const handlers = new Map<string, Set<(...args: unknown[]) => void>>()
  return {
    on(event: string, handler: (...args: unknown[]) => void) {
      handlers.set(event, (handlers.get(event) ?? new Set()).add(handler))
      return this
    },
    off(event: string, handler: (...args: unknown[]) => void) {
      handlers.get(event)?.delete(handler)
      return this
    },
    fire(event: string, ...args: unknown[]) {
      for (const handler of [...(handlers.get(event) ?? [])]) handler(...args)
    },
    clear: () => handlers.clear(),
  }
})
vi.mock('@/components/socket/SocketProvider', () => ({
  useSocketContext: () => ({ playAlertSound: () => {}, socket }),
}))

import { pass, renderBook, settle } from '@/test/bookPages'
import { fakeClock, frames } from '@/test/frames'
import { fakeScreen } from '@/test/screenWidth'

vi.setConfig({ testTimeout: 30_000 })

/** A NIFTY call sold at 168, waiting. */
const WORKING = {
  symbol: 'NIFTY27OCT2625900CE',
  exchange: 'NFO',
  action: 'SELL',
  quantity: 65,
  price: 168,
  trigger_price: 0,
  pricetype: 'LIMIT',
  product: 'MIS',
  orderid: '1001',
  order_status: 'open',
  timestamp: '2026-10-20 10:41:51',
} as Order

const book = (...orders: Order[]) => ({
  status: 'success',
  data: { orders, statistics: {} },
})
const user = () => userEvent.setup({ advanceTimers: vi.advanceTimersByTime })
type Tap = ReturnType<typeof user>

function orderCard() {
  const item = document.querySelector('li[data-order="1001"]')
  if (!item) throw new Error('no card for order 1001')
  return within(item as HTMLElement).getByRole('button')
}

/**
 * The page as a fresh load of the app has it, and a push as the app receives
 * one: the shell's listener records it, the page folds it in.
 */
async function load(): Promise<{ page: ReactElement; push: (frame: object) => void }> {
  vi.resetModules()
  const guardPath = '@/sudarshan/watchlist/openGuard'
  const guard = await import(/* @vite-ignore */ guardPath).catch(() => null)
  if (guard) guard.OPEN_GUARD.ms = 0
  const { default: OrderBook } = await import('@/pages/OrderBook')
  const { notePushedFrame } = await import('@/sudarshan/books/orderFold')
  return {
    page: <OrderBook />,
    push: (frame) => {
      const full = { mode: 'live', ...frame }
      act(() => {
        notePushedFrame(full)
        socket.fire('order_update', full)
      })
    },
  }
}

async function open(...orders: Order[]) {
  api.getOrders.mockResolvedValue(book(...orders))
  const loaded = await load()
  const view = renderBook('/orderbook', loaded.page)
  await settle()
  return { ...loaded, view }
}

async function cancelOnce(tap: Tap) {
  await tap.click(orderCard())
  await tap.click(within(screen.getByRole('dialog')).getByRole('button', { name: 'Cancel order' }))
  await tap.click(
    within(screen.getByRole('dialog', { name: /^Cancel this order/ })).getByRole('button', {
      name: 'Cancel order',
    })
  )
  await settle()
}

/** The sheet's Cancel and Modify as they stand (the sheet is opened and shut again). */
async function sheetButtons(
  tap: Tap
): Promise<{ cancel: string; offered: boolean; modify: boolean }> {
  await tap.click(orderCard())
  const sheet = screen.getByRole('dialog')
  // Modify, then Cancel: the two that open a step of their own.
  const [modify, cancel] = [...sheet.querySelectorAll('button[aria-haspopup="dialog"]')]
  const out = {
    cancel: cancel.textContent ?? '',
    offered: !(cancel as HTMLButtonElement).disabled,
    modify: !(modify as HTMLButtonElement).disabled,
  }
  await tap.click(within(sheet).getByRole('button', { name: 'Close' }))
  await settle()
  return out
}

beforeEach(() => {
  fakeClock()
  // 10:42:21 in India.
  vi.setSystemTime(new Date('2026-10-20T05:12:21Z'))
  frames.install()
  fakeScreen('phone')
  socket.clear()
  sessionStorage.clear()
  api.getOrders.mockReset()
  api.getMultiQuotes.mockReset().mockResolvedValue({ status: 'success', results: [] })
  api.cancelOrder.mockReset().mockResolvedValue({ status: 'success' })
  api.cancelAllOrders.mockReset().mockResolvedValue({ status: 'success', message: 'Done' })
  api.modifyOrder.mockReset().mockResolvedValue({ status: 'success' })
  for (const each of Object.values(toast)) each.mockReset()
})

afterEach(() => {
  vi.useRealTimers()
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
})

describe('g: a cancel sent', () => {
  it('holds Cancel and Modify until its order shows a final status pushed', async () => {
    const tap = user()
    const { push } = await open(WORKING)
    await cancelOnce(tap)
    expect(await sheetButtons(tap)).toEqual({
      cancel: 'Cancel sent',
      offered: false,
      modify: false,
    })

    push({ orderid: '1001', order_status: 'cancelled' })
    await settle()
    expect(orderCard()).not.toHaveTextContent('Cancel sent')
    expect(api.cancelOrder).toHaveBeenCalledTimes(1)
  })

  it('holds while in flight past 15 s, and for as long as the answer says after it', async () => {
    const tap = user()
    let answer: (value: unknown) => void = () => {}
    api.cancelOrder.mockReturnValue(
      new Promise((resolve) => {
        answer = resolve
      })
    )
    await open(WORKING)
    await cancelOnce(tap)
    await pass(20_000)
    expect((await sheetButtons(tap)).offered).toBe(false)
    await act(async () => answer({ status: 'success' }))
    await settle()
    expect(orderCard()).toHaveTextContent('Cancel sent')
  })

  it('lets go at once on a refusal, showing the reason the server gave', async () => {
    const tap = user()
    api.cancelOrder.mockRejectedValueOnce(
      new AxiosError('Request failed', 'ERR_BAD_REQUEST', undefined, undefined, {
        status: 400,
        statusText: '',
        headers: {},
        config: { headers: new AxiosHeaders() },
        data: { status: 'error', message: 'Order is already complete' },
      })
    )
    await open(WORKING)
    await cancelOnce(tap)
    expect(toast.error).toHaveBeenCalledWith('Order is already complete', 'orders')
    expect((await sheetButtons(tap)).offered).toBe(true)
  })

  it('with no answer is held 150 s from the tap, and no toast says it failed', async () => {
    const tap = user()
    api.cancelOrder.mockRejectedValueOnce(new AxiosError('Network Error', AxiosError.ERR_NETWORK))
    await open(WORKING)
    await cancelOnce(tap)
    expect((await sheetButtons(tap)).offered).toBe(false)
    expect(orderCard()).toHaveTextContent('No answer yet: it may still be going through')
    expect(toast.error).not.toHaveBeenCalled()
    expect(toast.warning).toHaveBeenCalledTimes(1)
    expect(toast.warning).toHaveBeenCalledWith(
      'No answer came for this cancel: it may still be going through. Wait for the order’s status to change before cancelling again.',
      'orders'
    )
    await pass(60_000)
    expect((await sheetButtons(tap)).offered).toBe(false)
    await pass(91_000)
    expect((await sheetButtons(tap)).offered).toBe(true)
  })

  it('after 15 s with the order still working offers "Cancel again", with a short warning', async () => {
    const tap = user()
    await open(WORKING)
    await cancelOnce(tap)
    await pass(14_000)
    expect((await sheetButtons(tap)).offered).toBe(false)
    await pass(1_500)
    expect(await sheetButtons(tap)).toEqual({ cancel: 'Cancel again', offered: true, modify: true })

    await tap.click(orderCard())
    await tap.click(
      within(screen.getByRole('dialog')).getByRole('button', { name: 'Cancel again' })
    )
    expect(screen.getByRole('dialog', { name: /^Cancel this order/ })).toHaveTextContent(
      /A cancel was sent at 10:42:2\d and the order still shows working\./
    )
  })

  it('is kept when the page goes and comes back (Positions and back), and across a reload', async () => {
    const tap = user()
    const { page, view } = await open(WORKING)
    await cancelOnce(tap)
    view.unmount()
    const again = renderBook('/orderbook', page)
    await settle()
    expect((await sheetButtons(tap)).offered).toBe(false)
    again.unmount()

    const reloaded = await load()
    renderBook('/orderbook', reloaded.page)
    await settle()
    expect((await sheetButtons(tap)).offered).toBe(false)
    expect(orderCard()).toHaveTextContent('Cancel sent')
  })

  it('lets go on a final status pushed while the order book was not open', async () => {
    const tap = user()
    const { page, view, push } = await open(WORKING)
    await cancelOnce(tap)
    view.unmount()
    // The shell hears the cancel go through on another page; the book read is still behind.
    push({ orderid: '1001', order_status: 'cancelled' })
    renderBook('/orderbook', page)
    await settle()
    expect(orderCard()).not.toHaveTextContent('Cancel sent')
  })
})

const CANCEL_NOT_CONFIRMED =
  'This cancel is not confirmed: it may have gone through. Check the order’s status before cancelling it again.'

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

describe('g: a cancel that may have reached the broker', () => {
  it.each([
    [
      'the app’s own JSON 500 (the read timed out after Upstox took it)',
      () => notConfirmedAnswer(500, { status: 'error', message: 'The read operation timed out' }),
    ],
    [
      'the app’s 502 marked not confirmed',
      () =>
        notConfirmedAnswer(502, {
          status: 'error',
          message: CANCEL_NOT_CONFIRMED,
          not_confirmed: true,
        }),
    ],
  ])('%s is held as not confirmed, the toast in the app’s words', async (_, error) => {
    const tap = user()
    api.cancelOrder.mockRejectedValueOnce(error())
    await open(WORKING)
    await cancelOnce(tap)
    expect((await sheetButtons(tap)).offered).toBe(false)
    expect(orderCard()).toHaveTextContent('Not confirmed: check its status before cancelling again')
    expect(toast.error).not.toHaveBeenCalled()
    expect(toast.warning).toHaveBeenCalledWith(CANCEL_NOT_CONFIRMED, 'orders')
    expect(api.cancelOrder).toHaveBeenCalledTimes(1)
  })
})

describe('g: a cancel confirmation tapped once that order’s cancel is held', () => {
  it('sends nothing, and says why', async () => {
    const tap = user()
    await open(WORKING)
    await tap.click(orderCard())
    await tap.click(
      within(screen.getByRole('dialog')).getByRole('button', { name: 'Cancel order' })
    )
    const confirmation = screen.getByRole('dialog', { name: /^Cancel this order/ })

    // No path of the page sends one while its confirmation is open; the hold's own store does,
    // as any other part of the app would.
    const sent = await import('@/sudarshan/books/sent')
    act(() => {
      sent.beginSends('cancel', false, [{ id: '1001' }])
    })
    await tap.click(within(confirmation).getByRole('button', { name: 'Cancel order' }))
    await settle()
    expect(api.cancelOrder).not.toHaveBeenCalled()
    expect(toast.warning).toHaveBeenLastCalledWith(
      'A cancel for this order is still being sent. Wait for its answer before cancelling again.',
      'orders'
    )
  })
})

describe('g: a cancel answer the app marks not confirmed, whatever its code', () => {
  it('a 2xx marked so holds the cancel as not confirmed', async () => {
    const tap = user()
    api.cancelOrder.mockResolvedValueOnce({
      status: 'error',
      message: CANCEL_NOT_CONFIRMED,
      not_confirmed: true,
    })
    await open(WORKING)
    await cancelOnce(tap)
    expect(orderCard()).toHaveTextContent('Not confirmed: check its status before cancelling again')
    expect((await sheetButtons(tap)).offered).toBe(false)
    expect(toast.error).not.toHaveBeenCalled()
  })
})
