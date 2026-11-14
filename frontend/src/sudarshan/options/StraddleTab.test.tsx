/** The Straddle tab (M11.4, O8) over a stand-in for the computed chart: what it loads and hands over. */
import { act, fireEvent, render, screen, within } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import type { SymbolData } from '@/lib/MarketDataManager'

const made = vi.hoisted(() => ({
  charts: [] as Array<{
    options: { callbacks: { onLoad(state: unknown): void }; apiKey: string }
    loads: Array<{
      legs: Array<{ symbol: string; exchange: string }>
      interval: string
      name: string
    }>
    ticks: Array<[number, number, number | null]>
    resyncs: number
    destroyed: boolean
  }>,
}))
vi.mock('@/sudarshan/chart/computedChart', () => ({
  ComputedChart: class {
    self: (typeof made.charts)[number]
    constructor(options: (typeof made.charts)[number]['options']) {
      this.self = { options, loads: [], ticks: [], resyncs: 0, destroyed: false }
      made.charts.push(this.self)
    }
    async loadComputed(
      spec: { legs: Array<{ symbol: string; exchange: string }> },
      interval: string,
      name: string
    ) {
      this.self.loads.push({ legs: [...spec.legs], interval, name })
    }
    tick(leg: number, price: number, time: number | null) {
      this.self.ticks.push([leg, price, time])
    }
    resync() {
      this.self.resyncs += 1
    }
    setAppMode() {}
    destroy() {
      this.self.destroyed = true
    }
  },
}))

import { chainOf, NOW, symbolOf } from './fixtures'
import { type ChainModel, chainModel } from './model'
import { NOT_TRADABLE, StraddleTab, type StraddleTabProps } from './StraddleTab'
import { around } from './window'

const CHAIN = 'NFO:NIFTY:2026-11-17'
const followedStrikes = () =>
  new Set(
    around(
      chainOf().strikes.map((each) => each.strike),
      25_900,
      20
    )
  )

function modelOf(spot = 25_912.35, lines: ReadonlyMap<string, SymbolData> = new Map()): ChainModel {
  return chainModel({
    snapshot: chainOf({ spot }),
    lines,
    followed: followedStrikes(),
    spot,
    now: NOW + 1000,
  })
}

const tickLine = (strike: number, side: 'CE' | 'PE', ltp: number, at: number): SymbolData =>
  ({
    symbol: symbolOf(strike, side),
    exchange: 'NFO',
    data: { ltp, last_trade_time: at },
    lastUpdate: at,
    updateSource: 'websocket',
  }) as SymbolData

async function show(props: Partial<StraddleTabProps> = {}) {
  const onFollow = vi.fn()
  const onTrade = vi.fn()
  const all: StraddleTabProps = {
    model: modelOf(),
    chainKey: CHAIN,
    underlying: 'NIFTY',
    startAt: null,
    followed: followedStrikes(),
    lines: new Map(),
    epoch: 1,
    apiKey: 'test-key',
    appMode: 'live',
    dim: false,
    onFollow,
    onTrade,
    ...props,
  }
  const view = render(<StraddleTab {...all} />)
  await act(async () => {
    await new Promise((resolve) => setTimeout(resolve, 0))
  })
  return {
    ...view,
    all,
    onFollow,
    onTrade,
    again: (next: Partial<StraddleTabProps>) => view.rerender(<StraddleTab {...all} {...next} />),
  }
}

const chart = () => made.charts.at(-1) as (typeof made.charts)[number]
const shownStrike = () =>
  Number(document.querySelector('[data-straddle-strike]')?.getAttribute('data-straddle-strike'))

beforeEach(() => {
  made.charts.length = 0
})

