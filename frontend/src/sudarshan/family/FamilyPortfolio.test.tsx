// SUDARSHAN P6 D6: the viewer's shared-portfolios screen and the base-path option on the owner's
// portfolio client. Synthetic figures only.
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { cleanup, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes } from 'react-router'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { MarketDataProvider } from '@/contexts/MarketDataContext'
import { getFamilyManager, resetFamilyManagerForTests } from './familyManager'
import FamilyPortfolio from './pages/FamilyPortfolio'
import { allocationOf, holdingsOf, summaryLines, sumOf, totalsOf } from './portfolioView'
import { FakeSocket, ok, routeFetch } from './testing/fixtures'

const tab = (id: number, name: string, level: 'view' | 'restricted' = 'view') => ({
  id,
  name,
  kind: 'custom',
  level,
  expires_at: null,
})

const live = (value?: number, gain?: number, today?: number) => ({
  rows: [],
  totals: {
    ...(value === undefined ? {} : { value_paise: value }),
    ...(gain === undefined ? {} : { overall_gain_paise: gain }),
    ...(today === undefined ? {} : { today: { paise: today, pct: '0.5000' } }),
  },
})

const portfolio = (over: Record<string, unknown> = {}) => ({
  read_at: Date.now() - 4_000,
  state: 'ok',
  words: null,
  hidden: [],
  tabs: [tab(1, 'Long term'), tab(2, 'Safe', 'restricted')],
  summary: {
    entries: [
      { tab: { id: 1, name: 'Long term' }, live: live(100_000, 10_000, 500) },
      { tab: { id: 2, name: 'Safe' }, live: live(50_000, 5_000, 250) },
    ],
  },
  ...over,
})

const tabAnswer = (id: number, view: unknown, over: Record<string, unknown> = {}) => ({
  tab: tab(id, id === 1 ? 'Long term' : 'Safe'),
  read_at: Date.now() - 3_000,
  state: 'ok',
  words: null,
  hidden: [],
  view,
  ...over,
})

const holdingRow = (name: string) => ({ asset: { id: name, name, symbol: name } })

function mount(path = '/family/portfolio') {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={client}>
      <MarketDataProvider manager={getFamilyManager()}>
        <MemoryRouter initialEntries={[path]}>
          <Routes>
            <Route path="/family/portfolio" element={<FamilyPortfolio />} />
          </Routes>
        </MemoryRouter>
      </MarketDataProvider>
    </QueryClientProvider>
  )
}

beforeEach(() => {
  FakeSocket.reset()
  vi.stubGlobal('WebSocket', FakeSocket)
})
afterEach(() => {
  cleanup()
  resetFamilyManagerForTests()
  vi.unstubAllGlobals()
})

describe('the reader', () => {
  it('absent is hidden, null is not known, and neither is 0', () => {
    expect(totalsOf(live()).value).toBeUndefined()
    expect(totalsOf({ totals: { value_paise: null } }).value).toBeNull()
    expect(totalsOf(live(12_345)).value).toBeCloseTo(123.45)
    expect(totalsOf(null).value).toBeUndefined()
  })

  it('a sum over a hidden line is hidden, never a smaller sum', () => {
    const lines = summaryLines({
      entries: [
        { tab: { id: 1, name: 'A' }, live: live(100) },
        { tab: { id: 2, name: 'B' }, live: live() },
      ],
    })
    expect(sumOf(lines, (f) => f.value)).toBeUndefined()
    expect(sumOf(lines.slice(0, 1), (f) => f.value)).toBe(1)
  })

  it('leaves out what it does not know the shape of', () => {
    expect(summaryLines({ entries: [{ nonsense: true }, 7] })).toEqual([])
    expect(holdingsOf({ rows: [{ asset: {} }, null] })).toEqual([])
    expect(allocationOf({ allocation: { by_class: [{ label: 'Equity', holdings: 2 }] } })).toEqual([
      { label: 'Equity', holdings: 2, value: undefined, pct: undefined },
    ])
  })
})

