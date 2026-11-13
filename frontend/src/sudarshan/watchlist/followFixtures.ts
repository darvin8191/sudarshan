import type { Contract, FollowRow, Resolved } from './v2/types'

/** Shared fixtures of the follow-row tests: a rule, a contract and the server's answer for a strike. */
export const rule = (over: Partial<FollowRow> = {}): FollowRow => ({
  id: 1,
  kind: 'atm_option',
  section_id: null,
  position: 1,
  underlying: 'NIFTY',
  underlying_exchange: 'NSE_INDEX',
  expiry_rule: 'nearest',
  strike_rule: 'atm',
  offset: 0,
  strike: null,
  opt_right: 'CE',
  ...over,
})

export const contract = (strike: number, expiry = '2026-11-24', right = 'CE'): Contract => ({
  exchange: 'NFO',
  symbol: `NIFTY24NOV26${strike}${right}`,
  key: `NFO:NIFTY24NOV26${strike}${right}`,
  name: 'NIFTY',
  lot: 65,
  tick: 0.05,
  expiry,
  isin: null,
})

export const answer = (strike: number, over: Partial<Resolved> = {}): Resolved => ({
  id: 1,
  state: 'ok',
  ce: contract(strike),
  pe: null,
  future: null,
  strike,
  band: { low: strike - 30, high: strike + 30 },
  valid_until: null,
  price: strike + 12.35,
  price_at: Date.UTC(2026, 10, 13, 4, 42),
  words: `Now: NIFTY ${strike} CE, 24 Nov 2026 (NIFTY at ${strike + 12.35})`,
  ...over,
})

/** The server's answer for a current-month future row. */
export const futureAnswer = (id: number, over: Partial<Resolved> = {}): Resolved => ({
  id,
  state: 'ok',
  ce: null,
  pe: null,
  future: { ...contract(0), symbol: 'NIFTY24NOV26FUT', key: 'NFO:NIFTY24NOV26FUT' },
  strike: null,
  band: null,
  valid_until: null,
  price: null,
  price_at: null,
  words: 'Now: NIFTY future, 24 Nov 2026',
  ...over,
})
