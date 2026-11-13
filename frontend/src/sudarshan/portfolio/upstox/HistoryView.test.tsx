/**
 * The closing records' line names the time of the day in question, as the server gives it: the
 * record is due at the day's last close plus 15 minutes, 15:55 until 6 Dec 2026 and 16:00 from
 * 7 Dec, when F&O closes at 15:45. Nothing here works a time out on the phone.
 */
import { render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router'
import { describe, expect, it } from 'vitest'
import type { UpstoxEodDay, UpstoxHistory } from '../types'
import { HistoryView, takenAt } from './HistoryView'

/** Epoch ms of an IST wall-clock time. */
const ist = (date: string, hh: number, mm: number) => {
  const [y, m, d] = date.split('-').map(Number)
  return Date.UTC(y, m - 1, d, hh, mm) - 19_800_000
}

const day = (on: string, due: number | null | undefined): UpstoxEodDay => ({
  day: Number(on.replaceAll('-', '')),
  read_at: ist(on, 16, 1),
  holdings_value_paise: 1_000_000,
  holdings_cost_paise: 900_000,
  positions_pnl_paise: 0,
  not_read: [],
  provisional: false,
  late: false,
  ...(due === undefined ? {} : { due_at: due }),
})

const show = (history: UpstoxHistory) =>
  render(
    <MemoryRouter>
      <HistoryView history={history} />
    </MemoryRouter>
  )

describe('the closing records line', () => {
  it('names the newest record time: 15:55 before 7 Dec 2026, 16:00 from that day', () => {
    expect(takenAt(day('2026-12-04', ist('2026-12-04', 15, 55)))).toBe(
      'Closing records taken at 15:55, not real-time'
    )
    expect(takenAt(day('2026-12-10', ist('2026-12-10', 16, 0)))).toBe(
      'Closing records taken at 16:00, not real-time'
    )
  })

  it('names no time when the server gives none', () => {
    expect(takenAt(day('2026-12-10', null))).toBe(
      'Closing records taken after the close, not real-time'
    )
    expect(takenAt(day('2026-12-10', undefined))).toBe(
      'Closing records taken after the close, not real-time'
    )
  })

  it('shows the line of the newest day on the page', () => {
    show({
      days: [
        day('2026-12-04', ist('2026-12-04', 15, 55)),
        day('2026-12-10', ist('2026-12-10', 16, 0)),
      ],
      note: null,
    })
    expect(screen.getByText('Closing records taken at 16:00, not real-time')).toBeInTheDocument()
  })

  it("shows the server's sentence before the first record, else one with no time", () => {
    show({
      days: [],
      note: 'No closing record has been taken yet. The first is taken at 16:00.',
    })
    expect(
      screen.getByText('No closing record has been taken yet. The first is taken at 16:00.')
    ).toBeInTheDocument()
  })

  it('says only "after the close" when the server sends no sentence', () => {
    show({ days: [], note: null })
    expect(
      screen.getByText(
        'No closing records yet. The first one is taken after the close on a trading day.'
      )
    ).toBeInTheDocument()
  })
})
