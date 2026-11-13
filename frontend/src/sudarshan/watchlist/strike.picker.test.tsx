/**
 * F14: the strike picker. Real `LiveList`, `useLiveRows` and `useStrikeLadder` over the market-data
 * harness and the mock bridge; every `strikes` ask is counted.
 */
import { QueryClientProvider } from '@tanstack/react-query'
import { act, fireEvent, render, screen, within } from '@testing-library/react'
import { MemoryRouter } from 'react-router'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('@/lib/MarketDataManager', () => import('@/test/marketDataHarness'))
const bridge = vi.hoisted(() => ({
  current: null as null | ReturnType<typeof import('./v2/mockBridge').createBridge>,
}))
vi.mock('@/api/client', async () => {
  const { createBridge } = await import('./v2/mockBridge')
  bridge.current = createBridge()
  return { webClient: bridge.current.client, apiClient: { post: async () => ({ data: {} }) } }
})
vi.mock('@/api/trading', () => ({
  tradingApi: {
    getMultiQuotes: async (_key: string, symbols: Array<{ symbol: string; exchange: string }>) => ({
      status: 'success',
      results: symbols.map((each) => ({
        symbol: each.symbol,
        exchange: each.exchange,
        data: {
          ltp: 25900,
          prev_close: 25800,
          open: 25800,
          high: 0,
          low: 0,
          volume: 0,
          oi: 0,
          bid: 0,
          ask: 0,
        },
      })),
    }),
  },
}))
vi.mock('@/api/oi-profile', () => ({
  oiProfileApi: { getUnderlyings: async () => ({ status: 'success', underlyings: ['NIFTY'] }) },
}))

import { createQueryClient } from '@/app/providers'
import { fakeClock, frames } from '@/test/frames'
import { feed } from '@/test/marketDataHarness'
import type { FollowForm } from './followRows'
import { LiveList, type PickerProps } from './LiveList'
import { OPEN_GUARD } from './openGuard'
import { ASK_GAP_MS } from './strikePicker'
import type { Call } from './v2/mockBridge'

const T0 = Date.UTC(2026, 10, 13, 4, 42) // 10:12 IST
const b = () => bridge.current as NonNullable<typeof bridge.current>
const settle = async (ms = 0) => {
  await act(async () => {
    await vi.advanceTimersByTimeAsync(ms)
    frames.flush()
  })
}

const EXP = [
  // 24 Nov 2026 is November's monthly, the last NIFTY expiry of its month, as the server marks it.
  ['24NOV', '2026-11-24', 'Nearest 24 Nov', true],
  ['01DEC', '2026-12-01', '1 Dec', false],
  ['08DEC', '2026-12-08', '8 Dec', false],
  ['29DEC', '2026-12-29', '29 Dec (month)', true],
] as const
const sym = (day: string, strike: number, right: string) => `NIFTY${day}26${strike}${right}`
const contract = (day: string, iso: string, strike: number, right: string) => ({
  symbol: sym(day, strike, right),
  exchange: 'NFO',
  key: `NFO:${sym(day, strike, right)}`,
  name: 'NIFTY',
  lot: 65,
  tick: 0.05,
  expiry: iso,
  isin: null,
})
const atmFor = (p: number) => Math.ceil((p - 25) / 50) * 50

/** What the server's `strikes` op answers: the ladder around p (the middle, 25,900, with none). */
const strikesOp = (call: Call) => {
  const p = call.params.p === undefined ? null : Number(call.params.p)
  const chosen = EXP.find(([, iso]) => iso === call.params.exp) ?? EXP[0]
  const centre = p === null ? 25900 : atmFor(p)
  return {
    underlying: 'NIFTY',
    exchange: 'NSE_INDEX',
    expiries: EXP.map(([, date, label, month]) => ({ date, label, month })),
    expiry: chosen[1],
    atm: p === null ? null : centre,
    strikes: Array.from({ length: 21 }, (_, at) => {
      const strike = centre + (at - 10) * 50
      return {
        strike,
        atm: p !== null && strike === centre,
        ce: contract(chosen[0], chosen[1], strike, 'CE'),
        pe: contract(chosen[0], chosen[1], strike, 'PE'),
      }
    }),
    words: p === null ? 'NIFTY’s price is not known yet.' : '',
  }
}

