/**
 * The page's twin of the service's words (sudarshan/signals/words.py): the freshness header worked
 * out again by the clock alone, ages, times and the language pick, held to the service's own golden
 * vectors (sudarshan/signals/tests/vectors/words.json, "the file the phone's own freshness code is
 * tested against"), plus the page's own lines.
 */
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { describe, expect, it } from 'vitest'
import type { Freshness, Lang, Why } from './types'
import {
  ageWords,
  dayWords,
  fill,
  freshLine,
  freshnessAt,
  istAt,
  istDayOf,
  levelRows,
  nextIstMidnight,
  shortAge,
  sinceWords,
  timeWords,
  whyIn,
} from './words'

interface Last {
  published_ms: number
  fresh_until_ms: number
  stopping: boolean
  clock_ok: boolean
}

const VECTORS = JSON.parse(
  readFileSync(join(process.cwd(), '..', 'sudarshan/signals/tests/vectors/words.json'), 'utf8')
) as {
  freshness: Array<{ name: string; last: Last | null; now_ms: number; expect: Freshness }>
  ages: Array<{ delta_ms: number; expect: string }>
  times: Array<{ ms: number; now_ms: number; seconds: boolean; expect: string }>
  why: Array<{ why: Partial<Why>; lang: Lang; expect: [string | null, Lang | null, string] }>
}

/** The freshness the service would have answered when the document was published. */
function answeredAtPublish(last: Last | null): Freshness {
  if (last === null) {
    return {
      status: 'never',
      words: '',
      published_ms: null,
      fresh_until_ms: null,
      colour: false,
      clock_ok: true,
      changes_at_ms: null,
    }
  }
  return {
    status: last.stopping ? 'stopped' : 'fresh',
    words: '',
    published_ms: last.published_ms,
    fresh_until_ms: last.fresh_until_ms,
    colour: true,
    clock_ok: last.clock_ok,
    changes_at_ms: null,
  }
}

const AT = 1794800712250 // Mon 16 Nov 2026 09:15:12.250 IST, the vectors' own moment

describe("the header, worked out again on the phone, says what the service's words say", () => {
  it('reads the vectors', () => {
    expect(VECTORS.freshness.length).toBeGreaterThan(10)
  })

  for (const vector of VECTORS.freshness) {
    it(vector.name, () => {
      const got = freshnessAt(answeredAtPublish(vector.last), vector.now_ms)
      expect(got.status).toBe(vector.expect.status)
      expect(got.words).toBe(vector.expect.words)
      expect(got.colour).toBe(vector.expect.colour)
      // The page works its words out again no later than the service's own next moment.
      if (vector.expect.changes_at_ms !== null) {
        expect(got.nextMs).not.toBeNull()
        expect(got.nextMs as number).toBeLessThanOrEqual(vector.expect.changes_at_ms)
      }
    })
  }

  it('carries an answer forward: fresh, then stale at fresh_until, grey at 30 minutes', () => {
    const answered = freshnessAt(
      answeredAtPublish({
        published_ms: AT,
        fresh_until_ms: AT + 360_000,
        stopping: false,
        clock_ok: true,
      }),
      AT
    )
    expect(answered.nextMs).toBe(AT + 60_000) // the fresh line's age moves at the minute
    const asked = {
      status: 'fresh' as const,
      words: 'TRINETRA · updated 09:15:12',
      published_ms: AT,
      fresh_until_ms: AT + 360_000,
      colour: true,
      clock_ok: true,
      changes_at_ms: AT + 360_000,
    }
    expect(freshnessAt(asked, AT + 359_999).status).toBe('fresh')
    expect(freshnessAt(asked, AT + 360_000).status).toBe('stale')
    expect(freshnessAt(asked, AT + 30 * 60_000 - 1).colour).toBe(true)
    expect(freshnessAt(asked, AT + 30 * 60_000).colour).toBe(false)
  })

  it('keeps the colour of a fresh reading past 30 minutes (an hourly heartbeat at night)', () => {
    const night = {
      status: 'fresh' as const,
      words: '',
      published_ms: AT,
      fresh_until_ms: AT + 3_660_000,
      colour: true,
      clock_ok: true,
      changes_at_ms: null,
    }
    const late = freshnessAt(night, AT + 45 * 60_000)
    expect(late.status).toBe('fresh')
    expect(late.colour).toBe(true)
  })

  it('keeps a status this release does not know as the service said it', () => {
    const newer = {
      status: 'resting' as unknown as Freshness['status'],
      words: 'A newer sentence.',
      published_ms: AT,
      fresh_until_ms: AT + 1,
      colour: false,
      clock_ok: true,
      changes_at_ms: null,
    }
    const got = freshnessAt(newer, AT + 10)
    expect(got.words).toBe('A newer sentence.')
    expect(got.colour).toBe(false)
    expect(got.age).toBeNull()
  })
})

