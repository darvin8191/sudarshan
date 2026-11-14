/** The heat map: every cell carries its net with its sign and its count as text; tint is never alone. */
import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { analytics } from './fixtures'
import { HeatMap, hoursOf, tintOf, weekdaysOf } from './HeatMap'

describe('the grid’s hours and days', () => {
  it('shows the market’s hours, widened to any hour with trades, and Monday to Friday', () => {
    expect(hoursOf([])).toEqual([9, 10, 11, 12, 13, 14, 15])
    expect(hoursOf([{ weekday: 0, hour: 18, trips: 1, net_paise: 1 }]).at(-1)).toBe(18)
    expect(hoursOf([{ weekday: 0, hour: 7, trips: 1, net_paise: 1 }])[0]).toBe(7)
    expect(weekdaysOf([])).toEqual([0, 1, 2, 3, 4])
    expect(weekdaysOf([{ weekday: 5, hour: 10, trips: 1, net_paise: 1 }])).toEqual([
      0, 1, 2, 3, 4, 5,
    ])
  })

  it('tints by sign and strength, and not at all for nothing', () => {
    expect(tintOf(0, 100)).toBe('')
    expect(tintOf(50, 0)).toBe('')
    expect(tintOf(100, 100)).toContain('var(--up) 34%')
    expect(tintOf(-50, 100)).toContain('var(--down) 22%')
    expect(tintOf(-10, 100)).toContain('var(--down) 12%')
  })
})

describe('HeatMap', () => {
  it('writes each cell’s net and count as text, with a true minus and a tint', () => {
    render(<HeatMap cells={analytics().heat} />)
    const cells = screen.getAllByTestId('heat-cell')
    const named = (label: string) =>
      cells.find((c) => c.getAttribute('aria-label')?.startsWith(label)) as HTMLElement
    const tue9 = named('Tue 9:00')
    expect(tue9.getAttribute('aria-label')).toBe('Tue 9:00: net +₹487.50, 2 trades')
    expect(tue9.textContent).toContain('+488')
    expect(tue9.style.background).toContain('--up')
    const thu10 = named('Thu 10:00')
    expect(thu10.textContent).toContain('−1k')
    expect(thu10.style.background).toContain('--down')
    // review M4: no arrow in a cell this small, and nothing runs into the next cell
    expect(thu10.querySelector('svg')).toBeNull()
    expect(thu10.className).toContain('overflow-hidden')
    expect(named('Mon 9:00').textContent).toBe('-')
    expect(named('Mon 9:00').getAttribute('aria-label')).toBe('Mon 9:00: no trades')
  })

  it('fits the market’s seven hours on a phone, and says to swipe when there are more', () => {
    const first = render(<HeatMap cells={analytics().heat} />)
    const table = screen.getByRole('table')
    expect(table.className).not.toContain('min-w-')
    expect(table.style.minWidth).toBe('16rem')
    expect(screen.queryByText(/Swipe the grid sideways/)).toBeNull()
    first.unmount()
    render(<HeatMap cells={[{ weekday: 0, hour: 18, trips: 1, net_paise: 100 }]} />)
    expect(screen.getByText('Swipe the grid sideways for the other hours.')).toBeTruthy()
  })

  it('says what it is for before there is a trade', () => {
    render(<HeatMap cells={[]} />)
    expect(screen.getByText(/once there are closed trades/)).toBeTruthy()
  })
})
