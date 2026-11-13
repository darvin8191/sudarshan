/**
 * F13: the search sheet of v2 (M8.18). The real sheet over the v2 mock bridge: the chips send `seg`,
 * the answer is drawn as sent, Recent lives in this browser only, and the ATM and FUT offers open
 * F11's form.
 */
import { QueryClientProvider } from '@tanstack/react-query'
import { act, fireEvent, render, screen, within } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const bridge = vi.hoisted(() => ({
  current: null as null | ReturnType<typeof import('./v2/mockBridge').createBridge>,
}))
vi.mock('@/api/client', async () => {
  const { createBridge } = await import('./v2/mockBridge')
  bridge.current = createBridge()
  return { webClient: bridge.current.client }
})
vi.mock('@/utils/toast', () => ({ showToast: { error: vi.fn() } }))

import { createQueryClient } from '@/app/providers'
import { OPEN_GUARD } from './openGuard'
import { RECENTS_KEY, RECENTS_MAX } from './recents'
import { SearchSheet } from './SearchSheet'
import type { Hit, Search } from './v2/types'

const b = () => bridge.current as NonNullable<typeof bridge.current>

const hit = (group: Hit['group'], symbol: string, over: Partial<Hit> = {}): Hit => ({
  group,
  title: symbol,
  detail: 'NSE · detail',
  added: false,
  contract: {
    exchange: group === 'options' ? 'NFO' : group === 'indices' ? 'NSE_INDEX' : 'NSE',
    symbol,
    key: `X:${symbol}`,
    name: symbol,
    lot: null,
    tick: null,
    expiry: null,
    isin: null,
  },
  ...over,
})
const answer = (over: Partial<Search> = {}): Search => ({
  understood: '',
  hits: [],
  underlying: null,
  suggestion: null,
  ...over,
})

const settle = async (ms = 300) => {
  await act(async () => {
    await vi.advanceTimersByTimeAsync(ms)
  })
}

function mount(props: Partial<React.ComponentProps<typeof SearchSheet>> = {}, held: string[] = []) {
  const onAdd = vi.fn(async () => {})
  const onFollow = vi.fn()
  const out = render(
    <QueryClientProvider client={createQueryClient()}>
      <SearchSheet
        open
        onOpenChange={() => {}}
        listId={7}
        listName="Swing"
        held={new Set(held)}
        full={false}
        onAdd={onAdd}
        onFollow={onFollow}
        {...props}
      />
    </QueryClientProvider>
  )
  return { ...out, onAdd, onFollow }
}
const type = async (text: string) => {
  fireEvent.change(screen.getByRole('searchbox', { name: 'Symbol, name or contract' }), {
    target: { value: text },
  })
  await settle()
  await settle(10)
}
const lastSearch = () =>
  b()
    .calls.filter((c) => c.op === 'search')
    .at(-1)

beforeEach(() => {
  vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout', 'Date'] })
  b().reset()
  OPEN_GUARD.ms = 0
  localStorage.clear()
  b().on('search', () => answer())
})
afterEach(() => {
  vi.useRealTimers()
  vi.restoreAllMocks()
})

describe('the field and the chips', () => {
  it('names the field and offers the six chips, each at least 48 dp', () => {
    mount()
    expect(screen.getByPlaceholderText('Symbol, name or contract')).toBeInTheDocument()
    const labels = ['All', 'Stocks', 'Futures', 'Options', 'Indices', 'Commodities']
    for (const label of labels) {
      expect(screen.getByRole('button', { name: label }).className).toContain('min-h-12')
    }
    expect(screen.getByRole('button', { name: 'All' })).toHaveAttribute('aria-pressed', 'true')
  })

  it('sends the chip as seg, with the list id, after the owner types', async () => {
    mount()
    await type('NIFTY')
    expect(lastSearch()?.params).toMatchObject({ q: 'NIFTY', seg: 'all', list: '7' })
    for (const [label, seg] of [
      ['Stocks', 'stocks'],
      ['Futures', 'futures'],
      ['Options', 'options'],
      ['Indices', 'indices'],
      ['Commodities', 'commodities'],
    ]) {
      fireEvent.click(screen.getByRole('button', { name: label }))
      await settle(0)
      expect(lastSearch()?.params).toMatchObject({ q: 'NIFTY', seg })
    }
  })

  it('waits for typing to pause: one search for a burst, none below two letters', async () => {
    mount()
    const field = screen.getByRole('searchbox', { name: 'Symbol, name or contract' })
    for (const value of ['R', 'RE', 'REL', 'RELI']) {
      fireEvent.change(field, { target: { value } })
      await settle(50)
    }
    expect(b().count('search')).toBe(0)
    await settle(300)
    expect(b().count('search')).toBe(1)
    expect(lastSearch()?.params.q).toBe('RELI')
  })
})

