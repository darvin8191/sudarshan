/**
 * The pieces every phone book is made of (M6.9), on their own: how a figure
 * reads when its ticks have stopped, which way its arrow points, how a
 * confirmation sends once, and the looks a trader relies on (a spinner that
 * still turns with reduced motion, a failure framed in the danger colour).
 */
import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { BookHeading, ProblemCard, RefreshIcon } from './parts'

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