describe('Portfolio screen', () => {
  it('shows the summary first, with the sums', async () => {
    routeFetch({
      '/family/api/portfolio/tabs/1': ok(tabAnswer(1, { rows: [], totals: live(1).totals })),
      '/family/api/portfolio': ok(portfolio()),
    })
    mount()
    const value = await screen.findByTestId('shared-value')
    await waitFor(() => expect(value.textContent).toContain('1,500.00'))
    expect(screen.getByTestId('shared-today').textContent).toContain('7.50')
    expect(screen.getByRole('heading', { name: 'Shared portfolios' })).toBeTruthy()
  })

  it('shows Hidden where the mask took the figures, never 0', async () => {
    routeFetch({
      '/family/api/portfolio/tabs/1': ok(tabAnswer(1, { rows: [], totals: {} })),
      '/family/api/portfolio': ok(
        portfolio({
          summary: {
            entries: [{ tab: { id: 1, name: 'Long term' }, live: { rows: [], totals: {} } }],
          },
        })
      ),
    })
    mount()
    const value = await screen.findByTestId('shared-value')
    expect(value.textContent).toContain('Hidden')
    expect(value.textContent).not.toMatch(/0\.00/)
    const body = await screen.findByTestId('tab-value')
    expect(body.textContent).toContain('Hidden')
    expect(screen.getByTestId('tab-gain').textContent).toContain('Hidden')
  })

  it('a restricted tab shows holdings and counts, and no money', async () => {
    routeFetch({
      '/family/api/portfolio/tabs/2': ok(
        tabAnswer(2, {
          rows: [holdingRow('ALPHA'), holdingRow('BETA')],
          allocation: { by_class: [{ label: 'Equity', holdings: 2 }] },
        })
      ),
      '/family/api/portfolio': ok(portfolio()),
    })
    mount('/family/portfolio?tab=2')
    const holdings = await screen.findByRole('list', { name: 'Holdings' })
    expect(within(holdings).getByText('ALPHA')).toBeTruthy()
    const allocation = screen.getByRole('list', { name: 'Allocation' })
    expect(allocation.textContent).toContain('2 holdings')
    expect(screen.queryByTestId('tab-value')).toBeNull()
    expect(screen.getAllByText(/limited detail/i).length).toBeGreaterThan(0)
    expect(allocation.textContent).not.toMatch(/₹/)
  })

  it('marks the selected tab by weight and a check, never by colour alone (rule 6)', async () => {
    routeFetch({
      '/family/api/portfolio/tabs/2': ok(tabAnswer(2, { rows: [holdingRow('ALPHA')] })),
      '/family/api/portfolio': ok(portfolio()),
    })
    mount('/family/portfolio?tab=2')
    await screen.findByRole('list', { name: 'Holdings' })
    const tabs = screen.getAllByRole('tab')
    const chosen = tabs.filter((tab) => tab.getAttribute('aria-selected') === 'true')
    expect(chosen).toHaveLength(1)
    for (const tab of tabs) {
      const selected = tab.getAttribute('aria-selected') === 'true'
      expect(tab.querySelector('[data-mark="selected"]') !== null).toBe(selected)
      expect(tab.className.includes('font-bold')).toBe(selected)
    }
  })

  it('has no way to change anything', async () => {
    const router = routeFetch({
      '/family/api/portfolio/tabs/1': ok(tabAnswer(1, { rows: [holdingRow('ALPHA')] })),
      '/family/api/portfolio': ok(portfolio()),
    })
    mount()
    await screen.findByRole('list', { name: 'Holdings' })
    for (const button of [...screen.queryAllByRole('button'), ...screen.getAllByRole('tab')]) {
      const name = button.getAttribute('aria-label') ?? button.textContent ?? ''
      expect(name).not.toMatch(
        /new tab|add|import|customi[sz]e|settings|delete|edit|buy|sell|rename/i
      )
    }
    expect(router.calls.every((call) => call.method === 'GET')).toBe(true)
    expect(router.urls().every((url) => url.startsWith('/family/api/'))).toBe(true)
  })

  it('changes tab and reads that tab', async () => {
    const router = routeFetch({
      '/family/api/portfolio/tabs/1': ok(tabAnswer(1, { rows: [holdingRow('ALPHA')] })),
      '/family/api/portfolio/tabs/2': ok(tabAnswer(2, { rows: [holdingRow('BETA')] })),
      '/family/api/portfolio': ok(portfolio()),
    })
    mount()
    await screen.findByText('ALPHA')
    await userEvent.click(screen.getByRole('tab', { name: /Safe/ }))
    await screen.findByText('BETA')
    expect(router.urls()).toContain('/family/api/portfolio/tabs/2')
  })

  it('says it is a read at a time, never Live, and gives the service words', async () => {
    routeFetch({
      '/family/api/portfolio/tabs/1': ok(tabAnswer(1, { rows: [] })),
      '/family/api/portfolio': ok(
        portfolio({
          state: 'unreachable',
          words: 'The account cannot be read right now; prices still move.',
        })
      ),
    })
    mount()
    expect(await screen.findByText(/cannot be read right now/)).toBeTruthy()
    expect((await screen.findAllByText(/Portfolios? from \d\d:\d\d:\d\d/)).length).toBeGreaterThan(
      0
    )
    expect(screen.queryByText(/^Live$/)).toBeNull()
  })

  it('says Not shared with you for a tab no longer shared', async () => {
    routeFetch({
      '/family/api/portfolio/tabs/1': ok(tabAnswer(1, null, { state: 'not_shared' })),
      '/family/api/portfolio': ok(portfolio()),
    })
    mount()
    expect(await screen.findAllByText('Not shared with you.')).not.toHaveLength(0)
  })
})

describe('portfolioCall base path option', () => {
  it('sends to the base it is given, and to the owner prefix when none is given', async () => {
    vi.resetModules()
    const get = vi.fn().mockResolvedValue({
      data: { status: 'success', enabled: true, data: { tab: 1 } },
    })
    vi.doMock('@/api/client', () => ({ webClient: { get, request: vi.fn() } }))
    const { portfolioCall } = await import('@/sudarshan/portfolio/api')
    await portfolioCall('tab_get', { id: 3 } as never)
    await portfolioCall('tab_get', { id: 3 } as never, undefined, { base: '/family/api/portfolio' })
    expect(get.mock.calls[0][0]).toBe('/sudarshan/portfolio/api/tabs/3')
    expect(get.mock.calls[1][0]).toBe('/family/api/portfolio/tabs/3')
    vi.doUnmock('@/api/client')
  })
})
