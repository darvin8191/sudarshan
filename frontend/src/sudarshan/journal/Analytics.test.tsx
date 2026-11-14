/**
 * The Analytics part in jsdom, the bridge a stand-in. Pinned: the six tiles with their words
 * (vector M1's figures), a ratio with no number saying why, the filters held in the address and
 * sent as the request, the mode on every read, the sentences for empty and failed, and the re-read
 * after an order update.
 */
import { act, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const api = vi.hoisted(() => ({ data: vi.fn() }))
vi.mock('./api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('./api')>()),
  journalData: api.data,
}))
const socket = vi.hoisted(() => {
  const handlers = new Map<string, Set<(...args: unknown[]) => void>>()
  return {
    connected: true,
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
  }
})
vi.mock('@/components/socket/SocketProvider', () => ({
  useSocketContext: () => ({ playAlertSound: () => {}, socket }),
}))
vi.mock('@/sudarshan/dashboard/MarketCalendar', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/sudarshan/dashboard/MarketCalendar')>()),
  MarketCalendarSource: () => null,
}))
vi.mock('./EquityChart', () => ({ EquityChart: () => <p data-testid="equity">equity chart</p> }))

import { renderBook, where } from '@/test/bookPages'
import { fakeClock } from '@/test/frames'
import { Analytics } from './Analytics'
import { EXPIRY_WORDS } from './filters'
import { analytics, metrics } from './fixtures'
import type { Analytics as Answer, Mode, Playbook, Tag } from './types'

const TAGS: Tag[] = [
  { id: 2, kind: 'mistake', name: 'Chased entry', colour: 'gold', archived: false, version: 1 },
]
const PLAYBOOKS: Playbook[] = [
  {
    id: 4,
    name: 'Opening range',
    name_alt: null,
    description: '',
    rules: [],
    default_risk_paise: null,
    archived: false,
    version: 1,
  },
]

function serve(answer: Answer = analytics()) {
  api.data.mockImplementation(async (op: string) => {
    if (op === 'tags_list') return TAGS
    if (op === 'playbooks_list') return PLAYBOOKS
    if (op === 'analytics') return answer
    throw new Error(`unexpected op ${op}`)
  })
}
const reads = () => api.data.mock.calls.filter(([op]) => op === 'analytics').map(([, req]) => req)
const show = (mode: Mode = 'live', at = '/journal?view=analytics') =>
  renderBook('/journal', <Analytics mode={mode} />, [at])
const tile = (label: string) =>
  screen.getAllByTestId('tile').find((t) => t.textContent?.startsWith(label)) as HTMLElement

beforeEach(() => {
  fakeClock()
  api.data.mockReset()
})

