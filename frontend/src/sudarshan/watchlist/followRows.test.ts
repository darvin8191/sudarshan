import { describe, expect, it, vi } from 'vitest'
import { answer, contract, rule } from './followFixtures'
import {
  addBody,
  chipOf,
  detailOf,
  draftOf,
  editBody,
  FollowResolver,
  GAP_MS,
  leftBand,
  lineOf,
  previewOf,
  titleOf,
} from './followRows'
import type { Resolved, ResolveItem } from './v2/types'

function make(replies: Resolved[][]) {
  const sent: Array<{ rows: ResolveItem[]; connected: boolean }> = []
  let at = 1_000_000
  const resolver = new FollowResolver({
    send: async (rows, connected) => {
      sent.push({ rows, connected })
      return replies.shift() ?? []
    },
    now: () => at,
    problem: () => 'The strike could not be read just now.',
  })
  return {
    resolver,
    sent,
    advance: (ms: number) => {
      at += ms
      return at
    },
  }
}
const flush = () => new Promise((resolve) => setTimeout(resolve, 0))

describe('the words of a row', () => {
  it('names the chip, the title and the detail', () => {
    expect(chipOf(rule())).toBe('ATM')
    expect(chipOf(rule({ offset: 2 }))).toBe('ATM+2')
    expect(chipOf(rule({ offset: -1 }))).toBe('ATM-1')
    expect(chipOf(rule({ kind: 'month_future' }))).toBe('Current-month FUT')
    expect(titleOf(rule(), 25900)).toBe('NIFTY 25,900 CE')
    expect(titleOf(rule({ kind: 'month_future' }), null)).toBe('NIFTY FUT')
    expect(detailOf(rule(), contract(25900))).toBe('NFO · 24 Nov 2026 · follows NIFTY')
  })

  it('leaves the band open at both ends', () => {
    expect(leftBand(25900, { low: 25870, high: 25930 })).toBe(false)
    expect(leftBand(25930, { low: 25870, high: 25930 })).toBe(true)
    expect(leftBand(1, { low: null, high: 5 })).toBe(false)
  })
})

describe('the form', () => {
  it('builds the body of an ATM option row and of a future row', () => {
    const under = { underlying: 'NIFTY', underlying_exchange: 'NSE_INDEX' }
    expect(addBody({ ...draftOf('atm_option'), offset: 2, right: 'PE' }, under, 7)).toEqual({
      kind: 'atm_option',
      underlying: 'NIFTY',
      underlying_exchange: 'NSE_INDEX',
      expiry_rule: 'nearest',
      section_id: 7,
      strike_rule: 'atm',
      offset: 2,
      opt_right: 'PE',
    })
    expect(addBody(draftOf('month_future'), under, null)).toEqual({
      kind: 'month_future',
      underlying: 'NIFTY',
      underlying_exchange: 'NSE_INDEX',
      expiry_rule: 'month',
    })
    expect(editBody(draftOf('month_future'))).toEqual({ expiry_rule: 'month' })
  })

  it('previews the row in words', () => {
    expect(previewOf('NIFTY', { ...draftOf('atm_option'), offset: 1 })).toMatch(
      /^NIFTY ATM\+1 CE, nearest expiry\./
    )
    expect(previewOf('NIFTY', draftOf('month_future'))).toBe("NIFTY future, this month's expiry.")
  })
})

