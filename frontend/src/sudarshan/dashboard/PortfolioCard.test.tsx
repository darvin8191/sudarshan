/**
 * The dashboard's Portfolio card (M3.11, design 8.3): one row per tab (Upstox first, then at most
 * four pinned tabs), value, today and overall with signs and arrows, prices from one feed
 * subscription over the rows' instruments (at most 200), the Upstox row named as the real account
 * in Analyze mode, "See all" to /portfolio, and the switch that keeps it off by default.
 */
import { act, render, screen, waitFor, within } from '@testing-library/react'
import { MemoryRouter } from 'react-router'
import { beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('@/lib/MarketDataManager', () => import('@/test/marketDataHarness'))
const api = vi.hoisted(() => ({ call: vi.fn() }))
vi.mock('@/sudarshan/portfolio/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/sudarshan/portfolio/api')>()),
  portfolioCall: api.call,
}))
vi.mock('@/components/socket/SocketProvider', () => ({
  useSocketContext: () => ({ playAlertSound: () => {}, socket: null }),
}))

import { useThemeStore } from '@/stores/themeStore'
import { tab } from '@/sudarshan/portfolio/sheets/fixtures'
import { FIXED, READ_AT, row } from '@/sudarshan/portfolio/tab/fixtures'
import type { PortfolioSummary, SummaryEntry } from '@/sudarshan/portfolio/types'
import { holding } from '@/sudarshan/portfolio/upstox/fixtures'
import { feed } from '@/test/marketDataHarness'
import {
  entriesShown,
  FILL_FAILED,
  instrumentsOf,
  PINNED_SHOWN,
  PortfolioCard,
  rowsOf,
} from './PortfolioCard'
import { setPortfolioCard, usePortfolioCard } from './portfolioCard'

function custom(
  id: number,
  name: string,
  rows: ReturnType<typeof row>[],
  pinned = true
): SummaryEntry {
  return {
    tab: tab(id, name, { pinned }),
    live: { rows, fixed: FIXED, totals: {} as never },
    upstox: null,
  }
}

function upstoxEntry(
  holdings = [holding()],
  state: 'ok' | 'stale' | 'unavailable' | 'reading' = 'ok'
): SummaryEntry {
  return {
    tab: tab(1, 'Upstox', { system: true, kind: 'upstox' }),
    live: null,
    upstox: {
      state,
      read_at: state === 'unavailable' || state === 'reading' ? null : READ_AT,
      note: state === 'ok' ? null : 'Upstox did not answer. Figures from 10:41:05.',
      holdings,
    },
  }
}

function summary(entries: SummaryEntry[]): PortfolioSummary {
  return { read_at: READ_AT, today: 20261104, entries }
}

function show(data: PortfolioSummary) {
  api.call.mockImplementation(async (op: string) => {
    if (op === 'summary') return { enabled: true, data }
    throw new Error(`unexpected op ${op}`)
  })
  return render(
    <MemoryRouter>
      <PortfolioCard marketOpen={() => true} />
    </MemoryRouter>
  )
}

beforeEach(() => {
  api.call.mockReset()
  feed.reset()
  useThemeStore.setState({ appMode: 'live' })
  setPortfolioCard(false)
})