describe('Analytics', () => {
  it('shows six tiles with their figures, signs and words', async () => {
    serve()
    show()
    await screen.findAllByTestId('tile')
    expect(screen.getAllByTestId('tile')).toHaveLength(6)
    expect(tile('Net').textContent).toContain('+₹397.50')
    expect(tile('Net').textContent).toContain('6 trades')
    expect(within(tile('Net')).getByText('Profit')).toBeTruthy()
    expect(tile('Win rate').textContent).toContain('60.00%')
    expect(tile('Win rate').textContent).toContain('3 Profit, 2 Loss, 1 Flat')
    expect(tile('Profit factor').textContent).toContain('1.30')
    expect(tile('Expectancy').textContent).toContain('+₹66.25')
    expect(tile('Average R').textContent).toContain('+0.13 R')
    expect(tile('Average R').textContent).toContain('R known for 6 of 6')
    expect(tile('Max drawdown').textContent).toContain('−₹1,000')
    expect(tile('Max drawdown').textContent).toContain('−2.00% of capital')
    expect(tile('Max drawdown').querySelector('[data-direction="down"]')).toBeTruthy()
  })

  it('says why a ratio has no number, and what to do for R', async () => {
    serve(
      analytics({
        metrics: metrics({
          profit_factor: null,
          profit_factor_state: 'no_losing_trades',
          win_rate: null,
          win_rate_state: 'no_trades',
          r_known: 0,
          r_avg: null,
        }),
      })
    )
    show()
    await screen.findAllByTestId('tile')
    expect(tile('Profit factor').textContent).toContain('No losing trades')
    expect(tile('Win rate').textContent).toContain('No trades')
    expect(tile('Average R').textContent).toContain('Add a risk to a trade to see R')
  })

  it('shows the curve, calendar, heat map, hold time, strategies, expiry days and charges', async () => {
    serve()
    show()
    expect(await screen.findByTestId('equity')).toBeTruthy()
    expect(screen.getByRole('region', { name: 'Calendar' })).toBeTruthy()
    expect(screen.getByRole('region', { name: 'Heat map' })).toBeTruthy()
    const hold = screen.getByRole('region', { name: 'Hold time' })
    expect(within(hold).getAllByTestId('hold-row')).toHaveLength(6)
    expect(within(hold).getByText('Under 5 min')).toBeTruthy()
    const strategies = screen.getByRole('region', { name: 'By strategy' })
    expect(within(strategies).getByText('ORB')).toBeTruthy()
    expect(within(strategies).getByText('75.00%')).toBeTruthy()
    expect(strategies.textContent).toContain('−₹1,002.50')
    expect(screen.queryByRole('region', { name: 'By tag' })).toBeNull()
    const expiry = screen.getByRole('region', { name: 'Expiry days' })
    expect(within(expiry).getByText('Index expiry day')).toBeTruthy()
    expect(screen.getByTestId('charges-line').textContent).toContain(
      'Charges took 3.92% of the gross profit'
    )
    expect(screen.getByTestId('charges-line').textContent).toContain('est.')
  })

  it('keeps every net whole on a phone: two columns, the net on one line, nothing scrolled sideways (review B1)', async () => {
    serve(
      analytics({
        by_strategy: [
          {
            name: 'ORB breakout long name',
            metrics: metrics({ trips: 30, net_paise: 12_345_678, win_rate: '62.50' }),
          },
          { name: '', metrics: metrics({ trips: 12, net_paise: -123_456_700 }) },
        ],
        by_tag: [{ name: '', metrics: metrics({ trips: 5, net_paise: -500_000 }) }],
      })
    )
    show()
    const strategies = await screen.findByRole('region', { name: 'By strategy' })
    for (const region of [strategies, screen.getByRole('region', { name: 'By tag' })]) {
      const table = within(region).getByRole('table')
      expect(table.className).not.toContain('min-w-')
      expect(region.querySelector('.overflow-x-auto')).toBeNull()
      const heads = within(table)
        .getAllByRole('columnheader')
        .map((h) => h.textContent)
      expect(heads.at(-1)).toBe('Net')
      for (const cell of within(table).getAllByTestId('net')) {
        expect(cell.className).toContain('whitespace-nowrap')
      }
    }
    const [orb, unnamed] = within(within(strategies).getByRole('table'))
      .getAllByRole('row')
      .slice(1)
    expect(orb.textContent).toContain('ORB breakout long name')
    expect(orb.textContent).toContain('30 trades · Win rate 62.50%')
    expect(within(orb).getByTestId('net').textContent).toBe('+₹1,23,456.78')
    expect(unnamed.textContent).toContain('No strategy')
    expect(within(unnamed).getByTestId('net').textContent).toBe('−₹12,34,567.00')
    // A tag row with no name is not a strategy (review L4).
    const tags = screen.getByRole('region', { name: 'By tag' })
    expect(tags.textContent).toContain('No tag')
    expect(tags.textContent).not.toContain('No strategy')
    // The expiry tables take the same two columns.
    const expiry = screen.getByRole('region', { name: 'Expiry days' })
    expect(expiry.querySelector('.overflow-x-auto')).toBeNull()
    expect(within(expiry).getAllByTestId('net').length).toBe(4)
  })

  it('says there is no charges figure without a winning trade', async () => {
    serve(analytics({ metrics: metrics({ charges_impact_pct: null }) }))
    show()
    expect(await screen.findByText(/no figure for what charges took/)).toBeTruthy()
  })

  it('names the beta card from the service’s words while it is not ready', async () => {
    serve(
      analytics({
        beta: {
          beta: null,
          correlation: null,
          state: 'short',
          correlation_state: 'short',
          days: 9,
          need: 20,
          note: 'Shown after 20 trading days (from 9 Dec 2026).',
        },
      })
    )
    show()
    expect(await screen.findByText('Shown after 20 trading days (from 9 Dec 2026).')).toBeTruthy()
  })
})

