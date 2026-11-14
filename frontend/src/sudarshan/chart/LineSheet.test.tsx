/**
 * A chart line's sheet (M4.7, C6): each action is the page's own call, made
 * once however it is tapped, through the open guard, and held "sent" by
 * `books/sent.ts`; nothing here is a new way to send an order.
 */
import { QueryClientProvider } from '@tanstack/react-query'
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { AlertRow } from '@/api/marketAlerts'
import { createQueryClient } from '@/app/providers'
import { useThemeStore } from '@/stores/themeStore'
import { resetAlertActionsForTests } from '@/sudarshan/alerts/actions'
import { resetOrderMemoryForTests } from '@/sudarshan/books/orderFold'
import { resetSentForTests } from '@/sudarshan/books/sent'
import { OPEN_GUARD } from '@/sudarshan/watchlist/openGuard'
import type { Order, Position } from '@/types/trading'
import { LineSheet, type LineSheetProps } from './LineSheet'
import { type ChartLine, linesOf } from './overlays'
import { PriceStore } from './prices'
import type { ChartBooks } from './useChartLines'

const api = vi.hoisted(() => ({
  closePosition: vi.fn(),
  cancelOrder: vi.fn(),
  modifyOrder: vi.fn(),
  getOrders: vi.fn(),
  remove: vi.fn(),
  toast: { success: vi.fn(), error: vi.fn(), warning: vi.fn(), info: vi.fn() },
}))
vi.mock('@/api/trading', () => ({ tradingApi: api }))
vi.mock('@/api/marketAlerts', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/api/marketAlerts')>()),
  marketAlertsApi: { remove: api.remove },
}))
vi.mock('@/utils/toast', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/utils/toast')>()),
  showToast: api.toast,
  toast: api.toast,
}))

const SYMBOL = 'NIFTY18NOV2625900CE'
const held: Position = {
  symbol: SYMBOL,
  exchange: 'NFO',
  product: 'NRML',
  quantity: 65,
  average_price: 131.2,
  ltp: 140,
  pnl: 572,
  pnlpercent: 6.7,
}
const stop: Order = {
  symbol: SYMBOL,
  exchange: 'NFO',
  action: 'SELL',
  quantity: 65,
  price: 119.5,
  trigger_price: 120,
  pricetype: 'SL',
  product: 'NRML',
  orderid: 'A1',
  order_status: 'trigger pending',
  timestamp: '2026-11-16 10:41:51',
}
const watch = {
  id: 7,
  version: 3,
  status: 'active',
  kind: 'price',
  symbol: SYMBOL,
  exchange: 'NFO',
  params: { op: 'at_or_above', level: '150' },
} as AlertRow

const refresh = vi.fn(async () => {})
const books = (over: Partial<ChartBooks> = {}): ChartBooks => ({
  positions: [held],
  orders: [stop],
  bookAt: Date.now(),
  sandbox: false,
  loading: false,
  failed: false,
  refresh,
  ...over,
})
const drawn = (from: ChartBooks = books()): ChartLine[] =>
  linesOf({
    symbol: SYMBOL,
    exchange: 'NFO',
    decimals: 2,
    positions: from.positions,
    orders: from.orders,
    alerts: [watch],
  }).lines

const handlers = { onClose: vi.fn(), onAdd: vi.fn(), onEditAlert: vi.fn() }

function sheet(ids: string[] | null, over: Partial<LineSheetProps> = {}) {
  const from = over.books ?? books()
  return (
    <QueryClientProvider client={createQueryClient()}>
      <LineSheet
        ids={ids}
        lines={drawn(from)}
        books={from}
        apiKey="test-key"
        tick={0.05}
        canAdd
        prices={new PriceStore((run) => run())}
        quiet={false}
        {...handlers}
        {...over}
      />
    </QueryClientProvider>
  )
}

beforeEach(() => {
  vi.clearAllMocks()
  resetSentForTests()
  resetOrderMemoryForTests()
  resetAlertActionsForTests()
  useThemeStore.setState({ appMode: 'live' })
  api.closePosition.mockResolvedValue({ status: 'success', orderid: 'X9' })
  api.cancelOrder.mockResolvedValue({ status: 'success', data: { orderid: 'A1' } })
  api.modifyOrder.mockResolvedValue({ status: 'success' })
  api.getOrders.mockResolvedValue({ status: 'success', data: { orders: [stop], statistics: {} } })
  api.remove.mockResolvedValue({})
})
afterEach(() => {
  OPEN_GUARD.ms = 0
  vi.useRealTimers()
})

