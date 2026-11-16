/**
 * The amount to add each month, worked out on the phone when a goal's value moves, against
 * sudarshan/portfolio/tests/vectors/goals.json (one copy, read from there; make_goals.py states
 * the sums and the Python core passes the same file).
 */
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { describe, expect, it } from 'vitest'
import { requiredMonthly } from './sums'

const FILE = JSON.parse(
  readFileSync(
    join(process.cwd(), '..', 'sudarshan', 'portfolio', 'tests', 'vectors', 'goals.json'),
    'utf8'
  )
) as {
  monthly: {
    id: string
    target_paise: number
    value_now_paise: number
    months: number
    rate_bp: number
    required_monthly_paise: number
  }[]
}

describe('goals.json on the phone', () => {
  it.each(FILE.monthly)('$id', (row) => {
    expect(requiredMonthly(row.target_paise, row.value_now_paise, row.months, row.rate_bp)).toBe(
      row.required_monthly_paise
    )
  })

  it('whole rupees, never below nothing, and never NaN or Infinity', () => {
    for (const target of [1, 1e9, 1e12, 1e15, 9e15, 1e300, Number.MAX_VALUE]) {
      for (const months of [-3, 0, 1, 7, 600, 100_000]) {
        for (const rate of [0, 1000, 5000]) {
          const got = requiredMonthly(target, 12_345, months, rate)
          if (got === null) continue // said as "cannot be worked out": the service's figure stays
          expect(Number.isSafeInteger(got) && got >= 0 && got % 100 === 0).toBe(true)
        }
      }
    }
    expect(requiredMonthly(Number.MAX_VALUE, 0, 1, 0)).toBeNull()
    expect(requiredMonthly(Number.NaN, 0, 12, 1000)).toBeNull()
  })
})
