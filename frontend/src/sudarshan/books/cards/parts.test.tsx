/**
 * The pieces every phone book is made of (M6.9), on their own: how a figure
 * reads when its ticks have stopped, which way its arrow points, how a
 * confirmation sends once, and the looks a trader relies on (a spinner that
 * still turns with reduced motion, a failure framed in the danger colour).
 */
import { act, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import {
  BookHeading,
  Confirm,
  Last,
  Money,
  ProblemCard,
  RefreshIcon,
  SignedPercent,
  STALE,
} from './parts'

/** The direction colours a live figure takes (dashboard/Trend), by name. */
const UP = 'text-[color:var(--up)]'
const DOWN = 'text-[color:var(--down)]'

describe('a figure whose ticks have stopped', () => {
  it('Money turns grey, keeping its sign and its arrow', () => {
    const { container, rerender } = render(<Money value={-1234.5} />)
    const live = container.firstElementChild as HTMLElement
    expect(live).toHaveClass(DOWN)
    expect(live).not.toHaveClass(STALE)

    rerender(<Money value={-1234.5} stale />)
    const stale = container.firstElementChild as HTMLElement
    expect(stale).toHaveClass(STALE)
    expect(stale).not.toHaveClass(DOWN)
    expect(stale).toHaveTextContent('−₹1,234.50')
    expect(stale.querySelector(':scope > svg')).toHaveAttribute('data-direction', 'down')
  })

  it('a signed percentage turns grey, keeping its sign', () => {
    const { container, rerender } = render(<SignedPercent value={7.02} />)
    expect(container.firstElementChild).toHaveClass(UP)
    rerender(<SignedPercent value={7.02} stale />)
    expect(container.firstElementChild).toHaveClass(STALE)
    expect(container.firstElementChild).not.toHaveClass(UP)
    expect(container.firstElementChild).toHaveTextContent('+7.02%')
  })

  it('the last price turns grey with a clock, keeping its last tick’s arrow', () => {
    const { container, rerender } = render(
      <Last value={152.5} tick="up" stale={false} exchange="NFO" />
    )
    const price = () => container.querySelector('[data-tick]') as HTMLElement
    expect(price()).toHaveClass(UP)
    rerender(<Last value={152.5} tick="up" stale exchange="NFO" />)
    expect(price()).toHaveClass('text-muted-foreground')
    expect(price()).not.toHaveClass(UP)
    expect(price().querySelector('svg')).toHaveAttribute('data-direction', 'up')
    expect(container.querySelector(`svg.${CSS.escape(STALE)}`)).not.toBeNull()
  })
})

describe('the arrow beside a sum of money', () => {
  it('points its own way: down for a loss, up for a gain', () => {
    const { container, rerender } = render(<Money value={-650} />)
    const arrow = () => (container.firstElementChild as HTMLElement).querySelector(':scope > svg')
    expect(arrow()).toHaveAttribute('data-direction', 'down')
    expect(arrow()?.querySelector('path')).toHaveAttribute('d', 'M5 8.5 0.8 1.5h8.4z')
    rerender(<Money value={650} />)
    expect(arrow()).toHaveAttribute('data-direction', 'up')
    expect(arrow()?.querySelector('path')).toHaveAttribute('d', 'M5 1.5 9.2 8.5H0.8z')
  })
})

describe('a confirmation tapped twice in the same moment', () => {
  it('sends once', () => {
    const onConfirm = vi.fn()
    render(
      <Confirm
        open
        title="Exit NIFTY 25900 CE?"
        description="Sells 65 (MIS) at market price."
        confirmLabel="Exit"
        onConfirm={onConfirm}
        onClose={() => {}}
      />
    )
    const button = screen.getByRole('button', { name: 'Exit' })
    // Both taps land before React draws again, as two quick touches can.
    act(() => {
      button.click()
      button.click()
    })
    expect(onConfirm).toHaveBeenCalledTimes(1)
  })
})

describe('Refresh while a read is under way', () => {
  it('spins, reduced motion or not: the theme keeps spinners turning', () => {
    const icon = render(<RefreshIcon refreshing onRefresh={() => {}} />)
    const spinner = screen.getByRole('button', { name: 'Refreshing' }).querySelector('svg')
    expect(spinner).toHaveClass('animate-spin')
    expect(spinner?.getAttribute('class')).not.toContain('motion-reduce')
    icon.unmount()

    render(<BookHeading title="Positions" refreshing onRefresh={() => {}} />)
    const heading = screen.getByRole('button', { name: 'Refreshing' }).querySelector('svg')
    expect(heading).toHaveClass('animate-spin')
    expect(heading?.getAttribute('class')).not.toContain('motion-reduce')
  })
})

describe('a read that failed', () => {
  it('is framed in the danger colour, which colour-blind mode keeps red', () => {
    render(<ProblemCard message="Positions could not be read just now." onRetry={() => {}} />)
    const card = screen.getByRole('alert')
    expect(card).toHaveClass('border-destructive')
    expect(card.getAttribute('class')).not.toContain('--down')
  })
})
