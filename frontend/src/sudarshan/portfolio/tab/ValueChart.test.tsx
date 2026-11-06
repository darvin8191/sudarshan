/** The value chart (M7.29): the library is loaded when the widget shows; no history is a sentence. */
import { render, screen, waitFor } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'

const lib = vi.hoisted(() => {
  const series = () => ({ setData: vi.fn(), update: vi.fn() })
  const made: ReturnType<typeof series>[] = []
  const chart = {
    addSeries: vi.fn(() => {
      const s = series()
      made.push(s)
      return s
    }),
    timeScale: () => ({ fitContent: vi.fn() }),
    remove: vi.fn(),
  }
  return { made, chart, createChart: vi.fn(() => chart) }
})
vi.mock('lightweight-charts', () => ({
  createChart: lib.createChart,
  LineSeries: 'line',
  ColorType: { Solid: 'solid' },
  LineStyle: { Dashed: 2 },
}))

import { report } from './fixtures'
import { ValueChart } from './ValueChart'

describe('ValueChart', () => {
  it('draws the value and the money put in, then moves today on a live value', async () => {
    lib.made.length = 0
    const { rerender, unmount } = render(
      <ValueChart report={report()} today={20261104} liveValuePaise={12_000_000} />
    )
    await waitFor(() => expect(lib.made.length).toBe(2))
    const [putIn, value] = lib.made
    // Two days of history and today from the live value.
    expect(value.setData.mock.calls[0][0]).toHaveLength(3)
    expect(screen.getByText(/Net money put in/)).toBeTruthy()
    rerender(<ValueChart report={report()} today={20261104} liveValuePaise={12_050_000} />)
    await waitFor(() => expect(value.update).toHaveBeenCalled())
    expect(value.update.mock.calls.at(-1)?.[0]).toEqual({ time: '2026-11-04', value: 120500 })
    expect(putIn.update).toHaveBeenCalled()
    unmount()
    expect(lib.chart.remove).toHaveBeenCalled()
  })

  it('gives the series start sentence instead of a chart when there is no history', () => {
    render(
      <ValueChart
        report={report({
          series: [],
          series_words: 'Daily history starts once every holding here has a closing price.',
        })}
        today={20261104}
        liveValuePaise={null}
      />
    )
    expect(
      screen.getByText('Daily history starts once every holding here has a closing price.')
    ).toBeTruthy()
    expect(screen.queryByRole('img')).toBeNull()
  })
})
