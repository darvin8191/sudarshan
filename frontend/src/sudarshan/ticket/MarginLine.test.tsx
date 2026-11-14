/**
 * The order ticket's margin line (B5: M5.9): the margin read 400 ms after the ticket opens and
 * after each change, one read at a time and only for the order as it stands; the funds once;
 * "not read" for anything not read, never ₹0; nothing read for an order that is not filled in.
 */
import { act, render, screen } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const post = vi.hoisted(() => vi.fn())
vi.mock('@/api/client', () => ({ apiClient: { post: (...args: unknown[]) => post(...args) } }))

import { MARGIN_SETTLE_MS, MarginLine, type MarginOrder } from './MarginLine'

const ORDER: MarginOrder = {
  symbol: 'NIFTY25NOV2625800PE',
  exchange: 'NFO',
  action: 'SELL',
  quantity: 130,
  pricetype: 'MARKET',
  product: 'MIS',
  price: 0,
  triggerPrice: 0,
}

const FUNDS = {
  status: 'success',
  data: {
    availablecash: '250000.00',
    collateral: '0.00',
    m2mrealized: '0.00',
    m2munrealized: '0.00',
    utiliseddebits: '1200.00',
  },
}

/** The margin each read answers, by quantity; a promise held open stands for a slow read. */
let margins: Record<number, number> = {}
let held: Array<() => void> = []
let slow = false

function answer(url: string, body: { positions?: Array<{ quantity: string }> }) {
  if (url === '/funds') return Promise.resolve({ data: FUNDS })
  const quantity = Number(body.positions?.[0]?.quantity)
  const reply = {
    data: { status: 'success', data: { total_margin_required: margins[quantity] ?? 0 } },
  }
  if (!slow) return Promise.resolve(reply)
  return new Promise((resolve) => held.push(() => resolve(reply)))
}

const marginReads = () => post.mock.calls.filter(([url]) => url === '/margin')
const line = () => screen.getByTestId('margin-line')

async function wait(ms: number) {
  await act(async () => {
    await vi.advanceTimersByTimeAsync(ms)
  })
}

beforeEach(() => {
  vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout'] })
  margins = { 65: 6200.25, 130: 12345.5, 195: 18500 }
  held = []
  slow = false
  post.mockReset().mockImplementation(answer)
})

afterEach(() => {
  vi.useRealTimers()
})

describe('the margin line', () => {
  it('reads the margin 400 ms after the ticket opens, and the funds at once', async () => {
    render(<MarginLine apiKey="k" order={ORDER} sandbox={false} />)
    await wait(0)
    expect(marginReads()).toHaveLength(0)
    expect(line()).toHaveTextContent('Margin needed reading · available ₹2,50,000.00')
    await wait(MARGIN_SETTLE_MS)
    expect(line()).toHaveTextContent('Margin needed ₹12,345.50 · available ₹2,50,000.00')
    expect(marginReads()[0][1]).toMatchObject({
      apikey: 'k',
      positions: [
        {
          symbol: 'NIFTY25NOV2625800PE',
          exchange: 'NFO',
          action: 'SELL',
          quantity: '130',
          product: 'MIS',
          pricetype: 'MARKET',
          price: '0',
        },
      ],
    })
  })

  it('reads once after a burst of changes, saying the old figure is updating meanwhile', async () => {
    const view = render(<MarginLine apiKey="k" order={ORDER} sandbox={false} />)
    await wait(MARGIN_SETTLE_MS)
    view.rerender(<MarginLine apiKey="k" order={{ ...ORDER, quantity: 65 }} sandbox={false} />)
    await wait(200)
    view.rerender(<MarginLine apiKey="k" order={{ ...ORDER, quantity: 195 }} sandbox={false} />)
    expect(line()).toHaveTextContent('Margin needed ₹12,345.50 updating')
    await wait(MARGIN_SETTLE_MS)
    expect(marginReads()).toHaveLength(2)
    expect(line()).toHaveTextContent('Margin needed ₹18,500.00 · available')
    expect(line()).not.toHaveTextContent('updating')
    expect(post.mock.calls.filter(([url]) => url === '/funds')).toHaveLength(1)
  })

  it('keeps one read in flight, then reads the order as it stands, not each step', async () => {
    slow = true
    const view = render(<MarginLine apiKey="k" order={ORDER} sandbox={false} />)
    await wait(MARGIN_SETTLE_MS)
    expect(marginReads()).toHaveLength(1)
    view.rerender(<MarginLine apiKey="k" order={{ ...ORDER, quantity: 65 }} sandbox={false} />)
    await wait(MARGIN_SETTLE_MS)
    view.rerender(<MarginLine apiKey="k" order={{ ...ORDER, quantity: 195 }} sandbox={false} />)
    await wait(MARGIN_SETTLE_MS)
    expect(marginReads()).toHaveLength(1)
    await act(async () => held.shift()?.())
    await wait(0)
    expect(marginReads()).toHaveLength(2)
    expect(marginReads()[1][1]).toMatchObject({ positions: [{ quantity: '195' }] })
    await act(async () => held.shift()?.())
    await wait(0)
    expect(line()).toHaveTextContent('Margin needed ₹18,500.00')
  })

  it('says "not read" for a margin or funds it could not read, never ₹0', async () => {
    post.mockImplementation((url: string) =>
      url === '/funds'
        ? Promise.resolve({
            data: {
              status: 'success',
              data: { availablecash: '0.00', collateral: '0.00', utiliseddebits: '0.00' },
            },
          })
        : Promise.reject(new Error('timeout of 10000ms exceeded'))
    )
    render(<MarginLine apiKey="k" order={ORDER} sandbox={false} />)
    await wait(MARGIN_SETTLE_MS)
    expect(line()).toHaveTextContent('Margin needed not read · available not read')
    expect(line()).not.toHaveTextContent('₹0')
  })

  it('reads nothing for an order not filled in: a limit with no price', async () => {
    render(
      <MarginLine apiKey="k" order={{ ...ORDER, pricetype: 'LIMIT', price: 0 }} sandbox={false} />
    )
    await wait(MARGIN_SETTLE_MS * 2)
    expect(marginReads()).toHaveLength(0)
    expect(line()).toHaveTextContent('Margin needed - · available')
  })

  it('says the funds are the sandbox’s in Analyze mode', async () => {
    render(<MarginLine apiKey="k" order={ORDER} sandbox />)
    await wait(MARGIN_SETTLE_MS)
    expect(line()).toHaveTextContent('available in the sandbox ₹2,50,000.00')
  })
})