const actions = () => screen.getByRole('dialog', { name: 'NIFTY 25900 CE' })

describe("a position line's sheet", () => {
  it('offers Exit and Add, saying what each sends, with the P&L beside the name', () => {
    render(sheet(['position:NRML']))
    const dialog = within(actions())
    expect(actions()).toHaveTextContent('Buy 65 @ 131.20')
    expect(actions()).toHaveTextContent('+₹572.00')
    expect(dialog.getByRole('button', { name: /^Exit/ })).toHaveTextContent('Sell 65 at market')
    expect(dialog.getByRole('button', { name: /^Add/ })).toHaveTextContent('Buy more')
    expect(dialog.queryByRole('button', { name: /Modify|Cancel order/ })).toBeNull()
  })

  it("asks first, then sends the Positions page's own close once, in the position's product", async () => {
    render(sheet(['position:NRML']))
    await userEvent.click(within(actions()).getByRole('button', { name: /^Exit/ }))
    expect(api.closePosition).not.toHaveBeenCalled()
    const confirm = screen.getByRole('dialog', { name: /^Exit NIFTY.*: sell 65 at market\?$/ })
    expect(confirm).toHaveTextContent('Sells 65 (NRML) at market price to close this position.')
    const yes = within(confirm).getByRole('button', { name: 'Exit' })
    // A double tap is one exit.
    fireEvent.click(yes)
    fireEvent.click(yes)
    await waitFor(() => expect(api.closePosition).toHaveBeenCalledTimes(1))
    expect(api.closePosition).toHaveBeenCalledWith(SYMBOL, 'NFO', 'NRML')
    await waitFor(() =>
      expect(api.toast.success).toHaveBeenCalledWith(
        'Exit sent: sell 65 NIFTY 18 NOV 25900 CE',
        'positions'
      )
    )
    expect(refresh).toHaveBeenCalled()
  })

  it('stays "Exit sent" after it, and a second exit cannot be sent while it is held', async () => {
    render(sheet(['position:NRML']))
    await userEvent.click(within(actions()).getByRole('button', { name: /^Exit/ }))
    await userEvent.click(
      within(screen.getByRole('dialog', { name: /^Exit NIFTY/ })).getByRole('button', {
        name: 'Exit',
      })
    )
    const exit = await waitFor(() => {
      const button = within(actions()).getByRole('button', { name: /^Exit/ })
      expect(button).toHaveTextContent('Exit sent')
      return button
    })
    expect(exit).toBeDisabled()
    fireEvent.click(exit)
    expect(screen.queryByRole('dialog', { name: /^Exit NIFTY/ })).toBeNull()
    expect(api.closePosition).toHaveBeenCalledTimes(1)
  })

  it('reads "not confirmed", never a refusal, when the exit may have reached the broker', async () => {
    api.closePosition.mockRejectedValue(
      Object.assign(new Error('timeout'), { isAxiosError: true, code: 'ECONNABORTED' })
    )
    render(sheet(['position:NRML']))
    await userEvent.click(within(actions()).getByRole('button', { name: /^Exit/ }))
    await userEvent.click(
      within(screen.getByRole('dialog', { name: /^Exit NIFTY/ })).getByRole('button', {
        name: 'Exit',
      })
    )
    await waitFor(() => expect(api.toast.warning).toHaveBeenCalled())
    expect(api.toast.error).not.toHaveBeenCalled()
    await waitFor(() =>
      expect(within(actions()).getByRole('button', { name: /^Exit/ })).toBeDisabled()
    )
  })

  it("says the exit goes to the sandbox in Analyze mode, where the page's call sends it", async () => {
    useThemeStore.setState({ appMode: 'analyzer' })
    render(sheet(['position:NRML'], { books: books({ sandbox: true }) }))
    expect(within(actions()).getByRole('button', { name: /^Exit/ })).toHaveTextContent(
      'Sell 65 in the sandbox'
    )
    await userEvent.click(within(actions()).getByRole('button', { name: /^Exit/ }))
    expect(screen.getByRole('dialog', { name: /^Exit NIFTY/ })).toHaveTextContent(
      'Analyze mode is on, so nothing goes to Upstox.'
    )
  })

  it('hands Add the position, so the ticket opens on its side and product; off until the lot is known', async () => {
    const { rerender } = render(sheet(['position:NRML'], { canAdd: false }))
    expect(within(actions()).getByRole('button', { name: /^Add/ })).toBeDisabled()
    rerender(sheet(['position:NRML']))
    await userEvent.click(within(actions()).getByRole('button', { name: /^Add/ }))
    expect(handlers.onAdd).toHaveBeenCalledWith(held)
    expect(api.closePosition).not.toHaveBeenCalled()
  })

  it('takes no tap for half a second after it opens, nor does its confirmation', () => {
    vi.useFakeTimers()
    OPEN_GUARD.ms = 500
    render(sheet(['position:NRML']))
    const exit = () => within(actions()).getByRole('button', { name: /^Exit/ })
    fireEvent.click(exit())
    expect(screen.queryByRole('dialog', { name: /^Exit NIFTY/ })).toBeNull()
    act(() => void vi.advanceTimersByTime(500))
    fireEvent.click(exit())
    const yes = within(screen.getByRole('dialog', { name: /^Exit NIFTY/ })).getByRole('button', {
      name: 'Exit',
    })
    fireEvent.click(yes)
    expect(api.closePosition).not.toHaveBeenCalled()
    act(() => void vi.advanceTimersByTime(500))
    fireEvent.click(yes)
    expect(api.closePosition).toHaveBeenCalledTimes(1)
  })
})