interface Props {
  picker?: Partial<PickerProps> | null
  items?: Array<{ id: number; symbol: string; exchange: string; position: number }>
  v2?: boolean
}
function Page({ picker = {}, items = [], v2 = true }: Props) {
  return (
    <LiveList
      items={items}
      apiKey="k"
      isOpen={() => true}
      loading={false}
      editing={false}
      listName="Swing"
      openItemId={null}
      panel={{ 'aria-label': 'Watchlist' }}
      onOpen={() => {}}
      onMove={() => {}}
      onRemove={() => {}}
      onCloseSheet={() => {}}
      onOrder={() => {}}
      onAlert={() => {}}
      flagNames={v2 ? [] : undefined}
      picker={
        picker === null
          ? undefined
          : {
              underlying: 'NIFTY',
              exchange: 'NSE_INDEX',
              listName: 'Swing',
              full: false,
              onAdd: async () => {},
              onFollow: () => {},
              onClose: () => {},
              ...picker,
            }
      }
    />
  )
}
function mount(props: Props = {}) {
  const client = createQueryClient()
  const tree = (p: Props) => (
    <QueryClientProvider client={client}>
      <MemoryRouter>
        <Page {...p} />
      </MemoryRouter>
    </QueryClientProvider>
  )
  const out = render(tree(props))
  return { ...out, again: (p: Props) => out.rerender(tree(p)) }
}

const tick = (price: number) =>
  act(() => {
    feed.push('NIFTY', 'NSE_INDEX', { ltp: price })
    frames.flush()
  })
const asked = () => b().calls.filter((call) => call.op === 'strikes')
const picker = () => document.querySelector('[data-strike-picker]') as HTMLElement
const strikeRow = (strike: number) =>
  picker().querySelector(`[data-strike="${strike}"]`) as HTMLElement
const optionKeys = () => feed.live().filter((key) => key.startsWith('NFO:'))

beforeEach(() => {
  feed.reset()
  frames.install()
  fakeClock()
  vi.setSystemTime(T0)
  b().reset()
  b().on('strikes', strikesOp)
  OPEN_GUARD.ms = 0
})
afterEach(() => {
  vi.useRealTimers()
  vi.unstubAllGlobals()
})

/** Open, give the first price, and let the second ask land. */
async function opened(price = 25910, props: Props = {}) {
  const out = mount(props)
  await settle()
  tick(price)
  await settle(ASK_GAP_MS)
  return out
}

describe('the picker opening', () => {
  it('shows the server’s tabs, and the ladder of 21 strikes with the word ATM on one', async () => {
    await opened(25910)
    const tabs = within(picker()).getAllByRole('button', { pressed: true })
    expect(tabs.map((tab) => tab.textContent)).toEqual(['Nearest 24 Nov'])
    for (const label of ['Nearest 24 Nov', '1 Dec', '8 Dec', '29 Dec (month)'])
      expect(within(picker()).getByRole('button', { name: label })).toBeInTheDocument()
    expect(picker().querySelectorAll('[data-strike]')).toHaveLength(21)
    expect(
      Array.from(picker().querySelectorAll('[data-strike]')).map((r) =>
        r.getAttribute('data-strike')
      )[0]
    ).toBe('25400')
    const atm = picker().querySelectorAll('[data-atm]')
    expect(atm).toHaveLength(1)
    expect(atm[0].getAttribute('data-strike')).toBe('25900')
    expect(within(atm[0] as HTMLElement).getByText('ATM')).toBeInTheDocument()
    expect(within(strikeRow(25950)).queryByText('ATM')).toBeNull()
  })

  it('asks first without a price, then with the underlying’s own tick, and says the server’s words meanwhile', async () => {
    mount()
    await settle()
    expect(asked()).toHaveLength(1)
    expect(asked()[0].params).toMatchObject({ u: 'NIFTY', x: 'NSE_INDEX', n: '10' })
    expect(asked()[0].params.p).toBeUndefined()
    expect(picker().querySelector('[data-atm]')).toBeNull()
    expect(screen.getByText('NIFTY’s price is not known yet.')).toBeInTheDocument()
    tick(25910)
    await settle(ASK_GAP_MS)
    expect(asked()).toHaveLength(2)
    expect(asked()[1].params.p).toBe('25910')
    expect(picker().querySelector('[data-atm]')?.getAttribute('data-strike')).toBe('25900')
  })

  it('shows the underlying’s price with its feed line: Real-time only while ticks arrive', async () => {
    mount()
    await settle()
    tick(25910.5)
    expect(within(picker()).getByText('25,910.50')).toBeInTheDocument()
    expect(picker().querySelector('[data-feed="live"]')?.textContent).toBe('Real-time')
    act(() => feed.setState({ isConnected: false, isAuthenticated: false }))
    await settle()
    expect(picker().querySelector('[data-feed="live"]')).toBeNull()
  })

  it('ticks the prices of the strikes shown', async () => {
    await opened(25910)
    act(() => {
      feed.push(sym('24NOV', 25900, 'CE'), 'NFO', { ltp: 210.35 })
      feed.push(sym('24NOV', 25900, 'PE'), 'NFO', { ltp: 140.1 })
      frames.flush()
    })
    expect(within(strikeRow(25900)).getByText('210.35')).toBeInTheDocument()
    expect(within(strikeRow(25900)).getByText('140.10')).toBeInTheDocument()
    act(() => {
      feed.push(sym('24NOV', 25900, 'CE'), 'NFO', { ltp: 211.2 })
      frames.flush()
    })
    expect(within(strikeRow(25900)).getByText('211.20')).toBeInTheDocument()
  })
})

