import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { clockTime, TimedRefresh, timedLabel } from './TimedRefresh'

describe('the timer switch of a wide-screen tool (T6)', () => {
  it('says what the timer is, and never calls it real-time', () => {
    expect(timedLabel(30_000)).toBe('Read again every 30 s (not real-time)')
    expect(timedLabel(60_000)).toBe('Read again every 1 min (not real-time)')
  })

  it('shows the time of the figures in India, to the second', () => {
    expect(clockTime(Date.UTC(2026, 10, 16, 5, 12, 5))).toBe('10:42:05')
    render(
      <TimedRefresh
        id="t"
        everyMs={30_000}
        on={false}
        onChange={() => {}}
        updatedAt={Date.UTC(2026, 10, 16, 5, 12, 5)}
      />
    )
    expect(screen.getByText('Updated 10:42:05')).toBeInTheDocument()
  })

  it('shows no time before the first read, and switches on a tap of the switch or its words', () => {
    const onChange = vi.fn()
    render(<TimedRefresh id="t" everyMs={30_000} on={false} onChange={onChange} updatedAt={null} />)
    expect(screen.queryByText(/Updated/)).toBeNull()
    const control = screen.getByRole('switch', { name: 'Read again every 30 s (not real-time)' })
    expect(control).toHaveAttribute('aria-checked', 'false')
    fireEvent.click(control)
    expect(onChange).toHaveBeenCalledWith(true)
  })

  it.each([
    ['IVSmile.tsx'],
    ['GEXDashboard.tsx'],
    ['OIRange.tsx'],
  ])('%s starts with its timer off and uses the labelled switch', (file) => {
    const source = readFileSync(resolve(__dirname, '../../pages', file), 'utf8')
    expect(source).toMatch(/const \[autoRefresh, setAutoRefresh\] = useState\(false\)/)
    expect(source).toContain('<TimedRefresh')
    expect(source).not.toMatch(/Auto: ON|Auto-refresh \(1m\)|Last updated:/)
  })
})
