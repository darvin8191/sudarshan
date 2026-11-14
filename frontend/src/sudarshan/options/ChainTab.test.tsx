import { fireEvent, render, screen, within } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { rowSignature } from './ChainRow'
import { ChainTab } from './ChainTab'
import { chainOf, NOW } from './fixtures'
import { type ChainModel, chainModel } from './model'
import { around } from './window'

function modelOf(edit?: (chain: ReturnType<typeof chainOf>) => void): ChainModel {
  const chain = chainOf()
  edit?.(chain)
  const strikes = chain.strikes.map((each) => each.strike)
  return chainModel({
    snapshot: chain,
    lines: new Map(),
    followed: new Set(around(strikes, 25_900, 20)),
    now: NOW + 1000,
  })
}

function show(model = modelOf(), props: Partial<Parameters<typeof ChainTab>[0]> = {}) {
  const onOpen = vi.fn()
  const view = render(
    <ChainTab
      model={model}
      chainKey="NFO:NIFTY:2026-11-17"
      directions={new Map()}
      dim={false}
      onOpen={onOpen}
      {...props}
    />
  )
  return { ...view, onOpen }
}

const rows = () => [...document.querySelectorAll<HTMLElement>('[data-strike]')]
const row = (strike: number) => document.querySelector(`[data-strike="${strike}"]`) as HTMLElement

vi.setConfig({ testTimeout: 30_000 })