describe('when the resolver asks', () => {
  it('asks once for every new row, in one call, with no price and no held strike', async () => {
    const { resolver, sent } = make([[answer(25900), answer(52000, { id: 2 })]])
    resolver.sync([rule(), rule({ id: 2, underlying: 'BANKNIFTY' })])
    await flush()
    expect(sent).toHaveLength(1)
    expect(sent[0].rows).toEqual([
      { id: 1, price: null, price_at: null, source: null, held: null },
      { id: 2, price: null, price_at: null, source: null, held: null },
    ])
  })

  it('asks nothing for a tick inside the band', async () => {
    const { resolver, sent } = make([[answer(25900)]])
    resolver.sync([rule()])
    await flush()
    resolver.tick(new Map([['NSE_INDEX:NIFTY', { price: 25905, at: 1 }]]), true)
    resolver.tick(new Map([['NSE_INDEX:NIFTY', { price: 25920, at: 2 }]]), true)
    await flush()
    expect(sent).toHaveLength(1)
  })

  it('asks once for ten ticks past the edge in one second, sending the price and the strike shown', async () => {
    const { resolver, sent, advance } = make([[answer(25900)], [answer(25950)]])
    resolver.sync([rule()])
    await flush()
    for (let i = 0; i < 10; i++) {
      advance(100)
      resolver.tick(new Map([['NSE_INDEX:NIFTY', { price: 25940 + i, at: i }]]), true)
    }
    await flush()
    expect(sent).toHaveLength(2)
    expect(sent[1].rows[0]).toMatchObject({ price: 25940, source: 'tick', held: 25900 })
    expect(sent[1].connected).toBe(true)
  })

  it('keeps a row to one ask in two seconds', async () => {
    const { resolver, sent, advance } = make([
      [answer(25900)],
      [answer(25950, { band: { low: 26000, high: 26100 } })],
      [answer(26000)],
    ])
    resolver.sync([rule()])
    await flush()
    const out = new Map([['NSE_INDEX:NIFTY', { price: 25960, at: 1 }]])
    advance(GAP_MS)
    resolver.tick(out, true)
    await flush()
    advance(GAP_MS - 1)
    resolver.tick(out, true)
    await flush()
    expect(sent).toHaveLength(2)
    advance(2)
    resolver.tick(out, true)
    await flush()
    expect(sent).toHaveLength(3)
  })

  it('asks on the first tick after waiting, and sends held null after a roll', async () => {
    const { resolver, sent } = make([
      [
        {
          ...answer(25900),
          state: 'waiting',
          ce: null,
          strike: null,
          band: null,
          words: "Waiting for NIFTY's price to pick the strike",
        },
      ],
      [answer(25900)],
      [answer(25900)],
    ])
    resolver.sync([rule()])
    await flush()
    expect(lineOf(resolver.rows.get(1)!, 0)).toBe("Waiting for NIFTY's price to pick the strike")
    resolver.tick(new Map([['NSE_INDEX:NIFTY', { price: 25912, at: 1 }]]), true)
    await flush()
    expect(sent).toHaveLength(2)
    await resolver.ask('valid', [1])
    expect(sent[2].rows[0].held).toBeNull()
  })

  it('does not lose a reconnect that lands while an ask is in flight', async () => {
    let release: (rows: Resolved[]) => void = () => {}
    const calls: number[] = []
    const resolver = new FollowResolver({
      send: (rows) => {
        calls.push(rows.length)
        return new Promise<Resolved[]>((resolve) => {
          release = resolve
        })
      },
      now: () => 0,
      problem: () => 'x',
    })
    resolver.sync([rule()])
    void resolver.ask('reconnect')
    expect(calls).toHaveLength(1)
    release([answer(25900)])
    await flush()
    expect(calls).toHaveLength(2)
    release([answer(25900)])
    await flush()
  })
})

describe('the lines under a row', () => {
  it('says where the strike moved from, for 60 seconds, and the server words for a roll as sent', async () => {
    const { resolver, advance } = make([
      [answer(25850)],
      [answer(25900, { price: 25931, price_at: Date.UTC(2026, 10, 13, 4, 42) })],
      [
        answer(25900, {
          ce: contract(25900, '2026-12-01'),
          words: 'Rolled to 1 Dec 2026 at 15:30',
        }),
      ],
    ])
    resolver.sync([rule()])
    await flush()
    await resolver.ask('reconnect')
    const row = resolver.rows.get(1) as NonNullable<ReturnType<typeof resolver.rows.get>>
    const at = row.note?.until ?? 0
    expect(lineOf(row, at - 60_000)).toBe('Moved from 25,850 CE at 10:12 (NIFTY 25,931)')
    expect(lineOf(row, at)).toBeNull()
    await resolver.ask('valid', [1])
    expect(lineOf(resolver.rows.get(1)!, advance(0))).toBe('Rolled to 1 Dec 2026 at 15:30')
  })

  it('shows the server words for a missing contract and for a closed market', async () => {
    const { resolver } = make([
      [
        {
          ...answer(0),
          state: 'missing',
          ce: null,
          strike: null,
          band: null,
          words: "NIFTY 25,900 CE is not in today's contract list.",
        },
      ],
    ])
    resolver.sync([rule()])
    await flush()
    expect(lineOf(resolver.rows.get(1)!, 0)).toBe(
      "NIFTY 25,900 CE is not in today's contract list."
    )
  })

  it('says a failed read in words while the row has no contract', async () => {
    const resolver = new FollowResolver({
      send: vi.fn().mockRejectedValue(new Error('x')),
      now: () => 0,
      problem: () => 'The strike could not be read just now.',
    })
    resolver.sync([rule()])
    await flush()
    expect(lineOf(resolver.rows.get(1)!, 0)).toBe('The strike could not be read just now.')
  })
})