describe('the Portfolio card', () => {
  it('shows the Upstox account and the pinned tabs with value, today and overall, and See all', async () => {
    show(summary([upstoxEntry(), custom(2, 'Gold', [row(1, 'Alpha', { price: 1_100_000 })])]))
    const card = await screen.findByRole('region', { name: 'Portfolio' })
    const rows = within(card).getAllByRole('listitem')
    expect(rows).toHaveLength(2)
    // Upstox: 10 shares at 1,500 = 15,000; today 10 x (1,500 - 1,480) = +200; overall +1,000.
    expect(within(rows[0]).getByText('Upstox')).toBeTruthy()
    expect(within(rows[0]).getByText('₹15,000.00')).toBeTruthy()
    expect(within(rows[0]).getByText('+₹200.00')).toBeTruthy()
    expect(within(rows[0]).getByText('+₹1,000.00')).toBeTruthy()
    // Gold: 10 units at 110 on a cost of 900: value 1,100, today +110 on 990, overall +200.
    expect(within(rows[1]).getByText('Gold')).toBeTruthy()
    expect(within(rows[1]).getByText('₹1,100.00')).toBeTruthy()
    expect(within(rows[1]).getByText('+₹110.00')).toBeTruthy()
    expect(within(rows[1]).getByText('+₹200.00')).toBeTruthy()
    expect(
      within(card)
        .getByRole('link', { name: /See all/ })
        .getAttribute('href')
    ).toBe('/portfolio')
    expect(api.call).toHaveBeenCalledWith('summary', {})
  })

  it('moves every row with the ticks of one subscription and says Real-time', async () => {
    show(summary([upstoxEntry(), custom(2, 'Gold', [row(1, 'ALPHA', { symbol: 'ALPHA' })])]))
    const card = await screen.findByRole('region', { name: 'Portfolio' })
    await waitFor(() => expect(feed.live().sort()).toEqual(['NSE:ALPHA:LTP', 'NSE:INFY:LTP']))
    act(() => {
      feed.push('INFY', 'NSE', { ltp: 1600 }) // 10 shares: 16,000
      feed.push('ALPHA', 'NSE', { ltp: 120 }) // 10 units: 1,200
    })
    await waitFor(() => expect(within(card).getByText('Real-time')).toBeTruthy())
    expect(within(card).getByText('₹16,000.00')).toBeTruthy()
    expect(within(card).getByText('₹1,200.00')).toBeTruthy()
  })

  it('names the Upstox row as the real account in Analyze mode', async () => {
    useThemeStore.setState({ appMode: 'analyzer' })
    show(summary([upstoxEntry()]))
    const card = await screen.findByRole('region', { name: 'Portfolio' })
    expect(within(card).getByText('Upstox account (real)')).toBeTruthy()
  })

  it('says Not read, never a zero, when Upstox could not be read, and keeps the other tabs', async () => {
    show(
      summary([
        upstoxEntry([], 'unavailable'),
        custom(2, 'Gold', [row(1, 'Alpha', { price: 1_100_000 })]),
      ])
    )
    const card = await screen.findByRole('region', { name: 'Portfolio' })
    const rows = within(card).getAllByRole('listitem')
    const upstox = rows[0].textContent ?? ''
    expect(upstox).toContain('Not read')
    expect(upstox).toContain('Upstox did not answer. Figures from 10:41:05.')
    expect(upstox).toContain('No good read yet')
    expect(upstox).not.toContain('₹0.00')
    expect(upstox).not.toContain('Today')
    expect(upstox).not.toContain('Overall')
    expect(within(rows[1]).getByText('₹1,100.00')).toBeTruthy() // the owner's own tab is whole
  })

  it('gives the time of the last good read for a row that is not read now', () => {
    const entries = [upstoxEntry([], 'unavailable')]
    const [shown] = rowsOf(entries, new Map(), READ_AT, false, READ_AT)
    expect(shown).toMatchObject({ status: 'unread', lastGood: 'Last good read 10:41:05' })
    const [never] = rowsOf(entries, new Map(), READ_AT, false, null)
    expect(never.lastGood).toBe('No good read yet')
    const [ok] = rowsOf([upstoxEntry()], new Map(), READ_AT, false, null)
    expect(ok).toMatchObject({ status: 'ok', lastGood: null })
  })

  it('shows the tabs and "Upstox account: reading" while Upstox is slow, then fills in', async () => {
    let release: (value: unknown) => void = () => {}
    api.call.mockImplementation(async (op: string) => {
      if (op === 'summary')
        return {
          enabled: true,
          data: summary([
            upstoxEntry([], 'reading'),
            custom(2, 'Gold', [row(1, 'Alpha', { price: 1_100_000 })]),
          ]),
        }
      if (op === 'upstox_book') {
        await new Promise((resolve) => {
          release = resolve
        })
        return {
          enabled: true,
          data: { holdings: { state: 'ok', read_at: READ_AT, note: null, data: [holding()] } },
        }
      }
      throw new Error(`unexpected op ${op}`)
    })
    render(
      <MemoryRouter>
        <PortfolioCard marketOpen={() => true} />
      </MemoryRouter>
    )
    const card = await screen.findByRole('region', { name: 'Portfolio' })
    const rows = within(card).getAllByRole('listitem')
    expect(within(rows[0]).getByText('Upstox account: reading')).toBeTruthy()
    expect(rows[0].textContent).not.toContain('₹')
    expect(within(rows[1]).getByText('₹1,100.00')).toBeTruthy() // the tabs do not wait
    expect(api.call).toHaveBeenCalledWith('upstox_book', { parts: ['holdings'] })
    expect(card.textContent).not.toMatch(/not answering/i)
    await act(async () => release(null))
    await waitFor(() => expect(within(card).getByText('₹15,000.00')).toBeTruthy())
    expect(within(card).queryByText('Upstox account: reading')).toBeNull()
  })

  it('says it in words about Upstox when the fill-in read fails, never that the service is silent', async () => {
    api.call.mockImplementation(async (op: string) => {
      if (op === 'summary')
        return { enabled: true, data: summary([upstoxEntry([], 'reading'), custom(2, 'Gold', [])]) }
      throw new Error('the bridge timed out')
    })
    render(
      <MemoryRouter>
        <PortfolioCard marketOpen={() => true} />
      </MemoryRouter>
    )
    const card = await screen.findByRole('region', { name: 'Portfolio' })
    await waitFor(() => expect(within(card).getByText(FILL_FAILED)).toBeTruthy())
    const upstox = within(card).getAllByRole('listitem')[0].textContent ?? ''
    expect(upstox).toContain('Not read')
    expect(upstox).not.toContain('₹0.00')
    expect(card.textContent).not.toMatch(/not answering|portfolio service/i)
  })

  it('shows at most four pinned tabs after the Upstox row, and subscribes at most 200 instruments', () => {
    const tabs = Array.from({ length: 6 }, (_, i) => custom(10 + i, `Tab ${i}`, []))
    const shown = entriesShown([...tabs, upstoxEntry(), custom(99, 'Not pinned', [], false)])
    expect(shown[0].tab.system).toBe(true)
    expect(shown).toHaveLength(1 + PINNED_SHOWN)
    expect(shown.map((e) => e.tab.name)).toEqual(['Upstox', 'Tab 0', 'Tab 1', 'Tab 2', 'Tab 3'])
    const many = Array.from({ length: 250 }, (_, i) =>
      holding({ symbol: `S${i}`, instrument_key: `k${i}` })
    )
    const shared = custom(2, 'Gold', [row(1, 'S0', { symbol: 'S0' })]) // the same instrument once
    expect(instrumentsOf([upstoxEntry(many), shared])).toHaveLength(200)
  })

  it('is nothing while the summary is being read, or has no tab to show', async () => {
    show(summary([]))
    await act(async () => {})
    expect(screen.queryByRole('region', { name: 'Portfolio' })).toBeNull()
  })
})

describe('the switch', () => {
  it('is off by default and remembers the choice on this device', () => {
    function Probe() {
      return <span>{usePortfolioCard() ? 'on' : 'off'}</span>
    }
    render(<Probe />)
    expect(screen.getByText('off')).toBeTruthy()
    act(() => setPortfolioCard(true))
    expect(screen.getByText('on')).toBeTruthy()
    expect(window.localStorage.getItem('sudarshan-portfolio-card')).toBe('1')
    act(() => setPortfolioCard(false))
    expect(window.localStorage.getItem('sudarshan-portfolio-card')).toBeNull()
  })
})