describe('the answer, as sent', () => {
  it('prints one heading for each run of a group, in the server order', async () => {
    b().on('search', () =>
      answer({
        hits: [
          hit('stocks', 'RELIANCE'),
          hit('stocks', 'RELIGARE'),
          hit('indices', 'NIFTY'),
          hit('options', 'NIFTY25900CE', {
            title: 'NIFTY 25,900 CE',
            detail: 'NFO · 24 Nov 2026 · lot 65',
          }),
          hit('commodities', 'GOLD'),
        ],
      })
    )
    mount()
    await type('REL')
    const headings = screen.getAllByRole('heading', { level: 3 }).map((h) => h.textContent)
    expect(headings).toEqual(['Stocks', 'Indices', 'Options', 'Commodities'])
    expect(screen.getByText('NIFTY 25,900 CE')).toBeInTheDocument()
    expect(screen.getByText('NFO · 24 Nov 2026 · lot 65')).toBeInTheDocument()
  })

  it('shows Added for a symbol the server marks and for one the list holds', async () => {
    b().on('search', () =>
      answer({
        hits: [hit('stocks', 'SBIN', { added: true }), hit('stocks', 'INFY'), hit('stocks', 'TCS')],
      })
    )
    mount({}, ['NSE:TCS'])
    await type('S')
    await type('SBI')
    expect(screen.getByRole('button', { name: /^SBIN, .*in the list$/ })).toBeDisabled()
    expect(screen.getByRole('button', { name: /^TCS, .*in the list$/ })).toBeDisabled()
    expect(screen.getByRole('button', { name: /^Add INFY/ })).toBeEnabled()
    expect(screen.getAllByText('Added')).toHaveLength(2)
  })

  it('shows the understood line and the near miss as sent', async () => {
    b().on('search', () =>
      answer({
        understood: 'NIFTY · next expiry 1 Dec · strike 26,000 · PE',
        suggestion: 'No exact match for RELAINCE. Did you mean RELIANCE?',
      })
    )
    mount()
    await type('RELAINCE')
    expect(screen.getByText('NIFTY · next expiry 1 Dec · strike 26,000 · PE')).toBeInTheDocument()
    expect(
      screen.getByText('No exact match for RELAINCE. Did you mean RELIANCE?')
    ).toBeInTheDocument()
    expect(screen.queryByText(/No symbol matches/)).toBeNull()
  })

  it('gives every control at least 48 dp, and the rows 56', async () => {
    b().on('search', () =>
      answer({ hits: [hit('indices', 'NIFTY'), hit('stocks', 'SBIN')], underlying: 'NIFTY' })
    )
    mount({}, [])
    await type('NIFTY')
    for (const name of ['Pick a strike', 'Add ATM row', 'Add current-month FUT row']) {
      expect(screen.getByRole('button', { name }).className).toContain('min-h-12')
    }
    expect(screen.getByRole('button', { name: /^Add SBIN/ }).className).toContain('min-h-14')
  })
})

describe('Add and Recent', () => {
  it('adds the contract the server named and records it as a recent', async () => {
    b().on('search', () =>
      answer({
        hits: [
          hit('options', 'NIFTY24NOV2625900CE', {
            title: 'NIFTY 25,900 CE',
            detail: 'NFO · lot 65',
          }),
        ],
      })
    )
    const { onAdd } = mount()
    await type('NIFTY 25900 CE')
    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: /^Add NIFTY 25,900 CE/ }))
    })
    expect(onAdd).toHaveBeenCalledWith('NIFTY24NOV2625900CE', 'NFO')
    const stored = JSON.parse(localStorage.getItem(RECENTS_KEY) ?? '[]')
    expect(stored[0]).toMatchObject({ symbol: 'NIFTY24NOV2625900CE', title: 'NIFTY 25,900 CE' })
  })

  it('does not record a recent when the add is refused', async () => {
    b().on('search', () => answer({ hits: [hit('stocks', 'SBIN')] }))
    const { onAdd } = mount({ onAdd: vi.fn().mockRejectedValue(new Error('no')) })
    expect(onAdd).not.toHaveBeenCalled()
    await type('SBIN')
    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: /^Add SBIN/ }))
    })
    expect(localStorage.getItem(RECENTS_KEY)).toBeNull()
  })

  it('shows the last 20 recents on an empty field, newest first, and Clear recents empties them', async () => {
    const many = Array.from({ length: 25 }, (_, i) => ({
      symbol: `S${i}`,
      exchange: 'NSE',
      title: `S${i}`,
      detail: 'NSE',
    }))
    localStorage.setItem(RECENTS_KEY, JSON.stringify(many))
    mount()
    const recent = screen.getByRole('region', { name: 'Recent' })
    expect(within(recent).getAllByRole('button', { name: /^Add S/ })).toHaveLength(RECENTS_MAX)
    expect(within(recent).getAllByRole('button', { name: /^Add S/ })[0]).toHaveAccessibleName(
      /^Add S0/
    )
    const clear = screen.getByRole('button', { name: 'Clear recents' })
    expect(clear.className).toContain('min-h-12')
    fireEvent.click(clear)
    expect(screen.queryByRole('region', { name: 'Recent' })).toBeNull()
    expect(localStorage.getItem(RECENTS_KEY)).toBeNull()
    expect(b().count('search')).toBe(0)
  })

  it('keeps a symbol once and caps what it keeps at 20', async () => {
    const seeded = Array.from({ length: 20 }, (_, i) => ({
      symbol: `S${i}`,
      exchange: 'NSE',
      title: `S${i}`,
      detail: '',
    }))
    localStorage.setItem(RECENTS_KEY, JSON.stringify(seeded))
    b().on('search', () => answer({ hits: [hit('stocks', 'NEW'), hit('stocks', 'S3')] }))
    mount()
    await type('NEW')
    for (const name of [/^Add NEW/, /^Add S3/]) {
      await act(async () => {
        fireEvent.click(screen.getByRole('button', { name }))
      })
    }
    const stored = JSON.parse(localStorage.getItem(RECENTS_KEY) ?? '[]')
    expect(stored).toHaveLength(20)
    expect(stored.map((r: { symbol: string }) => r.symbol).slice(0, 2)).toEqual(['S3', 'NEW'])
    expect(stored.filter((r: { symbol: string }) => r.symbol === 'S3')).toHaveLength(1)
  })

  it('works with no recents and no error when storage is refused', async () => {
    const refuse = () => {
      throw new DOMException('denied', 'SecurityError')
    }
    vi.spyOn(Storage.prototype, 'getItem').mockImplementation(refuse)
    vi.spyOn(Storage.prototype, 'setItem').mockImplementation(refuse)
    vi.spyOn(Storage.prototype, 'removeItem').mockImplementation(refuse)
    b().on('search', () => answer({ hits: [hit('stocks', 'SBIN')] }))
    const { onAdd } = mount()
    expect(screen.queryByRole('region', { name: 'Recent' })).toBeNull()
    expect(screen.getByText(/Type at least 2 letters/)).toBeInTheDocument()
    await type('SBIN')
    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: /^Add SBIN/ }))
    })
    expect(onAdd).toHaveBeenCalledWith('SBIN', 'NSE')
  })
})

