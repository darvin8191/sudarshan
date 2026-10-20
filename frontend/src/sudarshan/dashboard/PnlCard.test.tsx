import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { type DashboardFunds, marginUse, PnlCard } from './PnlCard'

const MINUS = '−'

function funds(overrides: Partial<DashboardFunds> = {}): DashboardFunds {
  return {
    availablecash: '48320.50',
    collateral: '0.00',
    m2munrealized: '650.75',
    m2mrealized: '1800.00',
    utiliseddebits: '12400.00',
    ...overrides,
  }
}

describe('PnlCard', () => {
  it("adds realised and unrealised into today's P&L, signed, with an up arrow", () => {
    render(<PnlCard funds={funds()} loading={false} />)

    const total = screen.getByTestId('pnl-total')
    expect(total).toHaveTextContent('+₹2,450.75')
    expect(total).toHaveClass('text-[color:var(--up)]')
    expect(total.querySelector('svg')).toHaveAttribute('data-direction', 'up')
    expect(screen.getByText('+₹1,800.00')).toBeInTheDocument()
    expect(screen.getByText('+₹650.75')).toBeInTheDocument()
  })

  it('shows a loss with a true minus and a down arrow, never colour alone', () => {
    render(
      <PnlCard
        funds={funds({ m2mrealized: '-840.00', m2munrealized: '-2310.25' })}
        loading={false}
      />
    )

    const total = screen.getByTestId('pnl-total')
    expect(total).toHaveTextContent(`${MINUS}₹3,150.25`)
    expect(total).toHaveClass('text-[color:var(--down)]')
    expect(total.querySelector('svg')).toHaveAttribute('data-direction', 'down')
  })

  it('gives a flat day no sign and no arrow', () => {
    render(<PnlCard funds={funds({ m2mrealized: '0', m2munrealized: '0.00' })} loading={false} />)

    const total = screen.getByTestId('pnl-total')
    expect(total).toHaveTextContent('₹0.00')
    expect(total).toHaveClass('text-foreground')
    expect(total.querySelector('svg')).toBeNull()
  })

  it('shows the funds available and the margin in use against all the margin there is', () => {
    render(<PnlCard funds={funds()} loading={false} />)

    expect(screen.getByText('₹48,320.50')).toBeInTheDocument()
    expect(screen.getByText('Margin used ₹12,400 of ₹60,721')).toBeInTheDocument()
    expect(screen.getByText('20%')).toBeInTheDocument()
    const fill = screen.getByTestId('margin-bar').firstElementChild as HTMLElement
    expect(Number.parseFloat(fill.style.width)).toBeCloseTo((12400 / 60720.5) * 100, 4)
  })

  it('counts pledged collateral into the margin and lists it when there is some', () => {
    render(<PnlCard funds={funds({ collateral: '39279.50' })} loading={false} />)

    expect(screen.getByText('Collateral')).toBeInTheDocument()
    expect(screen.getByText('₹39,279.50')).toBeInTheDocument()
    expect(screen.getByText('Margin used ₹12,400 of ₹1,00,000')).toBeInTheDocument()
    expect(screen.getByText('12%')).toBeInTheDocument()
  })

  it('says there is no margin in use when the broker reports nothing at all', () => {
    render(
      <PnlCard
        funds={funds({
          availablecash: '0.00',
          utiliseddebits: '0.00',
          m2mrealized: '0',
          m2munrealized: '0',
        })}
        loading={false}
      />
    )

    expect(screen.getByText('No margin in use')).toBeInTheDocument()
    expect(screen.queryByTestId('margin-bar')).toBeNull()
  })

  it('shows placeholders, not zeros, while the funds load', () => {
    const { container } = render(<PnlCard funds={null} loading />)

    expect(screen.queryByTestId('pnl-total')).toBeNull()
    expect(screen.queryByText('₹0.00')).toBeNull()
    expect(container.querySelectorAll('[data-slot="skeleton"]').length).toBeGreaterThan(0)
    expect(container.firstElementChild).toHaveAttribute('aria-busy', 'true')
  })
})

describe('marginUse', () => {
  it('reads a shortfall as all of the margin in use', () => {
    expect(marginUse(funds({ availablecash: '-2000', utiliseddebits: '15000' }))?.percent).toBe(100)
  })

  it('has nothing to measure when every figure is zero', () => {
    expect(marginUse(funds({ availablecash: '0', utiliseddebits: '0' }))).toBeNull()
  })

  it('treats junk from the API as zero rather than as a number', () => {
    expect(marginUse(funds({ availablecash: 'n/a', utiliseddebits: '100' }))?.percent).toBe(100)
  })
})
