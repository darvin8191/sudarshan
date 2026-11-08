/** The return, the indices and the financial years (M7.30, M7.31, M7.38): sentences, never zeros. */
import { render, screen, within } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { report } from './fixtures'
import { IncomeCard, monthText, yieldText } from './IncomeCard'
import { INDEX_WAITING, Performance } from './Performance'

describe('Performance', () => {
  it('shows the XIRR, the TWR with its start date and each index with dividends not included', () => {
    render(<Performance report={report()} />)
    expect(screen.getByText('+13.20%')).toBeTruthy() // XIRR, signed
    expect(screen.getByText('TWR since 2 May 2025')).toBeTruthy()
    expect(screen.getByText('+18.50%')).toBeTruthy()
    expect(screen.getByText('+12.10% a year')).toBeTruthy()
    expect(screen.getByText('NIFTY 50')).toBeTruthy()
    expect(screen.getAllByText('Dividends not included').length).toBeGreaterThan(0)
    expect(screen.getByText('₹1,10,000.00')).toBeTruthy() // the same money in the index
  })

  it('says when XIRR appears and shows the absolute return meanwhile', () => {
    render(
      <Performance
        report={report({
          xirr: {
            state: 'later',
            pct: null,
            from_day: 20270501,
            words: 'XIRR appears after one year (from 1 May 2027).',
          },
        })}
      />
    )
    expect(screen.getByText('XIRR appears after one year (from 1 May 2027).')).toBeTruthy()
    expect(screen.getByText('Absolute return')).toBeTruthy()
    expect(screen.getAllByText('Yearly return (XIRR)')).toHaveLength(1) // the index's, not the tab's
  })

  it('says the index history is still being collected instead of showing zeros', () => {
    const none = report().benchmarks.map((b) => ({
      ...b,
      value_paise: null,
      xirr_pct: null,
      twr_pct: null,
    }))
    render(<Performance report={report({ benchmarks: none })} />)
    expect(screen.getByText(INDEX_WAITING)).toBeTruthy()
    expect(screen.queryByText('Your money in the index')).toBeNull()
  })

  it('says why the TWR has no figure', () => {
    render(
      <Performance
        report={report({
          twr: {
            start_day: null,
            pct: null,
            annualised_pct: null,
            words: 'TWR needs closing prices for at least two days.',
          },
        })}
      />
    )
    expect(screen.getByText('TWR needs closing prices for at least two days.')).toBeTruthy()
  })

  it('lists the financial years with realised gain signed, and income and fees', () => {
    render(<Performance report={report()} />)
    const table = screen.getByRole('table')
    const rows = within(table).getAllByRole('row')
    expect(within(rows[1]).getByText('FY 2025-26')).toBeTruthy()
    expect(within(rows[1]).getByText('+₹2,500.00')).toBeTruthy()
    expect(within(rows[2]).getByText('−₹500.00')).toBeTruthy()
  })
})

describe('Performance and prices', () => {
  it('says unpriced holdings are counted at cost, as the Summary does', () => {
    render(<Performance report={report()} unpriced={2} />)
    expect(screen.getByText('2 holdings have no price yet and are counted at cost.')).toBeTruthy()
  })
  it('says nothing when every holding has a price', () => {
    render(<Performance report={report()} />)
    expect(screen.queryByText(/counted at cost/)).toBeNull()
  })
})

describe('IncomeCard', () => {
  it('writes the amount on every month bar', () => {
    render(<IncomeCard report={report()} />)
    const list = screen.getByRole('list', { name: 'Income by month' })
    expect(within(list).getByText('Nov 2025')).toBeTruthy()
    expect(within(list).getAllByText('₹100.00').length).toBe(1)
    expect(monthText(202601)).toBe('Jan 2026')
  })
  it('says so when there was no income in twelve months', () => {
    const none = report({
      income: {
        this_fy_paise: 0,
        by_month: [{ month: 202511, paise: 0 }],
        last_12m_paise: 0,
        held_income_paise: 0,
        held_cost_paise: 100_000,
        yield_on_cost_pct: '0.0000',
        by_fy: [],
      },
    })
    render(<IncomeCard report={none} />)
    expect(screen.getByText('No dividend or interest in the last 12 months.')).toBeTruthy()
    expect(screen.getByText('0.00%')).toBeTruthy()
    expect(screen.queryByRole('list', { name: 'Income by financial year' })).toBeNull()
  })
  it('shows the yield on cost, the twelve months and each financial year', () => {
    render(<IncomeCard report={report()} />)
    expect(screen.getByText('Yield on cost')).toBeTruthy()
    expect(screen.getByText('1.25%')).toBeTruthy()
    expect(
      screen.getByText(
        'Income of the last 12 months on the lots you still hold, divided by their cost.'
      )
    ).toBeTruthy()
    expect(screen.getByText('Last 12 months').nextSibling?.textContent).toBe('₹100.00')
    expect(screen.getByText('FY 2025-26')).toBeTruthy()
    expect(screen.getByText('₹400.00')).toBeTruthy()
  })
  it('says none, never a zero, when nothing is held at a cost', () => {
    const sold = report({
      income: {
        ...report().income,
        held_income_paise: 0,
        held_cost_paise: 0,
        yield_on_cost_pct: null,
      },
    })
    render(<IncomeCard report={sold} />)
    expect(screen.getByText('none')).toBeTruthy()
    expect(screen.getByText('Nothing is held at a cost.')).toBeTruthy()
    expect(yieldText(null)).toBe('none')
    expect(yieldText('12.3456')).toBe('12.35%')
  })
})