describe("an order line's sheet", () => {
  it('asks first, then sends the Orders page\'s own cancel once, and stays "Cancel sent"', async () => {
    render(sheet(['order:A1:trigger']))
    expect(actions()).toHaveTextContent('SELL 65 SL trigger 120.00')
    await userEvent.click(within(actions()).getByRole('button', { name: 'Cancel order' }))
    expect(api.cancelOrder).not.toHaveBeenCalled()
    const confirm = screen.getByRole('dialog', { name: 'Cancel this order?' })
    expect(confirm).toHaveTextContent('The order stops working at the exchange.')
    const yes = within(confirm).getByRole('button', { name: 'Cancel order' })
    fireEvent.click(yes)
    fireEvent.click(yes)
    await waitFor(() => expect(api.cancelOrder).toHaveBeenCalledTimes(1))
    expect(api.cancelOrder).toHaveBeenCalledWith('A1')
    const cancel = await waitFor(() =>
      within(actions()).getByRole('button', { name: 'Cancel sent' })
    )
    expect(cancel).toBeDisabled()
    // A cancel on its way holds Modify too.
    expect(within(actions()).getByRole('button', { name: 'Modify' })).toBeDisabled()
  })

  it('lets a held cancel go when the order book shows the order final', async () => {
    const { rerender } = render(sheet(['order:A1:trigger']))
    await userEvent.click(within(actions()).getByRole('button', { name: 'Cancel order' }))
    await userEvent.click(
      within(screen.getByRole('dialog', { name: 'Cancel this order?' })).getByRole('button', {
        name: 'Cancel order',
      })
    )
    await waitFor(() => within(actions()).getByRole('button', { name: 'Cancel sent' }))
    // The book now shows it cancelled: its lines are gone, and so is the sheet.
    rerender(
      sheet(['order:A1:trigger'], {
        books: books({ orders: [{ ...stop, order_status: 'cancelled' }], bookAt: Date.now() + 1 }),
      })
    )
    await waitFor(() => expect(handlers.onClose).toHaveBeenCalled())
  })

  it("modifies through the Orders page's own call: the whole order back, with what was changed", async () => {
    render(sheet(['order:A1:price']))
    await userEvent.click(within(actions()).getByRole('button', { name: 'Modify' }))
    const form = within(actions())
    expect(form.getByLabelText('Quantity')).toHaveValue(65)
    expect(form.getByLabelText('Trigger price')).toHaveValue(120)
    const price = form.getByLabelText('Price')
    await userEvent.clear(price)
    await userEvent.type(price, '118.5')
    const send = form.getByRole('button', { name: 'Modify order' })
    fireEvent.click(send)
    fireEvent.click(send)
    await waitFor(() => expect(api.modifyOrder).toHaveBeenCalledTimes(1))
    expect(api.modifyOrder).toHaveBeenCalledWith('A1', {
      symbol: SYMBOL,
      exchange: 'NFO',
      action: 'SELL',
      product: 'NRML',
      pricetype: 'SL',
      quantity: 65,
      price: 118.5,
      trigger_price: 120,
    })
    await waitFor(() => expect(handlers.onClose).toHaveBeenCalled())
    expect(api.toast.success).toHaveBeenCalledWith('Order modified: A1', 'orders')
  })

  it('sends nothing for a figure that cannot be sent, and says which', async () => {
    render(sheet(['order:A1:price']))
    await userEvent.click(within(actions()).getByRole('button', { name: 'Modify' }))
    const form = within(actions())
    await userEvent.clear(form.getByLabelText('Price'))
    expect(form.getByText('Enter a price above 0.')).toBeVisible()
    expect(form.getByRole('button', { name: 'Modify order' })).toBeDisabled()
    await userEvent.type(form.getByLabelText('Price'), '119')
    await userEvent.clear(form.getByLabelText('Quantity'))
    await userEvent.type(form.getByLabelText('Quantity'), '0')
    expect(form.getByText('Enter a whole quantity above 0.')).toBeVisible()
    fireEvent.submit(form.getByLabelText('Quantity').closest('form') as HTMLFormElement)
    expect(api.modifyOrder).not.toHaveBeenCalled()
  })

  it("keeps the form open with the refusal in the broker's words when a modify is refused", async () => {
    api.modifyOrder.mockResolvedValue({ status: 'error', message: 'Price out of range' })
    render(sheet(['order:A1:price']))
    await userEvent.click(within(actions()).getByRole('button', { name: 'Modify' }))
    await userEvent.click(within(actions()).getByRole('button', { name: 'Modify order' }))
    await waitFor(() =>
      expect(api.toast.error).toHaveBeenCalledWith('Price out of range', 'orders')
    )
    expect(handlers.onClose).not.toHaveBeenCalled()
    expect(within(actions()).getByLabelText('Price')).toBeVisible()
  })
})

