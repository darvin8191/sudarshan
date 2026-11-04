/** The phone's freshness words and calendar sums against vectors/freshness.json (one copy). */
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { describe, expect, it } from 'vitest'
import { businessDaysBetween, clockOfInstant, dayOfInstant } from './days'
import { priceFreshness } from './freshness'

const FRESHNESS = JSON.parse(
  readFileSync(
    join(process.cwd(), '..', 'sudarshan', 'portfolio', 'tests', 'vectors', 'freshness.json'),
    'utf8'
  )
)

describe('freshness.json', () => {
  it.each(FRESHNESS.prices as any[])('$id: $title', ({ price, context, expect: want }) => {
    expect(
      priceFreshness(price, {
        today: context.today,
        lastSessionDay: context.last_session_day,
        oldDays: context.old_days,
        holidays: context.holidays,
        noun: context.noun,
      })
    ).toEqual(want)
  })

  it.each(FRESHNESS.business_days as any[])('business days $start to $end', (c) => {
    expect(businessDaysBetween(c.start, c.end, c.holidays)).toBe(c.expect)
  })

  it.each(FRESHNESS.instants as any[])('instant $at', (c) => {
    expect(clockOfInstant(c.at)).toBe(c.clock)
    expect(dayOfInstant(c.at)).toBe(c.day)
  })
})
