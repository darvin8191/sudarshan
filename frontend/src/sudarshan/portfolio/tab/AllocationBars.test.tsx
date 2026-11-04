/** Allocation: bars, not a pie; label and percent on every bar; sorted; a tap filters. */
import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import type { AllocationRow } from '../types'
import { AllocationBars } from './AllocationBars'

const ROWS: AllocationRow[] = [
  { key: 'bonds', label: 'Bonds', value_paise: 20_000, pct: '20.0000', holdings: 1 },
  { key: 'equity', label: 'Equity', value_paise: 70_000, pct: '70.0000', holdings: 3 },
  { key: 'gold', label: 'Gold and silver', value_paise: 10_000, pct: '10.0000', holdings: 1 },
]

function show(active = '') {
  const handlers = { onBy: vi.fn(), onPick: vi.fn() }
  render(<AllocationBars by="class" rows={ROWS} isActive={(r) => r.key === active} {...handlers} />)
  return handlers
}

describe('the allocation bars', () => {
  it('lists the slices largest first, each with its label and percent as text', () => {
    show()
    const bars = within(screen.getByRole('list', { name: 'Allocation by class' })).getAllByRole(
      'button'
    )
    expect(bars.map((b) => b.textContent)).toEqual([
      expect.stringContaining('Equity'),
      expect.stringContaining('Bonds'),
      expect.stringContaining('Gold and silver'),
    ])
    expect(bars[0].textContent).toContain('70.00%')
    expect(bars[0].textContent).toContain('3 holdings')
    expect(document.querySelector('svg circle, svg path[d*="A"]')).toBeNull() // no pie
  })

  it('a tap asks for that slice; a slice in force is told by pressed state and a mark', async () => {
    const { onPick } = show('gold')
    await userEvent.click(screen.getByRole('button', { name: /Bonds/ }))
    expect(onPick).toHaveBeenCalledWith(expect.objectContaining({ key: 'bonds' }))
    const gold = screen.getByRole('button', { name: /Gold and silver/ })
    expect(gold.getAttribute('aria-pressed')).toBe('true')
    expect(gold.className).toMatch(/border-2/)
  })

  it('changes what it is split by', async () => {
    const { onBy } = show()
    await userEvent.click(screen.getByRole('button', { name: 'Sector' }))
    expect(onBy).toHaveBeenCalledWith('sector')
    expect(screen.getByRole('button', { name: 'Class' }).getAttribute('aria-pressed')).toBe('true')
  })
})
