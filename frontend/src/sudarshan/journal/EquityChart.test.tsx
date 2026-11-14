/** The equity chart: the library is loaded when it shows; the curve and its drawdown are in words too. */
import { render, screen, waitFor } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'

const lib = vi.hoisted(() => {
  const made: Array<{
    options: Record<string, unknown>
    pane: number | undefined
    setData: ReturnType<typeof vi.fn>
  }> = []
  const chart = {
    addSeries: vi.fn((_kind: unknown, options: Record<string, unknown>, pane?: number) => {
      const s = { options, pane, setData: vi.fn() }
      made.push(s)
      return s
    }),
    panes: () => [{ setHeight: vi.fn() }, { setHeight: vi.fn() }],
    timeScale: () => ({ fitContent: vi.fn() }),
    remove: vi.fn(),
  }
  return { made, chart, createChart: vi.fn(() => chart) }
})
vi.mock('lightweight-charts', () => ({
  createChart: lib.createChart,
  AreaSeries: 'area',
  ColorType: { Solid: 'solid' },
}))

import { EquityChart } from './EquityChart'
import { analytics } from './fixtures'

describe('EquityChart', () => {
  it('draws the equity above and the drawdown below it in the loss colour, then goes with the widget', async () => {
    lib.made.length = 0
    const { unmount } = render(<EquityChart equity={analytics().equity} />)
    await waitFor(() => expect(lib.made.length).toBe(2))
    const [curve, under] = lib.made
    expect(curve.pane).toBeUndefined()
    expect(curve.setData.mock.calls[0][0]).toEqual([
      { time: '2026-11-10', value: 487.5 },
      { time: '2026-11-12', value: -512.5 },
      { time: '2026-11-13', value: 397.5 },
    ])
    expect(under.pane).toBe(1)
    expect(under.options.lineColor).toBe('#C3282E')
    expect(under.setData.mock.calls[0][0].map((p: { value: number }) => p.value)).toEqual([
      0, -1000, -90,
    ])
    unmount()
    expect(lib.chart.remove).toHaveBeenCalled()
  })

  it('swaps the loss red for its orange in colour-blind mode', async () => {
    lib.made.length = 0
    document.documentElement.dataset.cvd = '1'
    try {
      render(<EquityChart equity={analytics().equity} />)
      await waitFor(() => expect(lib.made.length).toBe(2))
      expect(lib.made[1].options.lineColor).toBe('#AC5700')
      // and the legend's swatch says what the chart draws (review L7)
      const swatch = screen.getByTestId('drawdown-label').querySelector('line')
      expect(swatch?.getAttribute('stroke')).toBe('#AC5700')
    } finally {
      delete document.documentElement.dataset.cvd
    }
  })

  it('labels the equity and the drawdown in words, with the deepest point', () => {
    render(<EquityChart equity={analytics().equity} />)
    expect(screen.getByRole('img').getAttribute('aria-label')).toBe(
      'Equity +₹397.50. Deepest drawdown −₹1,000.'
    )
    const label = screen.getByTestId('drawdown-label').textContent ?? ''
    expect(label).toContain('deepest −₹1,000')
    expect(label).toContain('−2.00%')
    expect(label).toContain('Thu 12 Nov 2026')
    expect(label).toContain('still in drawdown')
  })

  it('says a recovery, and no drawdown when there was none', () => {
    const equity = analytics().equity
    const { rerender } = render(
      <EquityChart
        equity={{
          ...equity,
          drawdown: {
            ...equity.drawdown,
            duration_state: 'recovered',
            duration_days: 4,
            recovered_day: 20261118,
          },
        }}
      />
    )
    expect(screen.getByTestId('drawdown-label').textContent).toContain(
      'recovered after 4 trading days'
    )
    rerender(
      <EquityChart
        equity={{
          ...equity,
          drawdown: {
            ...equity.drawdown,
            max_drawdown_paise: 0,
            max_drawdown_pct: null,
            duration_state: 'none',
          },
        }}
      />
    )
    expect(screen.getByTestId('drawdown-label').textContent).toContain('none')
  })

  it('says it starts with the first closed trade when there are no points', () => {
    render(<EquityChart equity={{ ...analytics().equity, points: [] }} />)
    expect(screen.getByText('The curve starts with your first closed trade.')).toBeTruthy()
  })
})
