import { act, render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import { DrawingSheet } from './DrawingSheet'
import type { DrawState, PhoneDrawings } from './phoneDrawings'

function fake(initial: Partial<DrawState> = {}) {
  let state: DrawState = {
    tool: null,
    count: 1,
    canUndo: true,
    failed: false,
    selected: null,
    locked: 0,
    ...initial,
  }
  const heard = new Set<() => void>()
  const calls = {
    setInk: vi.fn(),
    lockSelected: vi.fn(),
    removeSelected: vi.fn(),
    deselect: vi.fn(),
    unlockAll: vi.fn(),
  }
  return {
    calls,
    drawings: {
      state: () => state,
      subscribe: (listener: () => void) => {
        heard.add(listener)
        return () => heard.delete(listener)
      },
      ...calls,
    } as unknown as PhoneDrawings,
    set(next: Partial<DrawState>) {
      state = { ...state, ...next }
      for (const listener of heard) listener()
    },
  }
}

const LEVEL = { id: 'd1', tool: 'horizontal-line', label: 'Horizontal line', ink: 0 }

describe("a selected drawing's bar", () => {
  it('shows nothing until a drawing is selected', () => {
    const { drawings, set } = fake()
    render(<DrawingSheet drawings={drawings} onEditNote={() => {}} />)
    expect(screen.queryByTestId('drawing-bar')).toBeNull()
    act(() => set({ selected: LEVEL }))
    expect(screen.getByRole('region', { name: 'Horizontal line selected' })).toBeVisible()
    // A tool held for its taps: the bar gets out of their way.
    act(() => set({ tool: 'ray' }))
    expect(screen.queryByTestId('drawing-bar')).toBeNull()
  })

  it('offers three inks named with their line style, the one it has pressed', async () => {
    const { drawings, calls } = fake({ selected: LEVEL })
    render(<DrawingSheet drawings={drawings} onEditNote={() => {}} />)
    const inks = within(screen.getByRole('group', { name: 'Ink' })).getAllByRole('button')
    expect(
      inks.map((ink) => [ink.getAttribute('aria-label'), ink.getAttribute('aria-pressed')])
    ).toEqual([
      ['Ink, solid', 'true'],
      ['Peacock, dashed', 'false'],
      ['Amber, dotted', 'false'],
    ])
    // Each sample is stroked in its own dash, so the three differ without colour.
    expect(inks.map((ink) => ink.querySelector('line')?.getAttribute('stroke-dasharray'))).toEqual([
      null,
      '5 3',
      '1.5 3',
    ])
    await userEvent.click(inks[2])
    expect(calls.setInk).toHaveBeenCalledWith(2)
  })

  it('locks, deletes and lets go, each a 48 px target', async () => {
    const { drawings, calls } = fake({ selected: LEVEL })
    render(<DrawingSheet drawings={drawings} onEditNote={() => {}} />)
    for (const button of screen.getAllByRole('button'))
      expect(button.className).toContain('size-12')
    await userEvent.click(screen.getByRole('button', { name: 'Lock where it is' }))
    expect(calls.lockSelected).toHaveBeenCalledOnce()
    await userEvent.click(screen.getByRole('button', { name: 'Delete this horizontal line' }))
    expect(calls.removeSelected).toHaveBeenCalledOnce()
    await userEvent.click(screen.getByRole('button', { name: 'Done with this drawing' }))
    expect(calls.deselect).toHaveBeenCalledOnce()
    expect(screen.queryByRole('button', { name: "Edit the note's words" })).toBeNull()
  })

  it("opens a note's words for a text note", async () => {
    const edit = vi.fn()
    const { drawings } = fake({
      selected: { ...LEVEL, id: 'n1', tool: 'text', label: 'Text note' },
    })
    render(<DrawingSheet drawings={drawings} onEditNote={edit} />)
    await userEvent.click(screen.getByRole('button', { name: "Edit the note's words" }))
    expect(edit).toHaveBeenCalledWith('n1')
  })

  it('offers the way back for locked drawings, which no longer answer a tap', async () => {
    const { drawings, calls } = fake({ locked: 2 })
    render(<DrawingSheet drawings={drawings} onEditNote={() => {}} />)
    await userEvent.click(screen.getByRole('button', { name: 'Unlock 2 locked' }))
    expect(calls.unlockAll).toHaveBeenCalledOnce()
  })
})
