/**
 * v0.18.0 verification V3 (M6.1): the passed-trigger line of Repeat's ticket has words of its own
 * in Analyze mode and in Live, and nothing else changes. Golden vectors
 * (`passed_trigger_vectors.json`): every Repeat case of a stop the last price has passed (sell and
 * buy, SL-M, an SL whose limit the price is within or past, one with no limit price (the ticket
 * takes the last price), a trigger at the price and one off the tick, NSE cash and an NFO option).
 * `sends` is the request Place sent for each on v0.17.0, recorded in both modes through the real
 * ticket before the words changed; here each case is placed again in each mode and must send that
 * very request. `says` is the line in each mode.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { tradingApi } from '@/api/trading'
import { useThemeStore } from '@/stores/themeStore'
import { RepeatTicket } from '@/sudarshan/books/cards/OrderSheet'
import type { BookOrder } from '@/sudarshan/books/orderFold'
import { cleanup, render, screen, userEvent, waitFor, within } from '@/test/test-utils'
import vectors from './passed_trigger_vectors.json'

const quote = vi.hoisted(() => ({ ltp: 0, contract: { lotSize: 1, tickSize: 0.05 } }))
vi.mock('@/sudarshan/watchlist/api', () => ({ contractOf: async () => quote.contract }))
vi.mock('@/api/trading', async () => {
  const actual = await vi.importActual<typeof import('@/api/trading')>('@/api/trading')
  return { ...actual, tradingApi: { ...actual.tradingApi, placeOrder: vi.fn() } }
})
vi.mock('@/utils/toast', () => ({ showToast: { error: vi.fn(), success: vi.fn() } }))
vi.mock('@/stores/authStore', () => ({ useAuthStore: () => ({ apiKey: 'test-api-key' }) }))
vi.mock('@/hooks/useLiveQuote', () => ({
  useLiveQuote: () => ({
    data: { ltp: quote.ltp, close: quote.ltp, change: 0, changePercent: 0, depth: undefined },
    isLive: false,
    isConnected: false,
    isLoading: false,
    isPaused: false,
    isFallbackMode: false,
    dataSource: 'none',
    refresh: async () => {},
  }),
}))

const placeOrder = vi.mocked(tradingApi.placeOrder)
const MODES = [
  ['Analyze', 'analyzer', 'analyze'],
  ['Live', 'live', 'live'],
] as const

type Case = (typeof vectors.cases)[number]

async function open(vector: Case, appMode: 'analyzer' | 'live') {
  useThemeStore.setState({ appMode })
  quote.ltp = vector.ltp
  quote.contract = vector.contract
  render(
    <RepeatTicket
      order={vector.order as unknown as BookOrder}
      apiKey="test-api-key"
      onClose={() => {}}
    />
  )
  return screen.findByRole('dialog', { name: /^Place Order/ })
}

beforeEach(() => {
  placeOrder.mockReset()
  placeOrder.mockResolvedValue({ status: 'success', orderid: 'ORD-3' } as never)
})

afterEach(() => {
  cleanup()
  useThemeStore.setState({ appMode: 'live' })
})

describe.each(MODES)('Repeat of a passed stop in %s', (_, appMode, key) => {
  it.each(vectors.cases)('$name: Place sends what it sent on v0.17.0', async (vector) => {
    const ticket = await open(vector, appMode)
    await userEvent.click(within(ticket).getByRole('button', { name: /^Place (BUY|SELL) Order$/ }))
    await waitFor(() => expect(placeOrder).toHaveBeenCalledTimes(1))
    expect(placeOrder.mock.calls[0][0]).toEqual(vector.sends)
  })

  it.each(vectors.cases)('$name: says so before Place, in its own words', async (vector) => {
    const ticket = await open(vector, appMode)
    const line = within(ticket).getByTestId('ticket-trigger-passed')
    expect(line).toHaveTextContent(vector.says[key])
    const place = within(ticket).getByRole('button', { name: /^Place (BUY|SELL) Order$/ })
    expect(place.getAttribute('aria-describedby')?.split(' ')).toContain(line.id)
    expect(placeOrder).not.toHaveBeenCalled()
  })
})

describe('the words of each mode', () => {
  it('in Analyze say what the sandbox does, and in Live never say what Upstox will do', () => {
    for (const { says } of vectors.cases) {
      expect(says.analyze).toMatch(/: in the sandbox this stop would (fill|trigger) at once/)
      expect(says.analyze).not.toMatch(/Upstox/)
      expect(says.live).toMatch(/: Upstox may refuse such a stop or (fill|trigger) it at once/)
      expect(says.live).toMatch(/ To close a position now, use Market or Limit\.$/)
      expect(says.live).not.toMatch(/would|sandbox/)
      for (const words of [says.analyze, says.live]) {
        expect(words).toMatch(
          /^The last price [\d.]+ (has already passed|is already at) the trigger/
        )
        expect(words).not.toMatch(/\b(paper|virtual|arm|armed|arming|real-time)\b|\b[45]\d\d\b/i)
      }
    }
  })
})
