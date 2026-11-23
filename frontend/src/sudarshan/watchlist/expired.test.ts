/** M8.2 TD1: when a contract in a list has expired, against hand-typed vectors. */
import { describe, expect, it } from 'vitest'
import vectors from './expired.vectors.json'
import { contractDay, expiredOn, expiredWords } from './rows'

describe('an expired contract (M8.2 TD1)', () => {
  it.each(vectors.cases)('$name', (c) => {
    expect(contractDay(c.symbol, c.exchange)).toBe(c.day)
    const day = expiredOn(c.symbol, c.exchange, c.today, c.gone)
    expect(day).toBe(c.expired)
    expect(day === null ? null : expiredWords(day)).toBe(c.words)
  })
})
