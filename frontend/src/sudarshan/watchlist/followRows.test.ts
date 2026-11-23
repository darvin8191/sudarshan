import { describe, expect, it, vi } from 'vitest'
import { answer, contract, rule } from './followFixtures'
import {
  addBody,
  chipOf,
  detailOf,
  draftOf,
  editBody,
  ended,
  expiryChoices,
  FollowResolver,
  fixedWords,
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

  // Review M3: every rule is relative, so a row on a picked strike still moves to another expiry.
  describe('a row on a fixed strike', () => {
    const open = [
      { date: '2026-11-24', label: 'Nearest 24 Nov', month: true },
      { date: '2026-12-01', label: '1 Dec', month: false },
      { date: '2026-12-08', label: '8 Dec', month: false },
      { date: '2026-12-29', label: '29 Dec (month)', month: true },
      { date: '2027-01-26', label: '26 Jan (month)', month: true },
    ]
    const pair = (expiry: 'nearest' | 'next' | 'month' | 'next_month') =>
      draftOf('strike_pair', undefined, { strike: 25900, expiry })

    it('says the date it is on today and where it moves, from the open expiries', () => {
      expect(previewOf('NIFTY', pair('nearest'), open)).toBe(
        'NIFTY 25,900 CE and PE as one row, nearest expiry (24 Nov today). When 24 Nov expires, this row moves to 1 Dec.'
      )
      expect(previewOf('NIFTY', pair('next'), open)).toBe(
        'NIFTY 25,900 CE and PE as one row, next expiry (1 Dec today). When 24 Nov expires, this row moves to 8 Dec.'
      )
      expect(previewOf('NIFTY', pair('month'), open)).toBe(
        "NIFTY 25,900 CE and PE as one row, this month's expiry (24 Nov today). When 24 Nov expires, this row moves to 29 Dec."
      )
      expect(previewOf('NIFTY', pair('next_month'), open)).toBe(
        "NIFTY 25,900 CE and PE as one row, next month's expiry (29 Dec today). When 24 Nov expires, this row moves to 26 Jan."
      )
      const one = {
        ...draftOf('atm_option', undefined, { strike: 25900, expiry: 'next' }),
        right: 'PE' as const,
      }
      expect(previewOf('NIFTY', one, open)).toBe(
        'NIFTY 25,900 PE, next expiry (1 Dec today). When 24 Nov expires, this row moves to 8 Dec.'
      )
    })

    it('says where it moves in words when the dates are not known, and never that it stays', () => {
      expect(previewOf('NIFTY', pair('nearest'))).toBe(
        'NIFTY 25,900 CE and PE as one row, nearest expiry. When it expires, this row moves to the next expiry.'
      )
      expect(previewOf('NIFTY', pair('next'))).toBe(
        'NIFTY 25,900 CE and PE as one row, next expiry. When the nearest expiry ends, this row moves to the one after it.'
      )
      expect(previewOf('NIFTY', pair('month'))).toBe(
        "NIFTY 25,900 CE and PE as one row, this month's expiry. When it expires, this row moves to next month's expiry."
      )
      expect(previewOf('NIFTY', pair('next_month'))).toBe(
        "NIFTY 25,900 CE and PE as one row, next month's expiry. When this month's expiry ends, this row moves to the month after."
      )
      for (const rule of ['nearest', 'next', 'month', 'next_month'] as const) {
        for (const said of [previewOf('NIFTY', pair(rule)), previewOf('NIFTY', pair(rule), open)])
          expect(said).not.toMatch(/for good|for ever|stays where it is/)
      }
      expect(fixedWords(25900)).toBe('Strike 25,900, fixed: it does not move with the price.')
    })

    it('says the expiry after it when the contract list does not show it yet', () => {
      expect(previewOf('NIFTY', pair('next_month'), open.slice(0, 4))).toBe(
        "NIFTY 25,900 CE and PE as one row, next month's expiry (29 Dec today). When 24 Nov expires, this row moves to the expiry after it."
      )
    })
  })
})

