/**
 * v0.17.0 phone review M2 (M8.2): a refusal while the morning login replaces the contract list is
 * a failed read, never an expiry; and a contract taken as gone on its expiry day is asked again
 * until its session is over. Against hand-typed vectors.
 */
import { describe, expect, it } from 'vitest'
import vectors from './refusal.vectors.json'
import { reaskable, refusalVerdict } from './rows'

describe('what a refused quote read says (M2)', () => {
  it.each(vectors.refusals)('$name', (c) => {
    expect(refusalVerdict(c.refused, c.today)).toEqual({ failed: c.failed, gone: c.gone })
  })
})

describe('when a contract taken as gone is asked again (M2)', () => {
  it.each(vectors.reasks)('$name', (c) => {
    expect(reaskable(c.symbol, c.exchange, c.today, c.open, Date.parse(c.at))).toBe(c.reask)
  })
})
