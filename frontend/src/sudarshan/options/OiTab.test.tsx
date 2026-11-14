import { fireEvent, render, screen, within } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { chainOf, NOW } from './fixtures'
import { type ChainModel, chainModel } from './model'
import { OiTab, painWords } from './OiTab'
import { around } from './window'

function modelOf(edit?: (chain: ReturnType<typeof chainOf>) => void, side = 20): ChainModel {
  const chain = chainOf()
  edit?.(chain)
  const strikes = chain.strikes.map((each) => each.strike)
  return chainModel({
    snapshot: chain,
    lines: new Map(),
    followed: new Set(around(strikes, 25_900, side)),
    now: NOW + 1000,
  })
}

const row = (strike: number) =>
  document.querySelector(`[data-oi-strike="${strike}"]`) as HTMLElement
const bar = (strike: number, side: 'CE' | 'PE', which: 'wide' | 'thin') =>
  row(strike).querySelector(`[data-side="${side}"] [data-bar="${which}"]`) as HTMLElement

describe('the OI tab', () => {
  it('says the totals, PCR with the far strikes’ time, max pain against the spot and the two walls', () => {
    const model = modelOf()
    render(<OiTab model={model} dim={false} />)
    const facts = document.querySelector('[data-oi-facts]') as HTMLElement
    expect(facts).toHaveTextContent('Calls 26.5 L · Puts 28.9 L')
    expect(facts).toHaveTextContent(`PCR ${model.pcr?.toFixed(2)} · far strikes as of 10:42:05`)
    expect(facts).toHaveTextContent(painWords(model.maxPain ?? 0, model.spot))
    expect(facts.textContent).toMatch(/Call wall \d\d,\d{3} \(.+\) · Put wall \d\d,\d{3} \(.+\)/)
  })

  it('says the time of the open interest while no tick has carried any', () => {
    const { rerender } = render(<OiTab model={modelOf()} dim={false} />)
    expect(document.querySelector('[data-oi-as-of]')).toHaveTextContent(
      'Open interest as of 10:42:05 · prices move with ticks'
    )
    rerender(<OiTab model={{ ...modelOf(), oiFromSnapshot: false }} dim={false} />)
    expect(document.querySelector('[data-oi-as-of]')).toBeNull()
  })

  it('words max pain against the spot either way, and without a spot', () => {
    expect(painWords(25_900, 25_912.35)).toBe('Max pain 25,900 · spot is 12 above')
    expect(painWords(26_000, 25_912.35)).toBe('Max pain 26,000 · spot is 88 below')
    expect(painWords(25_900, 25_900)).toBe('Max pain 25,900 · spot is at it')
    expect(painWords(25_900, null)).toBe('Max pain 25,900')
  })

  it('draws one row per followed strike with each side’s open interest and its change, signed', () => {
    render(<OiTab model={modelOf()} dim={false} />)
    expect(document.querySelectorAll('[data-oi-strike]')).toHaveLength(41)
    const call = row(25_900).querySelector('[data-side="CE"]') as HTMLElement
    expect(call.textContent).toMatch(/call open interest \d+(\.\d L|,\d{3})change today \+1,300/)
    const put = row(25_900).querySelector('[data-side="PE"]') as HTMLElement
    expect(put.textContent).toMatch(/put open interest .+change today −975/)
  })

  it('hatches a fall as well as signing it, and leaves a rise solid', () => {
    render(<OiTab model={modelOf()} dim={false} />)
    expect(bar(25_900, 'PE', 'thin')).toHaveAttribute('data-fell', 'true')
    expect(bar(25_900, 'PE', 'thin').style.backgroundImage).toContain('repeating-linear-gradient')
    expect(bar(25_900, 'CE', 'thin')).not.toHaveAttribute('data-fell')
    expect(bar(25_900, 'CE', 'thin').style.backgroundImage).toBe('')
  })

  it('swaps which bar is the wide one, and sorts nothing', () => {
    const model = modelOf()
    render(<OiTab model={model} dim={false} />)
    const order = () =>
      [...document.querySelectorAll<HTMLElement>('[data-oi-strike]')].map(
        (each) => each.dataset.oiStrike
      )
    const before = order()
    const oiWide = bar(25_900, 'CE', 'wide').style.width
    const changeThin = bar(25_900, 'CE', 'thin').style.width
    fireEvent.click(screen.getByRole('button', { name: 'Change' }))
    expect(screen.getByRole('button', { name: 'Change' })).toHaveAttribute('aria-pressed', 'true')
    expect(order()).toEqual(before)
    expect(bar(25_900, 'CE', 'wide').style.width).toBe(changeThin)
    expect(bar(25_900, 'CE', 'thin').style.width).toBe(oiWide)
    expect(bar(25_900, 'PE', 'wide').style.backgroundImage).toContain('repeating-linear-gradient')
  })

  it('tags the at-the-money strike, max pain and the walls in words', () => {
    const model = modelOf()
    render(<OiTab model={model} dim={false} />)
    expect(within(row(model.atm ?? 0)).getByText('ATM')).toBeInTheDocument()
    expect(within(row(model.maxPain ?? 0)).getByText('Max pain')).toBeInTheDocument()
    expect(within(row(model.callWall?.strike ?? 0)).getByText('Call wall')).toBeInTheDocument()
    expect(within(row(model.putWall?.strike ?? 0)).getByText('Put wall')).toBeInTheDocument()
  })

  it('folds the far strikes into two rows with their totals and time, and lists them on a tap', () => {
    render(<OiTab model={modelOf()} dim={false} />)
    const lower = screen.getByRole('button', { name: /8 lower strikes/ })
    expect(lower).toHaveTextContent('as of 10:42:05')
    expect(lower).toHaveAttribute('aria-expanded', 'false')
    expect(screen.getByRole('button', { name: /12 higher strikes/ })).toBeInTheDocument()
    fireEvent.click(lower)
    expect(document.querySelectorAll('[data-oi-strike]')).toHaveLength(49)
    expect(row(24_500)).toBeInTheDocument()
    fireEvent.click(lower)
    expect(document.querySelectorAll('[data-oi-strike]')).toHaveLength(41)
  })

  it('has no folded rows when every strike is followed, and says so when there is no open interest', () => {
    render(
      <OiTab
        model={modelOf((chain) => {
          chain.strikes = chain.strikes.slice(25, 36)
          for (const each of chain.strikes) {
            delete each.ce?.oi
            delete each.pe?.oi
          }
        })}
        dim={false}
      />
    )
    expect(document.querySelector('[data-oi-wing]')).toBeNull()
    expect(screen.getByText('Max pain not worked out: no open interest yet.')).toBeInTheDocument()
    expect(document.querySelector('[data-oi-facts]')).not.toHaveTextContent('far strikes as of')
  })
})
