/**
 * The strike sheet's four optional props (M8.11), asked for by the options screen: a host that is
 * not a watchlist row can leave the row's buttons and the other expiries out, reach a leg's own
 * sheet, and lead with the side that was tapped. A caller giving none of them gets the sheet as it
 * was, which the watchlist's own tests (`strike.sheet.test.tsx`) pin.
 */
import { fireEvent, render, screen, within } from '@testing-library/react'
import { MemoryRouter } from 'react-router'
import { describe, expect, it, vi } from 'vitest'
import type { QuotesData } from '@/api/trading'
import { rowView } from './rows'
import { type Legs, StrikeSheet, type StrikeSheetProps } from './StrikeSheet'
import type { LiveRow } from './useLiveRows'
import type { Contract } from './v2/types'

const NOW = Date.UTC(2026, 10, 16, 5, 12, 5)
const contract = (side: 'CE' | 'PE'): Contract => ({
  exchange: 'NFO',
  symbol: `TEST17NOV2625900${side}`,
  key: `NFO:TEST17NOV2625900${side}`,
  name: `TEST 25900 ${side}`,
  lot: 65,
  tick: 0.05,
  expiry: '2026-11-17',
  isin: null,
})
const live = (of: Contract, ltp: number): LiveRow => ({
  item: { id: 0, symbol: of.symbol, exchange: of.exchange, position: 0 },
  key: of.key,
  view: rowView({ quote: { ltp, prev_close: ltp - 2 } as QuotesData, at: NOW }, undefined),
  direction: null,
  stale: false,
  tickAt: null,
})
const legs: Legs = {
  ce: { contract: contract('CE'), live: live(contract('CE'), 142.35) },
  pe: { contract: contract('PE'), live: live(contract('PE'), 88.1) },
}
const other = {
  expiry: '2026-11-24',
  ce: live({ ...contract('CE'), symbol: 'TEST24NOV2625900CE' }, 201),
  pe: live({ ...contract('PE'), symbol: 'TEST24NOV2625900PE' }, 150),
}

function show(props: Partial<StrikeSheetProps> = {}) {
  const base: StrikeSheetProps = {
    title: 'TEST 25,900',
    detail: 'NFO · 17 Nov',
    chip: 'ATM',
    help: null,
    words: null,
    moved: null,
    strike: 25_900,
    expiry: '2026-11-17',
    legs,
    lines: new Map(),
    others: { state: 'ready', rows: [other], retry: () => {} },
    chain: null,
    feed: { connected: true, fallback: false, epoch: 1 },
    now: NOW,
    marketOpen: true,
    onClose: () => {},
    onOrder: vi.fn(),
    onEdit: vi.fn(),
    onRemove: vi.fn(),
  }
  const all = { ...base, ...props }
  render(
    <MemoryRouter>
      <StrikeSheet {...all} />
    </MemoryRouter>
  )
  return all
}

const sheet = () => document.querySelector('[data-strike-sheet]') as HTMLElement
const heads = () =>
  [...sheet().querySelectorAll('thead th')].map((th) => th.textContent).filter(Boolean)
const orderButtons = () =>
  within(sheet())
    .getAllByRole('button', { name: /^(Buy|Sell) (CE|PE)$/ })
    .map((button) => button.textContent)
const legCells = () =>
  [...sheet().querySelectorAll('tbody [data-leg]')].map((td) => td.getAttribute('data-leg'))

describe('the strike sheet as every caller had it', () => {
  it('leads with the call, shows the other expiries and the row’s two buttons, and no leg button', () => {
    const props = show()
    expect(heads()).toEqual(['CE', 'PE'])
    expect(legCells()).toEqual(['CE', 'PE'])
    expect(orderButtons()).toEqual(['Buy CE', 'Sell CE', 'Buy PE', 'Sell PE'])
    expect(screen.getByText('Same strike, other expiries')).toBeInTheDocument()
    expect(
      [...sheet().querySelectorAll('[data-other-leg]')].map((el) =>
        el.getAttribute('data-other-leg')
      )
    ).toEqual(['CE', 'PE'])
    expect(sheet().querySelector('[data-leg-more]')).toBeNull()
    fireEvent.click(screen.getByRole('button', { name: 'Edit row' }))
    fireEvent.click(screen.getByRole('button', { name: 'Remove row' }))
    expect(props.onEdit).toHaveBeenCalledTimes(1)
    expect(props.onRemove).toHaveBeenCalledTimes(1)
  })
})

describe('the strike sheet for a host that is not a watchlist row', () => {
  it('draws neither Edit row nor Remove row when the host gives neither', () => {
    show({ onEdit: undefined, onRemove: undefined })
    expect(screen.queryByRole('button', { name: 'Edit row' })).toBeNull()
    expect(screen.queryByRole('button', { name: 'Remove row' })).toBeNull()
    expect(orderButtons()).toHaveLength(4)
  })

  it('has no "other expiries" section, and no sentence about it, without `others`', () => {
    show({ others: undefined })
    expect(screen.queryByText('Same strike, other expiries')).toBeNull()
    expect(sheet().textContent).not.toMatch(/other expiries/i)
    expect(screen.getByText(/^Straddle 230\.45$/)).toBeInTheDocument()
  })

  it('leads with the put when the put was tapped: columns, order buttons and other expiries', () => {
    show({ first: 'pe' })
    expect(heads()).toEqual(['PE', 'CE'])
    expect(legCells()).toEqual(['PE', 'CE'])
    expect(orderButtons()).toEqual(['Buy PE', 'Sell PE', 'Buy CE', 'Sell CE'])
    expect(
      [...sheet().querySelectorAll('[data-other-leg]')].map((el) =>
        el.getAttribute('data-other-leg')
      )
    ).toEqual(['PE', 'CE'])
    // The figures moved with their columns: the put's price stands under PE, first.
    const prices = sheet().querySelectorAll('tbody [data-leg]')
    expect(prices[0].textContent).toContain('88.10')
    expect(prices[1].textContent).toContain('142.35')
  })

  it('still sends each button’s own contract when the put leads', () => {
    const props = show({ first: 'pe' })
    fireEvent.click(screen.getByRole('button', { name: 'Sell PE' }))
    expect(vi.mocked(props.onOrder).mock.calls.at(-1)?.[0]).toMatchObject({
      action: 'SELL',
      item: { symbol: 'TEST17NOV2625900PE' },
    })
    fireEvent.click(screen.getByRole('button', { name: 'Buy CE' }))
    expect(vi.mocked(props.onOrder).mock.calls.at(-1)?.[0]).toMatchObject({
      action: 'BUY',
      item: { symbol: 'TEST17NOV2625900CE' },
    })
  })

  it('offers each leg’s own sheet when the host can open one, the leading side first', () => {
    const onLeg = vi.fn()
    show({ onLeg, first: 'pe' })
    const more = [...sheet().querySelectorAll<HTMLElement>('[data-leg-more]')]
    expect(more.map((el) => el.dataset.legMore)).toEqual(['PE', 'CE'])
    fireEvent.click(more[0])
    expect(onLeg).toHaveBeenLastCalledWith('pe', legs.pe.contract)
    fireEvent.click(screen.getByRole('button', { name: 'CE: chart, alert, depth' }))
    expect(onLeg).toHaveBeenLastCalledWith('ce', legs.ce.contract)
  })
})