describe('when it asks again', () => {
  it('asks only when the price crosses the midpoint to a neighbour: ten ticks inside ask nothing', async () => {
    await opened(25910)
    const before = asked().length
    for (const price of [25901, 25912, 25920, 25924, 25899, 25880, 25876, 25905, 25915, 25925])
      tick(price) // 25,925 is the midpoint, which belongs to the lower strike
    await settle(ASK_GAP_MS * 3)
    expect(asked()).toHaveLength(before)
    tick(25926)
    await settle(ASK_GAP_MS)
    expect(asked()).toHaveLength(before + 1)
    expect(asked().at(-1)?.params.p).toBe('25926')
    expect(picker().querySelector('[data-atm]')?.getAttribute('data-strike')).toBe('25950')
  })

  it('keeps to one ask in two seconds: a move inside them waits for the rest of them, once', async () => {
    await opened(25910)
    await settle(ASK_GAP_MS)
    const before = asked().length
    tick(25930)
    await settle(0)
    expect(asked()).toHaveLength(before + 1) // the gap has passed since the last answer
    tick(25980) // another neighbour at once: turned away
    await settle(100)
    expect(asked()).toHaveLength(before + 1)
    await settle(ASK_GAP_MS)
    expect(asked()).toHaveLength(before + 2)
    expect(asked().at(-1)?.params.p).toBe('25980')
  })

  it('asks nothing in thirty minutes with a still price', async () => {
    await opened(25910)
    const before = asked().length
    await settle(30 * 60 * 1000)
    expect(asked()).toHaveLength(before)
    tick(25910)
    await settle(30 * 60 * 1000)
    expect(asked()).toHaveLength(before)
  })

  it('asks on a tab change, for that expiry, with the price', async () => {
    await opened(25910)
    fireEvent.click(within(picker()).getByRole('button', { name: '1 Dec' }))
    await settle()
    expect(asked().at(-1)?.params).toMatchObject({ exp: '2026-12-01', p: '25910', n: '10' })
    expect(within(picker()).getByRole('button', { name: '1 Dec' })).toHaveAttribute(
      'aria-pressed',
      'true'
    )
    expect(strikeRow(25900).textContent).toContain('ATM')
  })

  it('re-reads the ladder in the server’s words when the expiry ended while it was open', async () => {
    await opened(25910)
    b().on('strikes', (call) => {
      if (call.params.exp === '2026-12-01')
        return {
          status: 'error',
          code: 'invalid',
          field: 'exp',
          message: 'The 1 Dec expiry has ended. Showing the nearest expiry.',
        }
      return strikesOp(call)
    })
    fireEvent.click(within(picker()).getByRole('button', { name: '1 Dec' }))
    await settle()
    expect(
      screen.getByText('The 1 Dec expiry has ended. Showing the nearest expiry.')
    ).toBeInTheDocument()
    const last = asked().at(-1) as Call
    expect(last.params.exp).toBeUndefined()
    expect(last.params.p).toBe('25910')
    expect(within(picker()).getByRole('button', { name: 'Nearest 24 Nov' })).toHaveAttribute(
      'aria-pressed',
      'true'
    )
    expect(picker().querySelectorAll('[data-strike]')).toHaveLength(21)
  })
})