describe('the filters', () => {
  it('start at the month for the live mode, and send the mode', async () => {
    serve()
    show('live')
    await screen.findAllByTestId('tile')
    expect(reads()[0]).toEqual({ mode: 'live', period: 'month' })
    expect(screen.getByRole('tab', { name: 'Month' }).getAttribute('aria-selected')).toBe('true')
  })

  it('send the sandbox mode, and never the live one', async () => {
    serve()
    show('sandbox')
    await screen.findAllByTestId('tile')
    for (const req of reads()) expect(req.mode).toBe('sandbox')
  })

  it('move with a tap, are held in the address, and ask again', async () => {
    const user = userEvent.setup()
    serve()
    show()
    await screen.findAllByTestId('tile')
    await user.click(screen.getByRole('tab', { name: 'Week' }))
    await waitFor(() => expect(where()).toBe('/journal?view=analytics&period=week'))
    await waitFor(() => expect(reads().at(-1)).toEqual({ mode: 'live', period: 'week' }))
    await user.selectOptions(screen.getByLabelText('Strategy'), 'ORB')
    await user.selectOptions(screen.getByLabelText('Playbook'), '4')
    await user.selectOptions(screen.getByLabelText('Tag'), '2')
    await user.selectOptions(screen.getByLabelText('Expiry'), 'expiry')
    await waitFor(() =>
      expect(reads().at(-1)).toEqual({
        mode: 'live',
        period: 'week',
        strategy: 'ORB',
        playbook: 4,
        tag: 2,
        expiry: 'expiry',
      })
    )
    expect(where()).toContain('strategy=ORB')
    // the service's meaning: opened on an index's expiry day or the contract's own
    const expiry = screen.getByLabelText('Expiry') as HTMLSelectElement
    expect(expiry.selectedOptions[0].textContent).toBe('Opened on an expiry day')
    expect(screen.getByText(EXPIRY_WORDS)).toBeTruthy()
    await user.click(screen.getByRole('button', { name: 'Clear 4 filters' }))
    await waitFor(() => expect(reads().at(-1)).toEqual({ mode: 'live', period: 'week' }))
    expect(screen.queryByText(EXPIRY_WORDS)).toBeNull()
  })

  it('open on a filter in the address', async () => {
    serve()
    show('live', '/journal?view=analytics&period=fy&strategy=ORB&expiry=non_expiry')
    await screen.findAllByTestId('tile')
    expect(reads()[0]).toEqual({
      mode: 'live',
      period: 'fy',
      strategy: 'ORB',
      expiry: 'non_expiry',
    })
    expect((screen.getByLabelText('Expiry') as HTMLSelectElement).value).toBe('non_expiry')
  })

  it('keep the strategies seen, so a filter on one still lists the others', async () => {
    const user = userEvent.setup()
    serve()
    show()
    await screen.findAllByTestId('tile')
    await user.selectOptions(screen.getByLabelText('Strategy'), 'Gap fade')
    serve(analytics({ by_strategy: [analytics().by_strategy[1]] }))
    await waitFor(() => expect(reads().at(-1)?.strategy).toBe('Gap fade'))
    const options = within(screen.getByLabelText('Strategy'))
      .getAllByRole('option')
      .map((o) => o.textContent)
    expect(options).toEqual(['Every strategy', 'Gap fade', 'ORB'])
  })

  it('ask for both days of a custom period before reading', async () => {
    const user = userEvent.setup()
    serve()
    show()
    await screen.findAllByTestId('tile')
    const before = reads().length
    await user.click(screen.getByRole('tab', { name: 'Custom' }))
    expect(await screen.findByText('Pick both days to see a custom period.')).toBeTruthy()
    expect(reads().length).toBe(before)
    await user.type(screen.getByLabelText('From'), '2026-11-01')
    await user.type(screen.getByLabelText('To'), '2026-11-13')
    await waitFor(() =>
      expect(reads().at(-1)).toEqual({ mode: 'live', from: 20261101, to: 20261113 })
    )
  })
})

describe('the states', () => {
  it('says there are no trades in the period, with the next step', async () => {
    serve(analytics({ metrics: metrics({ trips: 0 }) }))
    show()
    expect(
      await screen.findByText('No closed trades in this period. Change the period or the filters.')
    ).toBeTruthy()
    expect(screen.queryAllByTestId('tile')).toHaveLength(0)
  })

  it('says why it could not read, with the next step, and tries again', async () => {
    const user = userEvent.setup()
    api.data.mockRejectedValue(new Error('x'))
    show()
    await screen.findByRole('button', { name: /Try again/ })
    serve()
    await user.click(screen.getByRole('button', { name: /Try again/ }))
    expect((await screen.findAllByTestId('tile')).length).toBe(6)
  })

  it('re-reads 3 s after an order update, once for a burst', async () => {
    serve()
    show()
    await screen.findAllByTestId('tile')
    const before = reads().length
    for (let n = 0; n < 4; n += 1) {
      act(() => socket.fire('order_update', {}))
      await act(async () => {
        await vi.advanceTimersByTimeAsync(200)
      })
    }
    expect(reads().length).toBe(before)
    await act(async () => {
      await vi.advanceTimersByTimeAsync(3100)
    })
    await waitFor(() => expect(reads().length).toBe(before + 1))
  })
})
