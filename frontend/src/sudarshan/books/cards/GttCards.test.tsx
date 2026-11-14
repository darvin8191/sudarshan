/**
 * The GTT book on a phone (B1: M5.6, M6.1), through the real GTT tab with its cards: a card per
 * GTT with its side, quantity, trigger and status in words, a sheet whose Modify and Cancel are
 * the tab's own, a cancel that asks first, goes once and is held until the GTT book shows it
 * gone, and a book read again when an order arrives for a GTT's instrument.
 */
import { act, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { AxiosError, AxiosHeaders } from 'axios'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { GttOrder } from '@/types/trading'

vi.mock('@/lib/MarketDataManager', () => import('@/test/marketDataHarness'))

const api = vi.hoisted(() => ({
  getGttOrderbook: vi.fn(),
  cancelGttOrder: vi.fn(),
  modifyGttOrder: vi.fn(),
  getOrders: vi.fn(),
}))
vi.mock('@/api/trading', () => ({
  tradingApi: {
    getGttOrderbook: (...args: unknown[]) => api.getGttOrderbook(...args),
    cancelGttOrder: (...args: unknown[]) => api.cancelGttOrder(...args),
    modifyGttOrder: (...args: unknown[]) => api.modifyGttOrder(...args),
    getOrders: (...args: unknown[]) => api.getOrders(...args),
    getMultiQuotes: vi.fn().mockResolvedValue({ status: 'success', results: [] }),
  },
}))
const auth = vi.hoisted(() => ({
  facts: {
    apiKey: 'test-api-key',
    sessionChecked: true,
    brokerSessionExpired: false,
    user: { broker: 'upstox' },
  },
}))
vi.mock('@/stores/authStore', () => ({
  useAuthStore: Object.assign(() => auth.facts, { getState: () => auth.facts }),
}))
const theme = vi.hoisted(() => ({ appMode: 'live' }))
vi.mock('@/stores/themeStore', () => ({
  onModeChange: () => () => {},
  useThemeStore: (pick: (state: { appMode: string }) => unknown) => pick(theme),
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

import GttTab from '@/components/trading/GttTab'
import OrderBook from '@/pages/OrderBook'
import { NOT_CONFIRMED } from '@/sudarshan/books/sendOutcome'
import { resetSentForTests } from '@/sudarshan/books/sent'
import { back, pass, renderBook, settle, where } from '@/test/bookPages'
import { fakeClock, frames } from '@/test/frames'
import { fakeScreen } from '@/test/screenWidth'
// Loaded before the tab asks for it, so its lazy import lands in the first settle.
import './GttCards'

vi.setConfig({ testTimeout: 15_000 })

/** A stop on a cash holding: SELL 50 RELIANCE if it falls to 1,240 (Upstox's book, UTC). */
const PROTECT: GttOrder = {
  trigger_id: 'GTT-C1',
  trigger_type: 'single',
  status: 'active',
  symbol: 'RELIANCE',
  exchange: 'NSE',
  trigger_prices: [1240],
  last_price: 0,
  legs: [{ action: 'SELL', quantity: 50, price: 1240, pricetype: 'LIMIT', product: 'CNC' }],
  created_at: '2026-11-12T04:41:00Z',
  expires_at: '2027-11-12T04:41:00Z',
}
/** One that fired in the sandbox (its times are India's, written without a zone). */
const FIRED: GttOrder = {
  trigger_id: 'GTT-261110-ab12cd34',
  trigger_type: 'two-leg',
  status: 'triggered',
  symbol: 'SBIN',
  exchange: 'NSE',
  trigger_prices: [780, 840],
  last_price: 0,
  legs: [
    {
      action: 'SELL',
      quantity: 10,
      price: 779,
      pricetype: 'LIMIT',
      product: 'CNC',
      triggered_order_id: '2611100001',
    },
    { action: 'SELL', quantity: 10, price: 841, pricetype: 'LIMIT', product: 'CNC' },
  ],
  created_at: '2026-11-10T09:15:30.123456',
}

const book = (...gtts: GttOrder[]) => ({ status: 'success', data: gtts })

function answered(status: number, data: unknown) {
  return new AxiosError('Request failed', 'ERR_BAD_RESPONSE', undefined, undefined, {
    status,
    statusText: '',
    headers: {},
    config: { headers: new AxiosHeaders() },
    data,
  })
}

const user = () => userEvent.setup({ advanceTimers: vi.advanceTimersByTime })

async function renderCards(...gtts: GttOrder[]) {
  // A fresh list on every read, as each answer parsed from the wire is.
  api.getGttOrderbook.mockImplementation(async () => book(...gtts))
  const view = renderBook('/orderbook', <GttTab cards />)
  await settle()
  await settle()
  return view
}

function card(id: string) {
  const item = document.querySelector(`li[data-gtt="${id}"]`)
  if (!item) throw new Error(`no card for ${id}`)
  return within(item as HTMLElement).getByRole('button')
}

/** Tap the card and Cancel GTT on its sheet: the confirmation. */
async function askToCancel(tap: ReturnType<typeof user>, id: string) {
  await tap.click(card(id))
  await tap.click(within(screen.getByRole('dialog')).getByRole('button', { name: 'Cancel GTT' }))
  return screen.getByRole('dialog', { name: 'Cancel this GTT?' })
}

/** Cancel GTT in the confirmation, twice in a row as a double tap lands. */
async function cancelFromSheet(tap: ReturnType<typeof user>, id: string) {
  const confirm = await askToCancel(tap, id)
  await tap.dblClick(within(confirm).getByRole('button', { name: 'Cancel GTT' }))
  await settle()
}

beforeEach(() => {
  fakeClock()
  vi.setSystemTime(new Date('2026-11-14T05:12:21Z'))
  frames.install()
  socket.clear()
  resetSentForTests()
  theme.appMode = 'live'
  api.getGttOrderbook.mockReset()
  api.cancelGttOrder.mockReset().mockResolvedValue({ status: 'success', trigger_id: 'GTT-C1' })
  api.modifyGttOrder.mockReset().mockResolvedValue({ status: 'success' })
  api.getOrders.mockReset().mockResolvedValue({ status: 'success', data: { orders: [] } })
  for (const fn of Object.values(toast)) fn.mockReset()
})

afterEach(() => {
  vi.useRealTimers()
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
})

describe('GTT book on a phone: the cards', () => {
  it('shows each GTT with its side, quantity, trigger and status in words, and when it was set', async () => {
    await renderCards(PROTECT, FIRED)
    expect(screen.queryByRole('table')).toBeNull()
    expect(screen.getByText(/^Read at 10:42:2\d$/)).toBeInTheDocument()

    const stop = card('GTT-C1')
    expect(stop).toHaveTextContent('SELL')
    expect(stop).toHaveTextContent('RELIANCE')
    expect(stop).toHaveTextContent('NSE · CNC · Single')
    expect(stop).toHaveTextContent('Created 12 Nov, 10:11')
    expect(stop).toHaveTextContent('Qty 50 · Trigger 1,240.00')
    const chip = (id: string) => card(id).querySelector('[data-status]') as HTMLElement
    expect(chip('GTT-C1')).toHaveTextContent('ACTIVE')
    expect(chip('GTT-C1')).toHaveClass('text-[color:var(--warning-text)]')

    const fired = card('GTT-261110-ab12cd34')
    expect(fired).toHaveTextContent('NSE · CNC · OCO')
    expect(fired).toHaveTextContent('Created 10 Nov, 09:15')
    expect(fired).toHaveTextContent('Qty 10 · Triggers 780.00 and 840.00')
    expect(chip('GTT-261110-ab12cd34')).toHaveTextContent('TRIGGERED')
    for (const id of ['GTT-C1', 'GTT-261110-ab12cd34']) {
      expect(chip(id).querySelector('svg')).not.toBeNull()
    }
  })

  it('says plainly when there is none, and when the book could not be read', async () => {
    const view = await renderCards()
    expect(screen.getByText('No GTT orders')).toBeInTheDocument()
    view.unmount()

    api.getGttOrderbook.mockRejectedValue(answered(500, { status: 'error', message: 'boom' }))
    renderBook('/orderbook', <GttTab cards />)
    await settle()
    await settle()
    expect(screen.getByRole('alert')).toHaveTextContent('The GTT book could not be read just now.')
    expect(screen.queryByText(/boom/)).toBeNull()
  })

  it('offers Modify and Cancel on an active GTT only, and shows what a fired one placed', async () => {
    const tap = user()
    await renderCards(PROTECT, FIRED)
    await tap.click(card('GTT-261110-ab12cd34'))
    let sheet = screen.getByRole('dialog')
    expect(within(sheet).queryByRole('button', { name: 'Cancel GTT' })).toBeNull()
    expect(sheet).toHaveTextContent('Order placed2611100001')
    expect(sheet).toHaveTextContent('Order 2 when it firesSELL 10 · Limit 841.00')
    back()
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull())

    await tap.click(card('GTT-C1'))
    sheet = screen.getByRole('dialog')
    expect(within(sheet).getByRole('button', { name: 'Modify' })).toBeEnabled()
    expect(within(sheet).getByRole('button', { name: 'Cancel GTT' })).toBeEnabled()
    expect(sheet).toHaveTextContent('Order when it firesSELL 50 · Limit 1,240.00')
    expect(sheet).toHaveTextContent('ProductCNC · Delivery')
    expect(sheet).toHaveTextContent('Expires12 Nov 2027')
    expect(where()).toBe('/orderbook?row=GTT-C1')
  })
})

describe('GTT book on a phone: Cancel and Modify', () => {
  it('cancels through the tab’s own call after asking, once however it is tapped', async () => {
    const tap = user()
    await renderCards(PROTECT)
    const confirm = await askToCancel(tap, 'GTT-C1')
    expect(confirm).toHaveTextContent(
      'SELL 50 RELIANCE · Trigger 1,240.00. It is removed at Upstox and will not fire.'
    )
    expect(api.cancelGttOrder).not.toHaveBeenCalled()
    await tap.dblClick(within(confirm).getByRole('button', { name: 'Cancel GTT' }))
    await settle()
    expect(api.cancelGttOrder).toHaveBeenCalledTimes(1)
    expect(api.cancelGttOrder).toHaveBeenCalledWith('GTT-C1')
    await waitFor(() => expect(where()).toBe('/orderbook'))
  })

  it('holds the GTT "Cancel sent" until the GTT book shows it gone', async () => {
    const tap = user()
    await renderCards(PROTECT)
    // The tab's own read after its cancel still shows it: Upstox can lag.
    await cancelFromSheet(tap, 'GTT-C1')
    expect(card('GTT-C1')).toHaveTextContent('Cancel sent')
    await pass(1_000)
    expect(card('GTT-C1')).toHaveTextContent('Cancel sent')
    await tap.click(card('GTT-C1'))
    const sheet = screen.getByRole('dialog')
    expect(within(sheet).getByRole('button', { name: 'Cancel sent' })).toBeDisabled()
    expect(within(sheet).getByRole('button', { name: 'Modify' })).toBeDisabled()
    back()
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull())

    api.getGttOrderbook.mockResolvedValue(book())
    act(() => socket.fire('gtt_event', { event: 'cancelled', trigger_id: 'GTT-C1' }))
    await settle()
    expect(document.querySelector('li[data-gtt]')).toBeNull()
    expect(screen.getByText('No GTT orders')).toBeInTheDocument()
  })

  it('lets go at once of a refused cancel, in Upstox’s words without its code', async () => {
    const tap = user()
    api.cancelGttOrder.mockRejectedValue(
      answered(400, {
        status: 'error',
        message: 'UDAPI1150: GTT order is not in a modifiable state',
      })
    )
    await renderCards(PROTECT)
    await cancelFromSheet(tap, 'GTT-C1')
    expect(toast.error).toHaveBeenCalledWith('GTT order is not in a modifiable state', 'orders')
    expect(card('GTT-C1')).not.toHaveTextContent('Cancel sent')
  })

  it('holds a cancel that may have gone as not confirmed, never failed', async () => {
    const tap = user()
    api.cancelGttOrder.mockRejectedValue(
      answered(500, { status: 'error', message: 'Failed to cancel GTT due to internal error' })
    )
    await renderCards(PROTECT)
    await cancelFromSheet(tap, 'GTT-C1')
    expect(toast.warning).toHaveBeenCalledWith(NOT_CONFIRMED.gttCancel, 'orders')
    expect(toast.error).not.toHaveBeenCalled()
    expect(card('GTT-C1')).toHaveTextContent(
      'Not confirmed: check its status before cancelling again'
    )
  })

  it('says a cancel or a modify goes to the sandbox when Analyze mode is on', async () => {
    const tap = user()
    theme.appMode = 'analyzer'
    await renderCards(PROTECT)
    await tap.click(card('GTT-C1'))
    const sheet = screen.getByRole('dialog')
    expect(within(sheet).getByRole('button', { name: 'Modify in the sandbox' })).toBeInTheDocument()
    await tap.click(within(sheet).getByRole('button', { name: 'Cancel GTT in the sandbox' }))
    expect(screen.getByRole('dialog', { name: 'Cancel this GTT?' })).toHaveTextContent(
      'It is removed from the sandbox. Analyze mode is on, so nothing goes to Upstox.'
    )
  })

  it('opens the tab’s own modify dialog, and Back closes it', async () => {
    const tap = user()
    await renderCards(PROTECT)
    await tap.click(card('GTT-C1'))
    await tap.click(within(screen.getByRole('dialog')).getByRole('button', { name: 'Modify' }))
    const dialog = screen.getByRole('dialog', { name: /Modify GTT/ })
    expect(dialog).toHaveTextContent('GTT-C1')
    expect(where()).toBe('/orderbook?act=modify&row=GTT-C1')

    back()
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull())
    expect(where()).toBe('/orderbook')
    expect(api.modifyGttOrder).not.toHaveBeenCalled()
  })
})

describe('GTT book on a phone: kept current', () => {
  it('reads again when an order arrives for a GTT’s instrument, not for another', async () => {
    await renderCards(PROTECT)
    const reads = () => api.getGttOrderbook.mock.calls.length
    const before = reads()
    act(() => socket.fire('order_update', { mode: 'live', symbol: 'INFY', exchange: 'NSE' }))
    await pass(1_500)
    expect(reads()).toBe(before)
    act(() => socket.fire('order_update', { mode: 'live', symbol: 'RELIANCE', exchange: 'NSE' }))
    await pass(1_500)
    expect(reads()).toBeGreaterThan(before)
  })

  it('keeps the GTTs last read, with their time, when a read fails', async () => {
    await renderCards(PROTECT)
    await pass(60_000)
    api.getGttOrderbook.mockRejectedValue(answered(500, { status: 'error', message: 'boom' }))
    act(() => socket.fire('gtt_event', { event: 'placed' }))
    await settle()
    expect(card('GTT-C1')).toHaveTextContent('RELIANCE')
    expect(
      screen.getByText(/Could not refresh just now\. Showing the GTTs read at 10:43:/)
    ).toBeInTheDocument()
  })
})

describe('the order book page', () => {
  it('shows the GTT tab as cards on a phone, and as the table on a wide screen', async () => {
    const tap = user()
    api.getGttOrderbook.mockResolvedValue(book(PROTECT))
    fakeScreen('phone')
    const view = renderBook('/orderbook', <OrderBook />)
    await settle()
    await tap.click(screen.getByRole('tab', { name: 'GTT' }))
    await settle()
    expect(card('GTT-C1')).toHaveTextContent('RELIANCE')
    expect(screen.queryByRole('table')).toBeNull()
    view.unmount()

    fakeScreen('wide')
    renderBook('/orderbook', <OrderBook />)
    await settle()
    await tap.click(screen.getByRole('tab', { name: 'GTT' }))
    await settle()
    expect(screen.getByRole('table')).toBeInTheDocument()
    expect(document.querySelector('li[data-gtt]')).toBeNull()
  })
})
