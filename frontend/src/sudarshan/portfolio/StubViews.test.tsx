/**
 * The money views of phase P7 beside the tabs (F0): offered only while the service says their
 * switch is on, each chip an icon and a word, the open one told by more than colour, and each
 * view's stub saying what it will show and that it is not ready yet.
 */
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import {
  PHASE_SWITCH,
  PHASE_VIEWS,
  PHASE_WORDS,
  PhaseChips,
  PhaseStub,
  phaseViewOf,
  phaseViewsOn,
} from './StubViews'
import { SWITCH_NAMES, WORDS } from './types'

describe('which money views are offered', () => {
  it('none while every switch is off or unnamed', () => {
    expect(phaseViewsOn({})).toEqual([])
    expect(phaseViewsOn({ tax: false, insights: false, goals: false })).toEqual([])
  })

  it('each switch brings its own views, in chip order', () => {
    expect(phaseViewsOn({ tax: true })).toEqual(['tax'])
    expect(phaseViewsOn({ insights: true })).toEqual(['risk', 'calendar'])
    expect(phaseViewsOn({ goals: true })).toEqual(['goals'])
    expect(phaseViewsOn({ tax: true, insights: true, goals: true })).toEqual([...PHASE_VIEWS])
  })

  it('every view waits for a switch the contract names', () => {
    for (const view of PHASE_VIEWS) expect(SWITCH_NAMES).toContain(PHASE_SWITCH[view])
  })

  it('the address opens a view only while it is offered', () => {
    const on = phaseViewsOn({ tax: true })
    expect(phaseViewOf(new URLSearchParams('view=tax'), on)).toBe('tax')
    expect(phaseViewOf(new URLSearchParams('view=goals'), on)).toBeNull()
    expect(phaseViewOf(new URLSearchParams('view=holdings'), on)).toBeNull()
    expect(phaseViewOf(new URLSearchParams(''), on)).toBeNull()
  })
})

describe('the chips', () => {
  it('draw nothing when no view is on', () => {
    const { container } = render(<PhaseChips views={[]} selected={null} onSelect={() => {}} />)
    expect(container.innerHTML).toBe('')
  })

  it('each has an icon and a word, a 48 dp target, and the open one is told by more than colour', async () => {
    const chosen = vi.fn()
    render(<PhaseChips views={['tax', 'risk']} selected="tax" onSelect={chosen} />)
    const tax = screen.getByRole('tab', { name: 'Tax' })
    const risk = screen.getByRole('tab', { name: 'Risk' })
    expect(tax.getAttribute('aria-selected')).toBe('true')
    expect(risk.getAttribute('aria-selected')).toBe('false')
    expect(tax.className).toContain('font-bold')
    expect(tax.className).toContain('border-2')
    for (const chip of [tax, risk]) {
      expect(chip.querySelector('svg')).not.toBeNull()
      expect(chip.className).toContain('min-h-12')
    }
    await userEvent.click(risk)
    expect(chosen).toHaveBeenCalledWith('risk')
  })
})

describe('the stubs', () => {
  it.each(PHASE_VIEWS)('%s says what it will show and that it is not ready yet', (view) => {
    render(<PhaseStub view={view} />)
    expect(screen.getByText(PHASE_WORDS[view])).toBeTruthy()
    expect(screen.getByText(WORDS.not_ready)).toBeTruthy()
    expect(screen.queryByText(/Real-time/)).toBeNull()
  })

  it('the tax stub names it a working paper for the CA', () => {
    render(<PhaseStub view="tax" />)
    expect(screen.getByRole('heading', { name: 'Tax' })).toBeTruthy()
    expect(screen.getByText(/verify with your CA/)).toBeTruthy()
  })
})
