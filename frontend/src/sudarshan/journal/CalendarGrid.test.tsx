/** The calendar: every day with trades carries its net as text, with sign and arrow; months move. */
import { fireEvent, render, screen, within } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import {
  CalendarGrid,
  daysByNumber,
  isoWeekOf,
  monthCells,
  monthsOf,
  weeksOfMonth,
} from './CalendarGrid'
import { analytics } from './fixtures'

describe('the month grid', () => {
  it('starts on Monday and has the month’s days', () => {
    const cells = monthCells(202611) // 1 Nov 2026 is a Sunday: six blanks first
    expect(cells.slice(0, 6)).toEqual([null, null, null, null, null, null])
    expect(cells[6]).toBe(20261101)
    expect(cells.filter((c) => c !== null)).toHaveLength(30)
    expect(monthCells(202606)[0]).toBe(20260601) // a Monday
  })

  it('reads day and month keys by their digits', () => {
    const days = [
      { key: '2026-11-10', trips: 1, wins: 1, net_paise: 1 },
      { key: '20261213', trips: 1, wins: 1, net_paise: 1 },
      { key: 'junk', trips: 1, wins: 1, net_paise: 1 },
    ]
    expect([...daysByNumber(days).keys()]).toEqual([20261110, 20261213])
    expect(monthsOf(days)).toEqual([202611, 202612])
  })
})

describe('CalendarGrid', () => {
  it('shows each day’s net as text with its sign, a true minus and a tint, and its count', () => {
    render(<CalendarGrid calendar={analytics().calendar} />)
    const cell = (day: number) =>
      screen
        .getAllByTestId('calendar-cell')
        .find((c) => c.getAttribute('data-day') === String(day)) as HTMLElement
    expect(cell(20261110).textContent).toContain('+488')
    expect(cell(20261110).className).toContain('--up-tint')
    expect(cell(20261112).textContent).toContain('−1k')
    expect(cell(20261112).className).toContain('--down-tint')
    // review M4: no arrow in a cell this small, and nothing runs into the next cell
    expect(cell(20261112).querySelector('svg')).toBeNull()
    expect(cell(20261112).className).toContain('overflow-hidden')
    expect(cell(20261112).getAttribute('aria-label')).toBe('12 Nov: net −₹1,000.00, 1 trade, 0 won')
    expect(cell(20261111).getAttribute('aria-label')).toBe('11 Nov: no trades')
    expect(cell(20261111).textContent).toBe('11')
  })

  it('names the month, its net and its weeks', () => {
    render(<CalendarGrid calendar={analytics().calendar} />)
    expect(screen.getByRole('heading', { name: 'November 2026' })).toBeTruthy()
    expect(screen.getByText(/Month net/).textContent).toContain('+₹397.50')
    const weeks = screen.getByRole('list', { name: 'Weeks' })
    expect(within(weeks).getByText('Week 46')).toBeTruthy()
  })

  it('names a week "Week 45" and lists only the weeks of the month shown (review M8)', () => {
    const calendar = analytics().calendar
    calendar.weeks = [
      { key: '2026W45', trips: 20, wins: 12, net_paise: 9_800_000 },
      { key: '2026W46', trips: 22, wins: 13, net_paise: 2_545_678 },
      { key: '2026W40', trips: 2, wins: 1, net_paise: 1000 },
      { key: 'odd-key', trips: 1, wins: 1, net_paise: 1 },
    ]
    render(<CalendarGrid calendar={calendar} />)
    const rows = within(screen.getByRole('list', { name: 'Weeks' })).getAllByRole('listitem')
    expect(rows).toHaveLength(2)
    expect(rows[0].textContent).toContain('Week 45')
    expect(rows[1].textContent).toContain('Week 46')
    expect(screen.queryByText(/2026W|odd-key/)).toBeNull()
  })

  it('works out ISO weeks across a year’s turn', () => {
    expect(isoWeekOf(20261109)).toEqual({ year: 2026, week: 46 })
    expect(isoWeekOf(20261101)).toEqual({ year: 2026, week: 44 })
    expect(isoWeekOf(20261231)).toEqual({ year: 2026, week: 53 })
    expect(isoWeekOf(20270104)).toEqual({ year: 2027, week: 1 })
    const weeks = weeksOfMonth(
      [
        { key: '2026W53', trips: 1, wins: 1, net_paise: 1 },
        { key: '2027-W01', trips: 1, wins: 1, net_paise: 1 },
      ],
      202701
    )
    expect(weeks.map((w) => w.week)).toEqual([53, 1])
  })

  it('moves between the months that have trades', () => {
    const calendar = analytics().calendar
    calendar.days.push({ key: '20261002', trips: 1, wins: 1, net_paise: 5000 })
    calendar.months.push({ key: '2026-10', trips: 1, wins: 1, net_paise: 5000 })
    render(<CalendarGrid calendar={calendar} />)
    expect(
      (screen.getByRole('button', { name: 'Later month' }) as HTMLButtonElement).disabled
    ).toBe(true)
    fireEvent.click(screen.getByRole('button', { name: 'Earlier month' }))
    expect(screen.getByRole('heading', { name: 'October 2026' })).toBeTruthy()
    expect(
      (screen.getByRole('button', { name: 'Earlier month' }) as HTMLButtonElement).disabled
    ).toBe(true)
  })

  it('says what it is for before there is a day', () => {
    render(<CalendarGrid calendar={{ days: [], weeks: [], months: [] }} />)
    expect(screen.getByText(/Days with closed trades are shown here/)).toBeTruthy()
  })
})