describe('the Straddle tab', () => {
  it('opens on the money: the two premiums added, the break-evens, and the move as a percent', async () => {
    await show()
    expect(shownStrike()).toBe(25_900)
    expect(screen.getByText('ATM straddle')).toBeInTheDocument()
    const row = modelOf().rows.find((each) => each.strike === 25_900)
    const premium = (row?.ce?.ltp ?? 0) + (row?.pe?.ltp ?? 0)
    const facts = document.querySelector('[data-straddle-facts]') as HTMLElement
    expect(within(facts).getByText(premium.toFixed(2))).toBeInTheDocument()
    expect(document.querySelector('[data-straddle-breakevens]')?.textContent).toBe(
      `${(25_900 - premium).toLocaleString('en-IN', { minimumFractionDigits: 2 })} and ${(25_900 + premium).toLocaleString('en-IN', { minimumFractionDigits: 2 })}`
    )
    expect(document.querySelector('[data-straddle-move]')?.textContent).toBe(
      `±${((premium / 25_912.35) * 100).toFixed(2)}%`
    )
    expect(screen.getByText('Expected move')).toBeInTheDocument()
  })

  it('tells the change with a sign and an arrow, not by colour alone', async () => {
    await show()
    const change = document.querySelector('[data-straddle-change]') as HTMLElement
    // The fixture's call is 4 above its close and its put 3 below: +1.00.
    expect(change.textContent).toMatch(/^\+1\.00 · \+0\.\d%$/)
    expect(change.querySelector('svg')).not.toBeNull()
  })

  it('loads the computed chart of the call and the put on 5m, and says it is not tradable', async () => {
    await show()
    expect(made.charts).toHaveLength(1)
    expect(chart().loads).toEqual([
      {
        legs: [
          { symbol: symbolOf(25_900, 'CE'), exchange: 'NFO' },
          { symbol: symbolOf(25_900, 'PE'), exchange: 'NFO' },
        ],
        interval: '5m',
        name: 'NIFTY 25,900 straddle',
      },
    ])
    expect(screen.getByText(NOT_TRADABLE)).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /^(Buy|Sell)/ })).toBeNull()
  })

  it('opens on the strike the address named', async () => {
    await show({ startAt: 26_100 })
    expect(shownStrike()).toBe(26_100)
    expect(chart().loads[0].name).toBe('NIFTY 26,100 straddle')
    expect(screen.getByText('Straddle')).toBeInTheDocument()
    expect(screen.getByText('Premium, of the spot')).toBeInTheDocument()
  })

  it('follows nothing new for a window strike, and exactly the two legs of a far one', async () => {
    const { onFollow } = await show()
    expect(onFollow.mock.calls.flat(2)).toEqual([])
    const far = await show({ startAt: 24_500 })
    expect(far.onFollow).toHaveBeenLastCalledWith([
      `NFO:${symbolOf(24_500, 'CE')}`,
      `NFO:${symbolOf(24_500, 'PE')}`,
    ])
    expect(screen.getByText(/a far strike, followed while shown/)).toBeInTheDocument()
    far.unmount()
    expect(far.onFollow).toHaveBeenLastCalledWith([])
  })

  it('steps a strike at a time, re-pointing the chart', async () => {
    await show()
    fireEvent.click(screen.getByRole('button', { name: 'Higher strike' }))
    expect(shownStrike()).toBe(25_950)
    expect(chart().loads.at(-1)?.legs[0].symbol).toBe(symbolOf(25_950, 'CE'))
    fireEvent.click(screen.getByRole('button', { name: 'Lower strike' }))
    fireEvent.click(screen.getByRole('button', { name: 'Lower strike' }))
    expect(shownStrike()).toBe(25_850)
    expect(chart().loads).toHaveLength(4)
    expect(made.charts).toHaveLength(1)
  })

  it('reads another interval for the same pair', async () => {
    await show()
    fireEvent.click(screen.getByRole('button', { name: '15m' }))
    expect(chart().loads.at(-1)).toMatchObject({ interval: '15m' })
    expect(screen.getByRole('button', { name: '15m' })).toHaveAttribute('aria-pressed', 'true')
  })

  it('hands each leg’s tick to the chart once, with the exchange’s time', async () => {
    const at = NOW + 5_000
    const ce = tickLine(25_900, 'CE', 150, at)
    const view = await show({ lines: new Map([[`NFO:${ce.symbol}`, ce]]) })
    expect(chart().ticks).toEqual([[0, 150, Math.floor(at / 1000)]])
    // The same cache line again (another frame): not another tick.
    view.again({ lines: new Map([[`NFO:${ce.symbol}`, ce]]), dim: true })
    expect(chart().ticks).toHaveLength(1)
    const pe = tickLine(25_900, 'PE', 80, at + 1000)
    view.again({
      lines: new Map([
        [`NFO:${ce.symbol}`, ce],
        [`NFO:${pe.symbol}`, pe],
      ]),
    })
    expect(chart().ticks.at(-1)).toEqual([1, 80, Math.floor((at + 1000) / 1000)])
    expect(chart().ticks).toHaveLength(2)
  })

  it('never hands over a quote read standing in for the stream', async () => {
    const polled = { ...tickLine(25_900, 'CE', 150, NOW), updateSource: 'rest' } as SymbolData
    await show({ lines: new Map([[`NFO:${polled.symbol}`, polled]]) })
    expect(chart().ticks).toEqual([])
  })

  it('stays on its strike when the money moves, unless Follow ATM is on, and then says so', async () => {
    const view = await show()
    const follow = screen.getByRole('switch', { name: /Follow ATM/ })
    expect(follow).toHaveAttribute('aria-checked', 'false')
    view.again({ model: modelOf(25_960) })
    expect(shownStrike()).toBe(25_900)
    expect(document.querySelector('[data-atm-moved]')).toBeNull()

    fireEvent.click(follow)
    expect(follow).toHaveAttribute('aria-checked', 'true')
    expect(shownStrike()).toBe(25_950)
    expect(document.querySelector('[data-atm-moved]')?.textContent).toMatch(
      /^ATM moved to 25,950 at \d\d:\d\d$/
    )
    expect(chart().loads.at(-1)?.legs[1].symbol).toBe(symbolOf(25_950, 'PE'))
    // Stepping by hand is a choice: the switch goes off and the note with it.
    fireEvent.click(screen.getByRole('button', { name: 'Higher strike' }))
    expect(follow).toHaveAttribute('aria-checked', 'false')
    expect(document.querySelector('[data-atm-moved]')).toBeNull()
  })

  it('asks the chart to read the gap when the socket signs in again', async () => {
    const view = await show()
    view.again({ epoch: 2 })
    expect(chart().resyncs).toBe(1)
    view.again({ epoch: 2, dim: true })
    expect(chart().resyncs).toBe(1)
  })

  it('"Trade the legs" opens the strike sheet, that side first: no order is built here', async () => {
    const { onTrade } = await show()
    fireEvent.click(screen.getByRole('button', { name: '25,900 put' }))
    expect(onTrade).toHaveBeenLastCalledWith(25_900, 'pe')
    fireEvent.click(screen.getByRole('button', { name: '25,900 call' }))
    expect(onTrade).toHaveBeenLastCalledWith(25_900, 'ce')
  })

  it('shows the chart’s own sentence when a load fails, with Try again', async () => {
    await show()
    act(() =>
      chart().options.callbacks.onLoad({
        kind: 'error',
        symbol: 'NIFTY 25,900 straddle',
        interval: '5m',
        message: 'No candles were returned for the put',
      })
    )
    expect(screen.getByRole('alert')).toHaveTextContent(
      'Could not load NIFTY 25,900 straddle on 5m'
    )
    fireEvent.click(screen.getByRole('button', { name: 'Try again' }))
    expect(chart().loads).toHaveLength(2)
  })

  it('destroys its chart on leaving', async () => {
    const view = await show()
    view.unmount()
    expect(chart().destroyed).toBe(true)
  })

  it('says so when no strike lists both sides', async () => {
    const chain = chainOf({ count: 3 })
    for (const each of chain.strikes) delete each.pe
    const model = chainModel({ snapshot: chain, lines: new Map(), followed: null, now: NOW })
    await show({ model })
    expect(document.querySelector('[data-straddle-none]')).not.toBeNull()
    expect(made.charts).toHaveLength(0)
  })
})
