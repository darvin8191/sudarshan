import { render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router'
import { describe, expect, it } from 'vitest'
import { SymbolChartLink } from './SymbolChartLink'

describe('SymbolChartLink', () => {
  it("opens the phone chart for the row's instrument, and says so", () => {
    render(
      <MemoryRouter>
        <SymbolChartLink exchange="NFO" symbol="NIFTY27OCT2625000CE" />
      </MemoryRouter>
    )
    const link = screen.getByRole('link', {
      name: 'Open the chart for NIFTY27OCT2625000CE on NFO',
    })
    expect(link).toHaveAttribute('href', '/chart/NFO/NIFTY27OCT2625000CE')
    expect(link).toHaveTextContent('NIFTY27OCT2625000CE')
  })

  it('wraps what it is given, such as a symbol cell with its own layout', () => {
    render(
      <MemoryRouter>
        <SymbolChartLink exchange="NSE" symbol="M&M">
          <span>M&amp;M · Mahindra</span>
        </SymbolChartLink>
      </MemoryRouter>
    )
    expect(screen.getByRole('link')).toHaveAttribute('href', '/chart/NSE/M%26M')
    expect(screen.getByText('M&M · Mahindra')).toBeInTheDocument()
  })
})