// M8.11: a row that stays on its date.
describe('a row that stays on its date', () => {
  const dated = (over = {}) =>
    rule({
      expiry_rule: 'date',
      expiry_on: '2026-11-24',
      strike_rule: 'fixed',
      strike: 25900,
      ...over,
    })

  it('says its date and that it stays there, contract or not', () => {
    expect(detailOf(dated(), contract(25900))).toBe('NFO · 24 Nov 2026 · stays on this date')
    expect(detailOf(dated(), null)).toBe('NFO · 24 Nov 2026 · stays on this date')
    expect(detailOf(rule(), null)).toBe('NFO · follows NIFTY')
  })

  it('has ended only when the server finds no contract on its date', () => {
    const missing = answer(25900, { state: 'missing', ce: null, strike: null })
    expect(ended({ rule: dated(), answer: missing })).toBe(true)
    expect(ended({ rule: dated(), answer: answer(25900) })).toBe(false)
    expect(ended({ rule: dated(), answer: null })).toBe(false)
    expect(ended({ rule: rule(), answer: missing })).toBe(false)
  })

  it('starts the picker’s form on the tab’s date, and offers that tab’s rule beside it (W4)', () => {
    const fixed = { strike: 25900, date: '2026-12-01', expiry: 'next' as const }
    expect(draftOf('strike_pair', undefined, fixed)).toMatchObject({
      expiry: 'date',
      expiryOn: '2026-12-01',
      strike: 25900,
    })
    expect(expiryChoices('strike_pair', null, fixed)).toEqual([
      { value: 'date', label: 'Stays on 1 Dec' },
      { value: 'next', label: 'Next' },
    ])
    expect(expiryChoices('strike_pair', null, { date: '2026-12-08' })).toEqual([
      { value: 'date', label: 'Stays on 8 Dec' },
    ])
    expect(expiryChoices('atm_option', '2026-11-24').map((each) => each.value)).toEqual([
      'date',
      'nearest',
      'next',
      'month',
      'next_month',
    ])
    expect(expiryChoices('atm_option', null).map((each) => each.value)).toEqual([
      'nearest',
      'next',
      'month',
      'next_month',
    ])
  })

  it('says it stops on its date, and sends the date only with the date rule', () => {
    const draft = draftOf('strike_pair', undefined, { strike: 25900, date: '2026-12-01' })
    expect(previewOf('NIFTY', draft)).toBe(
      'NIFTY 25,900 CE and PE as one row. Stays on 1 Dec 2026. When it expires the row stops and says so; it does not move to the next expiry.'
    )
    expect(
      previewOf('NIFTY', { ...draftOf('atm_option', dated({ strike_rule: 'atm', strike: null })) })
    ).toBe(
      'NIFTY ATM CE. Stays on 24 Nov 2026. The strike is picked from the price when the row is added, and moves with it. When it expires the row stops and says so; it does not move to the next expiry.'
    )
    const under = { underlying: 'NIFTY', underlying_exchange: 'NSE_INDEX' }
    expect(addBody(draft, under, null)).toEqual({
      kind: 'strike_pair',
      underlying: 'NIFTY',
      underlying_exchange: 'NSE_INDEX',
      expiry_rule: 'date',
      expiry_on: '2026-12-01',
      strike_rule: 'fixed',
      strike: 25900,
    })
    expect(editBody(draftOf('atm_option', dated()))).toEqual({
      expiry_rule: 'date',
      expiry_on: '2026-11-24',
      kind: 'atm_option',
      strike_rule: 'fixed',
      strike: 25900,
      opt_right: 'CE',
    })
    expect(editBody({ ...draftOf('atm_option', dated()), expiry: 'nearest' })).not.toHaveProperty(
      'expiry_on'
    )
  })

  it('asks the server again when its date changes', async () => {
    const { resolver, sent } = make([[answer(25900)], [answer(25900)]])
    resolver.sync([dated()])
    await flush()
    resolver.sync([dated()])
    await flush()
    expect(sent).toHaveLength(1)
    resolver.sync([dated({ expiry_on: '2026-12-01' })])
    await flush()
    expect(sent).toHaveLength(2)
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

  it('asks a row whose read keeps failing after 2 s, then 5 s, then every 15 s (review L13)', async () => {
    let at = 1_000_000
    let fail = true
    const send = vi.fn(async () => {
      if (fail) throw new Error('x')
      return [answer(25900)]
    })
    const resolver = new FollowResolver({
      send,
      now: () => at,
      problem: () => 'The strike could not be read just now.',
    })
    resolver.sync([rule()])
    await flush() // the first read fails
    expect(send).toHaveBeenCalledTimes(1)
    const tickAt = async (ms: number) => {
      at += ms
      resolver.tick(new Map([['NSE_INDEX:NIFTY', { price: 25910, at }]]), true)
      await flush()
    }
    for (const [wait, calls] of [
      [1_999, 1],
      [1, 2], // 2 s after the first failure
      [4_999, 2],
      [1, 3], // 5 s after the second
      [14_999, 3],
      [1, 4], // 15 s after the third
      [14_999, 4],
      [1, 5], // and every 15 s while it fails
    ] as const) {
      await tickAt(wait)
      expect(send).toHaveBeenCalledTimes(calls)
    }
    fail = false
    await tickAt(15_000)
    expect(send).toHaveBeenCalledTimes(6)
    expect(resolver.rows.get(1)?.fails).toBe(0)
    expect(lineOf(resolver.rows.get(1)!, at)).toBeNull()
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