describe('what it follows', () => {
  it('follows the underlying and the CE and PE of every strike shown, and releases them all on close', async () => {
    const out = await opened(25910)
    expect(feed.live()).toContain('NSE_INDEX:NIFTY:LTP')
    expect(optionKeys()).toHaveLength(42)
    out.again({ picker: null })
    await settle()
    expect(feed.live()).toEqual([])
    out.unmount()
  })

  it('releases the strikes of a tab the moment it is left, and follows the new tab’s when they arrive', async () => {
    await opened(25910)
    expect(optionKeys().every((key) => key.includes('24NOV'))).toBe(true)
    let release: (() => void) | undefined
    b().on('strikes', (call) =>
      call.params.exp === '2026-12-01'
        ? new Promise((resolve) => {
            release = () => resolve(strikesOp(call))
          })
        : strikesOp(call)
    )
    fireEvent.click(within(picker()).getByRole('button', { name: '1 Dec' }))
    await settle()
    expect(optionKeys()).toEqual([])
    expect(feed.live()).toContain('NSE_INDEX:NIFTY:LTP')
    act(() => release?.())
    await settle()
    expect(optionKeys()).toHaveLength(42)
    expect(optionKeys().every((key) => key.includes('01DEC'))).toBe(true)
  })

  it('counts them first in the 250: the picker’s 43 stay in when the list would fill the cap', async () => {
    const items = Array.from({ length: 250 }, (_, index) => ({
      id: index + 1,
      symbol: `S${index}`,
      exchange: 'NSE',
      position: index,
    }))
    await opened(25910, { items })
    expect(optionKeys()).toHaveLength(42)
    expect(feed.live()).toContain('NSE_INDEX:NIFTY:LTP')
    expect(
      new Set(feed.live().map((key) => key.replace(/:(LTP|Quote)$/, ''))).size
    ).toBeLessThanOrEqual(250)
  })
})