describe("an alert line's sheet, and lines that share a target", () => {
  it("opens the alert's own form for Edit, and deletes after asking", async () => {
    render(sheet(['alert:7']))
    expect(actions()).toHaveTextContent('Alert ≥ 150.00')
    await userEvent.click(within(actions()).getByRole('button', { name: 'Edit' }))
    expect(handlers.onEditAlert).toHaveBeenCalledWith(7)
    await userEvent.click(within(actions()).getByRole('button', { name: 'Delete' }))
    expect(api.remove).not.toHaveBeenCalled()
    await userEvent.click(
      within(screen.getByRole('dialog', { name: 'Delete this alert?' })).getByRole('button', {
        name: 'Delete',
      })
    )
    await waitFor(() => expect(api.remove).toHaveBeenCalledWith(7))
  })

  it('lists the lines under a shared target first, and acts only on the one picked', async () => {
    render(sheet(['order:A1:trigger', 'order:A1:price', 'position:NRML']))
    const list = within(screen.getByRole('list', { name: 'Lines near this price' }))
    expect(list.getAllByRole('button').map((button) => button.textContent)).toEqual([
      'Buy 65 @ 131.20',
      'SELL 65 SL trigger 120.00',
      'SELL 65 SL limit 119.50',
    ])
    expect(screen.queryByRole('button', { name: /^Exit/ })).toBeNull()
    await userEvent.click(list.getByRole('button', { name: 'Buy 65 @ 131.20' }))
    expect(within(actions()).getByRole('button', { name: /^Exit/ })).toBeVisible()
    expect(screen.queryByRole('button', { name: 'Cancel order' })).toBeNull()
  })

  it('warns before any action on a line whose book could not be read', () => {
    const lines = linesOf({
      symbol: SYMBOL,
      exchange: 'NFO',
      decimals: 2,
      positions: [held],
      orders: [],
      alerts: [],
      staleNote: 'as of 10:42:07',
    }).lines
    render(sheet(['position:NRML'], { lines, books: books({ failed: true }) }))
    expect(actions()).toHaveTextContent(
      'The books could not be read just now: this line is as of 10:42:07. Check Positions or Orders before acting on it.'
    )
  })

  it('shows nothing while no label has been tapped, and closes when its line has gone', async () => {
    const { rerender } = render(sheet(null))
    expect(screen.queryByRole('dialog')).toBeNull()
    rerender(sheet(['order:GONE:price']))
    await waitFor(() => expect(handlers.onClose).toHaveBeenCalled())
    expect(screen.queryByRole('dialog')).toBeNull()
  })
})