describe('ages, times and the language, as the vectors say', () => {
  for (const vector of VECTORS.ages) {
    it(`age ${vector.delta_ms} ms is "${vector.expect}"`, () => {
      expect(ageWords(vector.delta_ms)).toBe(vector.expect)
    })
  }
  for (const vector of VECTORS.times) {
    it(`time ${vector.ms} at ${vector.now_ms} is "${vector.expect}"`, () => {
      expect(timeWords(vector.ms, vector.now_ms, vector.seconds)).toBe(vector.expect)
    })
  }
  for (const vector of VECTORS.why) {
    it(`why in ${vector.lang} from ${Object.keys(vector.why).join(', ') || 'nothing'}`, () => {
      const why = { en: null, hi: null, gu: null, ...vector.why } as Why
      const got = whyIn(why, vector.lang)
      expect([got.text, got.lang, got.note ?? '']).toEqual(vector.expect)
    })
  }
})

describe("the page's own lines", () => {
  const fresh = (status: 'fresh' | 'stale' | 'stopped' | 'never', now: number) =>
    freshnessAt(
      {
        status,
        words: '',
        published_ms: status === 'never' ? null : AT,
        fresh_until_ms: status === 'never' ? null : AT + 360_000,
        colour: status !== 'never',
        clock_ok: true,
        changes_at_ms: null,
      },
      now
    )

  it('always says the age of the last reading', () => {
    expect(freshLine(fresh('fresh', AT + 125_000))).toBe(
      'TRINETRA · updated 09:15:12 · 2 minutes old'
    )
    expect(freshLine(fresh('fresh', AT + 125_000)).includes('Real-time')).toBe(false)
    expect(freshLine(fresh('fresh', AT + 400_000))).toBe(
      'TRINETRA has not published since 09:15. This is its last reading, 6 minutes old.'
    )
    expect(freshLine(fresh('stopped', AT + 3 * 3_600_000))).toBe(
      'TRINETRA was stopped by its operators at 09:15. Its last reading is 3 hours old.'
    )
    expect(freshLine(fresh('never', AT))).toBe(
      'TRINETRA is not connected yet. Publishing has not been switched on in TRINETRA.'
    )
  })

  it('says a short age where the line has little room', () => {
    expect([0, 59_999, 60_000, 14 * 60_000, 120 * 60_000, 48 * 3_600_000].map(shortAge)).toEqual([
      '<1 min',
      '<1 min',
      '1 min',
      '14 min',
      '2 h',
      '2 d',
    ])
  })

  it('names when a reading began, and its sessions', () => {
    const now = AT + 86_400_000 // Tue 17 Nov
    expect(sinceWords(AT - 3 * 86_400_000, 3, now)).toBe('Since 13 Nov, 3 sessions')
    expect(sinceWords(now - 60_000, 1, now)).toBe('Since 09:14, 1 session')
    expect(sinceWords(null, 2, now)).toBe('2 sessions')
    expect(sinceWords(null, null, now)).toBe('')
  })

  it('writes levels in Indian grouping and leaves out the missing ones', () => {
    expect(levelRows({ trigger: 25900, invalid: 26120.5, target: null })).toEqual([
      ['Trigger', '25,900'],
      ['Invalid at', '26,120.50'],
    ])
    expect(levelRows(null)).toEqual([])
  })

  it("finds India's day, its midnight and a wall time on it", () => {
    const midnight = 1794853800000 // Tue 17 Nov 2026 00:00 IST
    expect(istDayOf(midnight - 1)).toBe(20261116)
    expect(istDayOf(midnight)).toBe(20261117)
    expect(nextIstMidnight(midnight - 1)).toBe(midnight)
    expect(nextIstMidnight(midnight)).toBe(midnight + 86_400_000)
    expect(istAt(midnight + 5 * 3_600_000, 9, 15)).toBe(midnight + 33_300_000)
    expect(dayWords(midnight)).toBe('17 Nov')
  })

  it('fills only the fields it is given', () => {
    expect(fill('{count} files, {other}', { count: 3 })).toBe('3 files, {other}')
  })
})