describe('adding', () => {
  it('Add CE and Add PE add that contract and mark it Added', async () => {
    const onAdd = vi.fn(async () => {})
    await opened(25910, { picker: { onAdd } })
    const row = within(strikeRow(25900))
    fireEvent.click(row.getByRole('button', { name: 'Add CE at 25,900' }))
    await settle()
    expect(onAdd).toHaveBeenLastCalledWith(sym('24NOV', 25900, 'CE'), 'NFO')
    expect(row.getByRole('button', { name: 'CE at 25,900 is in the list' })).toBeDisabled()
    expect(row.getByText('Added')).toBeInTheDocument()
    fireEvent.click(row.getByRole('button', { name: 'Add PE at 25,900' }))
    await settle()
    expect(onAdd).toHaveBeenLastCalledWith(sym('24NOV', 25900, 'PE'), 'NFO')
    expect(row.getAllByText('Added')).toHaveLength(2)
    expect(onAdd).toHaveBeenCalledTimes(2)
  })

  it('marks what the list already holds as Added, and disables Add on a full list', async () => {
    const held = [{ id: 5, symbol: sym('24NOV', 25950, 'CE'), exchange: 'NFO', position: 0 }]
    await opened(25910, { items: held, picker: { full: true } })
    expect(within(strikeRow(25950)).getByText('Added')).toBeInTheDocument()
    expect(
      within(strikeRow(25900)).getByRole('button', { name: 'Add CE at 25,900' })
    ).toBeDisabled()
    expect(screen.getByText('Swing is full. Remove a symbol to add another.')).toBeInTheDocument()
  })

  it('says the server’s sentence when an add is refused, and leaves the strike addable', async () => {
    const onAdd = vi.fn(async () => {
      throw new Error('refused')
    })
    await opened(25910, { picker: { onAdd } })
    const row = within(strikeRow(25900))
    fireEvent.click(row.getByRole('button', { name: 'Add CE at 25,900' }))
    await settle()
    expect(row.getByRole('button', { name: 'Add CE at 25,900' })).toBeEnabled()
    expect(row.queryByText('Added')).toBeNull()
  })

  it('opens F12’s form from Add as one row, on that strike and the tab’s expiry rule', async () => {
    const onFollow = vi.fn<(form: FollowForm) => void>()
    await opened(25910, { picker: { onFollow } })
    fireEvent.click(
      within(strikeRow(25950)).getByRole('button', { name: 'Add as one row at 25,950' })
    )
    expect(onFollow).toHaveBeenLastCalledWith({
      mode: 'add',
      kind: 'strike_pair',
      underlying: 'NIFTY',
      exchange: 'NSE_INDEX',
      sectionId: null,
      fixed: { strike: 25950, expiry: 'nearest' },
    })
    fireEvent.click(within(picker()).getByRole('button', { name: '1 Dec' }))
    await settle()
    fireEvent.click(
      within(strikeRow(25900)).getByRole('button', { name: 'Add as one row at 25,900' })
    )
    expect(onFollow).toHaveBeenLastCalledWith(
      expect.objectContaining({ fixed: { strike: 25900, expiry: 'next' } })
    )
    fireEvent.click(within(picker()).getByRole('button', { name: '29 Dec (month)' }))
    await settle()
    fireEvent.click(
      within(strikeRow(25900)).getByRole('button', { name: 'Add as one row at 25,900' })
    )
    expect(onFollow).toHaveBeenLastCalledWith(
      expect.objectContaining({ fixed: { strike: 25900, expiry: 'next_month' } })
    )
  })

  it('does not offer Add as one row on a weekly no rule can follow, and says why', async () => {
    const onFollow = vi.fn()
    await opened(25910, { picker: { onFollow } })
    fireEvent.click(within(picker()).getByRole('button', { name: '8 Dec' }))
    await settle()
    expect(screen.getByText(/A row can follow the nearest/)).toBeInTheDocument()
    const one = within(strikeRow(25900)).getByRole('button', { name: 'Add as one row at 25,900' })
    expect(one).toBeDisabled()
    expect(onFollow).not.toHaveBeenCalled()
    expect(within(strikeRow(25900)).getByRole('button', { name: 'Add CE at 25,900' })).toBeEnabled()
  })

  it('opens F11’s ATM form from Add ATM row', async () => {
    const onFollow = vi.fn()
    await opened(25910, { picker: { onFollow } })
    fireEvent.click(within(picker()).getByRole('button', { name: 'Add ATM row' }))
    expect(onFollow).toHaveBeenCalledWith({
      mode: 'add',
      kind: 'atm_option',
      underlying: 'NIFTY',
      exchange: 'NSE_INDEX',
      sectionId: null,
    })
  })

  it('links Open option chain to the chain of the underlying', async () => {
    await opened(25910)
    expect(within(picker()).getByText('Open option chain').closest('a')?.getAttribute('href')).toBe(
      '/optionchain?exchange=NFO&underlying=NIFTY'
    )
  })
})

describe('touch and the switch', () => {
  it('gives every button and link at least 48 dp', async () => {
    await opened(25910)
    const controls = Array.from(picker().querySelectorAll<HTMLElement>('button, a')).concat(
      Array.from(document.querySelectorAll<HTMLElement>('[role="dialog"] > div button'))
    )
    expect(controls.length).toBeGreaterThan(60)
    for (const control of new Set(controls)) {
      if (control.closest('[data-strike-picker]') === null) continue
      expect(control.className, control.outerHTML.slice(0, 120)).toMatch(/min-h-12|min-h-14/)
    }
  })

  it('does nothing for a tap in the first 500 ms, then works', async () => {
    OPEN_GUARD.ms = 500
    const onAdd = vi.fn(async () => {})
    mount({ picker: { onAdd } })
    await settle(50)
    const add = within(strikeRow(25900)).getByRole('button', { name: 'Add CE at 25,900' })
    fireEvent.click(add)
    await settle()
    expect(onAdd).not.toHaveBeenCalled()
    await settle(500)
    fireEvent.click(add)
    await settle()
    expect(onAdd).toHaveBeenCalledTimes(1)
  })

  it('renders no picker with the switch off, and asks nothing', async () => {
    mount({ v2: false })
    await settle()
    expect(picker()).toBeNull()
    expect(asked()).toHaveLength(0)
    expect(feed.live()).toEqual([])
  })
})
