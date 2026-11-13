/**
 * SUDARSHAN: the admin timings page names the hours of the day in question. The server sends a
 * shipped NFO or BFO close with the change it follows by itself (15:45 from 7 Dec 2026) and today's
 * own hours; the page only shows them.
 */
import { describe, expect, it } from 'vitest'
import type { MarketTiming, TodayTiming } from '@/types/admin'
import { changeText, hoursOn } from './MarketTimings'

const NFO: MarketTiming = {
  id: 3,
  exchange: 'NFO',
  start_time: '09:15',
  end_time: '15:40',
  start_offset: 33_300_000,
  end_offset: 56_400_000,
  changes: [{ from: '2026-12-07', end_offset: 56_700_000, end_time: '15:45' }],
}

describe('the timings page', () => {
  it('words a change of the close with its first day', () => {
    expect(changeText({ from: '2026-12-07', end_time: '15:45' })).toBe('15:45 from 7 Dec 2026')
  })

  it("takes today's hours from the server when the exchange trades today", () => {
    const today: TodayTiming[] = [{ exchange: 'NFO', start_time: '09:15', end_time: '15:45' }]
    expect(hoursOn('NFO', '2026-12-10', today, [NFO])).toBe('09:15 - 15:45')
  })

  it('reads the row with its changes on a day the exchange does not trade', () => {
    expect(hoursOn('NFO', '2026-12-05', [], [NFO])).toBe('09:15 - 15:40') // Saturday before
    expect(hoursOn('NFO', '2026-12-12', [], [NFO])).toBe('09:15 - 15:45') // Saturday after
    expect(hoursOn('NFO', '', [], [NFO])).toBe('09:15 - 15:40') // no day known yet
    expect(hoursOn('MCX', '2026-12-12', [], [NFO])).toBeNull()
  })
})
