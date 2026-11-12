import { render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { NOW, resultRow } from '@/test/screener'
import { dayStart } from './cardLine'
import { RunFigures } from './Results'

afterEach(() => vi.useRealTimers())

describe('RunFigures words a run by the page clock, never the machine clock', () => {
  it('says the bare time when the page is on the day of the run, whatever the machine says', () => {
    vi.useFakeTimers({ toFake: ['Date'] })
    vi.setSystemTime(NOW + 86_400_000 * 90)
    render(<RunFigures row={resultRow('S1R1')} at={NOW} now={NOW + 60_000} />)
    expect(screen.getByText(/at the run, 10:45/)).toBeTruthy()
  })

  it('names the weekday when the page is on a later day', () => {
    render(<RunFigures row={resultRow('S1R1')} at={NOW} now={NOW + 86_400_000} />)
    expect(screen.getByText(/at the run, Mon 10:45/)).toBeTruthy()
  })

  it('dayStart is the IST midnight of the moment', () => {
    expect(dayStart(NOW)).toBe(Date.UTC(2026, 10, 8, 18, 30, 0))
    expect(dayStart(NOW + 1000)).toBe(dayStart(NOW))
  })
})