describe('an underlying with options', () => {
  it('shows Pick a strike disabled while F14 is not wired', async () => {
    b().on('search', () => answer({ underlying: 'NIFTY', hits: [hit('indices', 'NIFTY')] }))
    mount()
    await type('NIFTY')
    expect(screen.getByRole('button', { name: 'Pick a strike' })).toBeDisabled()
  })

  it('opens F11’s form for the ATM row and the FUT row with the underlying and its exchange', async () => {
    b().on('search', () => answer({ underlying: 'NIFTY', hits: [hit('indices', 'NIFTY')] }))
    const { onFollow } = mount()
    await type('NIFTY')
    fireEvent.click(screen.getByRole('button', { name: 'Add ATM row' }))
    expect(onFollow).toHaveBeenLastCalledWith({
      mode: 'add',
      kind: 'atm_option',
      underlying: 'NIFTY',
      exchange: 'NSE_INDEX',
      sectionId: null,
    })
    fireEvent.click(screen.getByRole('button', { name: 'Add current-month FUT row' }))
    expect(onFollow).toHaveBeenLastCalledWith({
      mode: 'add',
      kind: 'month_future',
      underlying: 'NIFTY',
      exchange: 'NSE_INDEX',
      sectionId: null,
    })
  })

  it('reads the exchange from where the options trade when no cash hit came back', async () => {
    b().on('search', () =>
      answer({ underlying: 'RELIANCE', hits: [hit('options', 'RELIANCE24NOV262900CE')] })
    )
    const { onFollow } = mount()
    await type('RELIANCE 2900 CE')
    fireEvent.click(screen.getByRole('button', { name: 'Add ATM row' }))
    expect(onFollow).toHaveBeenCalledWith(
      expect.objectContaining({ underlying: 'RELIANCE', exchange: 'NSE' })
    )
  })

  it('opens F14’s picker with the underlying and its exchange once it is wired', async () => {
    b().on('search', () => answer({ underlying: 'NIFTY', hits: [hit('indices', 'NIFTY')] }))
    const onPickStrike = vi.fn()
    mount({ onPickStrike })
    await type('NIFTY')
    fireEvent.click(screen.getByRole('button', { name: 'Pick a strike' }))
    expect(onPickStrike).toHaveBeenCalledWith('NIFTY', 'NSE_INDEX')
  })
})

describe('the 500 ms guard', () => {
  it('ignores a tap on the sheet for half a second after it opens', async () => {
    OPEN_GUARD.ms = 500
    b().on('search', () => answer({ hits: [hit('stocks', 'SBIN')] }))
    const { onAdd } = mount()
    await type('SBIN')
    // The typing above took 300 ms of the window; a tap now is still inside it.
    fireEvent.click(screen.getByRole('button', { name: /^Add SBIN/ }))
    expect(onAdd).not.toHaveBeenCalled()
    await settle(300)
    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: /^Add SBIN/ }))
    })
    expect(onAdd).toHaveBeenCalledTimes(1)
  })
})
