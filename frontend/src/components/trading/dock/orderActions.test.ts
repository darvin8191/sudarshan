import { AxiosError, AxiosHeaders } from 'axios'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import type { DockOrder, DockPosition } from './blotter'
import { cancelDockOrder, closeDockPosition, type OrderActionContext } from './orderActions'

const fake = vi.hoisted(() => ({
  api: { cancelOrder: vi.fn(), closePosition: vi.fn() },
  toast: { success: vi.fn(), error: vi.fn(), warning: vi.fn() },
}))
vi.mock('@/api/trading', () => ({ tradingApi: fake.api }))
vi.mock('@/utils/toast', () => ({ showToast: fake.toast }))

const order = { orderid: '42', symbol: 'SBIN', exchange: 'NSE' } as DockOrder
const position = { symbol: 'SBIN', exchange: 'NSE', product: 'MIS', quantity: 10 } as DockPosition
const ctx = (over: Partial<OrderActionContext> = {}): OrderActionContext => ({
  appMode: 'live',
  refuse: vi.fn(() => false),
  refresh: vi.fn(),
  ...over,
})

beforeEach(() => {
  vi.clearAllMocks()
  fake.api.cancelOrder.mockResolvedValue({ status: 'success' })
  fake.api.closePosition.mockResolvedValue({ status: 'success' })
})

describe('the dock order actions', () => {
  it('cancel sends the order id and reports success', async () => {
    const c = ctx()
    expect(await cancelDockOrder(order, c)).toBe(true)
    expect(fake.api.cancelOrder).toHaveBeenCalledWith('42')
    expect(c.refresh).toHaveBeenCalled()
  })

  it('close sends symbol, exchange and product to the square-off route', async () => {
    expect(await closeDockPosition(position, ctx())).toBe(true)
    expect(fake.api.closePosition).toHaveBeenCalledWith('SBIN', 'NSE', 'MIS')
  })

  it('sends nothing while trading is locked', async () => {
    const c = ctx({ refuse: vi.fn(() => true) })
    expect(await cancelDockOrder(order, c)).toBe(false)
    expect(await closeDockPosition(position, c)).toBe(false)
    expect(fake.api.cancelOrder).not.toHaveBeenCalled()
    expect(fake.api.closePosition).not.toHaveBeenCalled()
  })

  it('leaves analyzer outcomes to the global analyzer toast', async () => {
    fake.api.closePosition.mockResolvedValue({ status: 'error', message: 'No position' })
    expect(await closeDockPosition(position, ctx({ appMode: 'analyzer' }))).toBe(false)
    expect(fake.toast.error).not.toHaveBeenCalled()
    expect(await closeDockPosition(position, ctx({ appMode: 'live' }))).toBe(false)
    expect(fake.toast.error).toHaveBeenCalledWith('No position', 'orders')
  })

  it('says a send that may still be going through in the app’s words, never the transport’s', async () => {
    // SUDARSHAN M6.9: what axios throws when the 45 s wait runs out.
    const timedOut = new AxiosError('timeout of 45000ms exceeded', AxiosError.ECONNABORTED)
    fake.api.closePosition.mockRejectedValueOnce(timedOut)
    expect(await closeDockPosition(position, ctx())).toBe(false)
    fake.api.cancelOrder.mockRejectedValueOnce(timedOut)
    expect(await cancelDockOrder(order, ctx())).toBe(false)

    expect(fake.toast.error).not.toHaveBeenCalled()
    expect(fake.toast.warning.mock.calls).toEqual([
      [
        'No answer came for this exit: it may still be going through. Wait for Positions or Orders to show it before sending another.',
        'orders',
      ],
      [
        'No answer came for this cancel: it may still be going through. Wait for the order’s status to change before cancelling again.',
        'orders',
      ],
    ])
  })

  it('still shows a refusal in red, with the reason the app gave', async () => {
    fake.api.closePosition.mockRejectedValueOnce(
      new AxiosError('Request failed', 'ERR_BAD_REQUEST', undefined, undefined, {
        status: 409,
        statusText: '',
        headers: {},
        config: { headers: new AxiosHeaders() },
        data: {
          status: 'error',
          message:
            'An exit for this position is still being placed. Wait for it, then check Orders.',
        },
      })
    )
    expect(await closeDockPosition(position, ctx())).toBe(false)
    expect(fake.toast.error).toHaveBeenCalledWith(
      'An exit for this position is still being placed. Wait for it, then check Orders.',
      'orders'
    )
  })
})
