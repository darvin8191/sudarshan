/** The calendar: every day with trades carries its net as text, with sign and arrow; months move. */
import { fireEvent, render, screen, within } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { CalendarGrid, daysByNumber, monthCells, monthsOf } from './CalendarGrid'
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
  it('shows each day’s net as text with its sign, a true minus and an arrow, and its count', () => {
    render(<CalendarGrid calendar={analytics().calendar} />)
    const cell = (day: number) =>
      screen
        .getAllByTestId('calendar-cell')
        .find((c) => c.getAttribute('data-day') === String(day)) as HTMLElement
    expect(cell(20261110).textContent).toContain('+488')
    expect(cell(20261110).querySelector('[data-direction="up"]')).toBeTruthy()
    expect(cell(20261112).textContent).toContain('−1k')
    expect(cell(20261112).querySelector('[data-direction="down"]')).toBeTruthy()
    expect(cell(20261112).getAttribute('aria-label')).toBe('12 Nov: net −₹1,000.00, 1 trade, 0 won')
    expect(cell(20261111).getAttribute('aria-label')).toBe('11 Nov: no trades')
    expect(cell(20261111).textContent).toBe('11')
  })

  it('names the month, its net and its weeks', () => {
    render(<CalendarGrid calendar={analytics().calendar} />)
    expect(screen.getByRole('heading', { name: 'November 2026' })).toBeTruthy()
    expect(screen.getByText(/Month net/).textContent).toContain('+₹397.50')
    const weeks = screen.getByRole('list', { name: 'Weeks' })
    expect(within(weeks).getByText('Week 2026-W46')).toBeTruthy()
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