describe('the Chain tab', () => {
  it('draws ten strikes either side of the money, then the window, then the far strikes with their time', () => {
    show()
    expect(rows().map((each) => Number(each.dataset.strike))).toEqual(
      Array.from({ length: 21 }, (_, i) => 25_400 + i * 50)
    )
    fireEvent.click(screen.getByText('Show more strikes'))
    expect(rows()).toHaveLength(41)
    expect(document.querySelectorAll('[data-wing]')).toHaveLength(0)
    fireEvent.click(screen.getByText('Show 20 far strikes (not real-time)'))
    expect(rows()).toHaveLength(61)
    expect(document.querySelectorAll('[data-wing]')).toHaveLength(20)
    expect(screen.getByText('Far strikes are as of 10:42:05 · not real-time')).toBeInTheDocument()
    expect(within(row(24_500)).getByText('not real-time')).toBeInTheDocument()
    expect(screen.queryByText(/^Show /)).toBeNull()
  }, 20_000)

  it('marks the at-the-money row with the word and the side in the money with words, not colour alone', () => {
    show()
    expect(within(row(25_900)).getByText('ATM')).toBeInTheDocument()
    expect(row(25_900)).toHaveAttribute('data-atm', 'true')
    expect(rows().filter((each) => each.dataset.atm)).toHaveLength(1)
    const itmCall = within(row(25_800)).getByRole('button', { name: /25,800 call, in the money/ })
    expect(itmCall).toHaveAttribute('data-itm', 'true')
    expect(within(row(25_800)).getByRole('button', { name: /25,800 put/ })).not.toHaveAttribute(
      'data-itm'
    )
    expect(
      within(row(26_000)).getByRole('button', { name: /26,000 put, in the money/ })
    ).toBeInTheDocument()
  })

  it('shows the change with its sign and arrow in the Price view, and the IV in the middle', () => {
    show()
    const call = within(row(25_900)).getByRole('button', { name: /25,900 call/ })
    expect(call.textContent).toMatch(/\+4\.00 · \+\d+\.\d%/)
    expect(call.querySelector('[data-direction="up"]')).not.toBeNull()
    const put = within(row(25_900)).getByRole('button', { name: /25,900 put/ })
    expect(put.textContent).toMatch(/−3\.00 · −\d+\.\d%/)
    expect(put.querySelector('[data-direction="down"]')).not.toBeNull()
    expect(row(25_900).textContent).toMatch(/IV \d+\.\d/)
  })

  it('shows open interest in lakh, its change with a sign, the build-up words and the strike’s PCR in the OI view', () => {
    show()
    fireEvent.click(screen.getByRole('button', { name: 'OI' }))
    expect(screen.getByRole('button', { name: 'OI' })).toHaveAttribute('aria-pressed', 'true')
    const call = within(row(25_900)).getByRole('button', { name: /25,900 call/ })
    expect(call.textContent).toMatch(/open interest\d+(\.\d L|,\d{3})/)
    expect(call.textContent).toContain('+1,300')
    expect(call.textContent).toContain('Long build-up')
    expect(call.querySelector('[data-oi-bar]')).not.toBeNull()
    const put = within(row(25_900)).getByRole('button', { name: /25,900 put/ })
    expect(put.textContent).toContain('−975')
    expect(put.textContent).toContain('Long unwinding')
    expect(row(25_900).textContent).toMatch(/PCR \d\.\d\d/)
  })

  it('shows delta and theta in the Greeks view, and a dash where there is nothing to work out', () => {
    show(
      modelOf((chain) => {
        const at = chain.strikes.find((each) => each.strike === 25_950)
        if (at?.ce) Object.assign(at.ce, { ltp: 0, bid: 0, ask: 0 })
      })
    )
    fireEvent.click(screen.getByRole('button', { name: 'Greeks' }))
    const call = within(row(25_900)).getByRole('button', { name: /25,900 call/ })
    expect(call.textContent).toMatch(/delta 0\.\d\d · Θtheta −\d/)
    const empty = within(row(25_950)).getByRole('button', { name: /25,950 call/ })
    expect(within(empty).getAllByText('not available').length).toBeGreaterThanOrEqual(2)
  })

  it('says a side is not listed where the chain has only the other', () => {
    show(
      modelOf((chain) => {
        delete chain.strikes.find((each) => each.strike === 25_850)?.pe
      })
    )
    expect(within(row(25_850)).getByText('25,850 put is not listed')).toBeInTheDocument()
    expect(within(row(25_850)).getAllByRole('button')).toHaveLength(1)
  })

  it('opens the strike sheet on the side tapped', () => {
    const { onOpen } = show()
    fireEvent.click(within(row(25_950)).getByRole('button', { name: /25,950 put/ }))
    expect(onOpen).toHaveBeenCalledWith(25_950, 'pe')
    fireEvent.click(within(row(25_900)).getByRole('button', { name: /25,900 call/ }))
    expect(onOpen).toHaveBeenLastCalledWith(25_900, 'ce')
  })

  it('shows the direction of a leg’s last tick as an arrow beside its price, never a flash', () => {
    const model = modelOf()
    const key = model.rows.find((each) => each.strike === 25_900)?.ce?.key ?? ''
    show(model, { directions: new Map([[key, 'down']]) })
    const price = row(25_900).querySelector('[data-leg="CE"] [data-tick]') as HTMLElement
    expect(price).toHaveAttribute('data-tick', 'down')
    expect(price.querySelector('[data-direction="down"]')).not.toBeNull()
    expect(document.querySelector('[class*="animate-"]')).toBeNull()
  })

  it('greys every figure when the feed is not current: no up or down ink for an old move', () => {
    show(modelOf(), { dim: true })
    expect(document.querySelector('[class*="--up)"], [class*="--down)"]')).toBeNull()
  })

  it('does not move the list when the money walks: the rows drawn are the chain’s first', () => {
    const { rerender } = show()
    const walked = { ...modelOf(), atm: 26_300 }
    rerender(
      <ChainTab
        model={walked}
        chainKey="NFO:NIFTY:2026-11-17"
        directions={new Map()}
        dim={false}
        onOpen={() => {}}
      />
    )
    expect(Number(rows()[0].dataset.strike)).toBe(25_400)
    rerender(
      <ChainTab
        model={walked}
        chainKey="NFO:NIFTY:2026-11-24"
        directions={new Map()}
        dim={false}
        onOpen={() => {}}
      />
    )
    expect(Number(rows()[10].dataset.strike)).toBe(26_300)
  })

  it('gives a row one signature for what it draws: the same figures, the same text', () => {
    const model = modelOf()
    const at = model.rows[30]
    const extra = { ceTick: null, peTick: null, grey: false, maxOi: 10, itm: null }
    expect(rowSignature(at, 'price', extra)).toBe(
      rowSignature({ ...at, ce: at.ce && { ...at.ce } }, 'price', extra)
    )
    expect(rowSignature(at, 'price', extra)).not.toBe(
      rowSignature(
        { ...at, ce: at.ce && { ...at.ce, ltp: (at.ce.ltp ?? 0) + 0.05 } },
        'price',
        extra
      )
    )
    // Open interest moving does not redraw a row that is not showing it.
    const moved = { ...at, ce: at.ce && { ...at.ce, oi: 1 } }
    expect(rowSignature(at, 'price', extra)).toBe(rowSignature(moved, 'price', extra))
    expect(rowSignature(at, 'oi', extra)).not.toBe(rowSignature(moved, 'oi', extra))
  })
})
