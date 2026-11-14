import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { chainOf, NOW, STOPS_AT } from './fixtures'
import { IvTab, NO_IV_RANK, smilePoints } from './IvTab'
import { type ChainModel, chainModel } from './model'
import { smileScale } from './Smile'
import { around } from './window'

function modelOf(now = NOW + 1000, edit?: (chain: ReturnType<typeof chainOf>) => void): ChainModel {
  const chain = chainOf()
  edit?.(chain)
  const strikes = chain.strikes.map((each) => each.strike)
  return chainModel({
    snapshot: chain,
    lines: new Map(),
    followed: new Set(around(strikes, 25_900, 20)),
    now,
  })
}

describe('the IV tab', () => {
  it('says ATM IV, the skew with its sign and the expected move, and that IV rank is not built', () => {
    const model = modelOf()
    render(<IvTab model={model} dim={false} />)
    const facts = document.querySelector('[data-iv-facts]') as HTMLElement
    expect(facts).toHaveTextContent(`ATM IV${model.atmIv?.toFixed(1)}%`)
    expect(facts.textContent).toMatch(/Skew[+−]?\d+\.\d/)
    const move = model.expectedMove
    expect(facts.textContent).toContain(`±${move?.premium.toFixed(2)} (${move?.pct?.toFixed(2)}%)`)
    expect(screen.getByText(NO_IV_RANK)).toBeInTheDocument()
  })

  it('draws the smile from the followed strikes only, the two lines told apart by dash and point shape', () => {
    const model = modelOf()
    render(<IvTab model={model} dim={false} />)
    const points = smilePoints(model)
    expect(points).toHaveLength(41)
    expect(points[0].strike).toBe(24_900)
    const smile = document.querySelector('[data-smile]') as SVGElement
    expect(smile).toHaveAttribute('aria-hidden', 'true')
    const call = smile.querySelector('[data-line="call"]') as SVGElement
    const put = smile.querySelector('[data-line="put"]') as SVGElement
    expect(call.getAttribute('points')?.split(' ')).toHaveLength(41)
    expect(call).not.toHaveAttribute('stroke-dasharray')
    expect(put).toHaveAttribute('stroke-dasharray')
    expect(smile.querySelectorAll('circle')).toHaveLength(41)
    expect(smile.querySelectorAll('rect')).toHaveLength(41)
    expect(smile.querySelector('[data-smile-atm]')).toHaveTextContent('ATM')
    expect(screen.getByText('Call IV')).toBeInTheDocument()
    expect(screen.getByText('Put IV')).toBeInTheDocument()
  })

  it('carries the same figures in a table for exact reading and for a screen reader', () => {
    const model = modelOf()
    render(<IvTab model={model} dim={false} />)
    const table = screen.getByRole('table', { name: 'Implied volatility by strike, in percent' })
    expect(table.querySelectorAll('[data-iv-strike]')).toHaveLength(41)
    const atm = table.querySelector('[data-iv-strike="25900"]') as HTMLElement
    const at = model.rows.find((row) => row.atm)
    expect(atm).toHaveTextContent(
      `25,900ATM${at?.ce?.greeks?.iv.toFixed(1)}${at?.pe?.greeks?.iv.toFixed(1)}`
    )
  })

  it('leaves out a leg whose IV cannot be worked out, with a dash in the table', () => {
    const model = modelOf(NOW + 1000, (chain) => {
      const at = chain.strikes.find((each) => each.strike === 26_000)
      if (at?.ce) Object.assign(at.ce, { ltp: 0, bid: 0, ask: 0 })
    })
    render(<IvTab model={model} dim={false} />)
    const smile = document.querySelector('[data-smile]') as SVGElement
    expect(smile.querySelectorAll('circle')).toHaveLength(40)
    expect(document.querySelector('[data-iv-strike="26000"]')).toHaveTextContent('not available')
  })

  it('says so when the contracts have stopped, and draws nothing', () => {
    render(<IvTab model={modelOf(STOPS_AT + 1000)} dim={false} />)
    expect(
      screen.getByText('These contracts have stopped trading, so there is no IV to work out.')
    ).toBeInTheDocument()
    expect(document.querySelector('[data-smile]')).toBeNull()
    expect(document.querySelector('[data-iv-facts]')).toHaveTextContent('ATM IV—not available')
  })

  it('scales the drawing to the figures, with room for a flat smile', () => {
    expect(smileScale([])).toBeNull()
    expect(smileScale([{ strike: 1, call: 10, put: null }])).toBeNull()
    const flat = smileScale([
      { strike: 100, call: 12, put: 12 },
      { strike: 110, call: 12, put: null },
    ])
    expect(flat).toMatchObject({ low: 12, high: 13 })
    expect(flat?.x(100)).toBe(30)
    expect(flat?.x(110)).toBe(320)
    expect(flat?.y(13)).toBe(12)
  })
})
