/**
 * The Concentration card moving with ticks (P7 lane G): the rows the risk answer carries, priced
 * by `useTabLive` from fake feed lines, against the vectors the risk core runs (risk.json `rows`:
 * RW1 is the read, RW2 the same rows after one tick). "Real-time" only while ticks arrive; an
 * answer with no rows is the service's own bars with the time they were read.
 */
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { render, screen, within } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import type { SymbolData } from '@/lib/MarketDataManager'

const mocks = vi.hoisted(() => ({ lines: new Map(), connected: true, seen: [] as unknown[][] }))
vi.mock('@/sudarshan/live/useFrameLines', () => ({
  useFrameLines: (args: { instruments: unknown[]; enabled?: boolean }) => {
    mocks.seen.push(args.enabled === false ? [] : args.instruments)
    return {
      lines: mocks.lines,
      directions: new Map(),
      feed: { connected: mocks.connected, fallback: false, epoch: 1 },
    }
  },
}))

import type { HoldingRow, RiskConcentration, RiskReport } from '../types'
import { Concentration, readWords } from './Concentration'

interface Case {
  id: string
  rows: HoldingRow[]
  max_holding_pct: string
  max_sector_pct: string
  expect: RiskConcentration
}
const FILE = JSON.parse(
  readFileSync(
    join(process.cwd(), '..', 'sudarshan', 'portfolio', 'tests', 'vectors', 'risk.json'),
    'utf8'
  )
) as { rows: Case[] }
const [READ, TICKED] = FILE.rows
const READ_AT = Date.UTC(2026, 10, 13, 8, 35, 9) // 14:05:09 IST on 13 Nov 2026
const SNAPSHOT: RiskConcentration = {
  holdings: [{ label: 'Old figure', weight_pct: '100.0000', over_limit: true }],
  sectors: [],
  untagged_pct: '100.0000',
  flags: ['Old figure is 100% of what is held here, above your 15% limit.'],
}

function report(live: boolean): RiskReport {
  const base = {
    tab_id: null,
    as_of_day: 20261113,
    basis: 'On closing values up to 13 Nov 2026.',
    risk: { concentration: SNAPSHOT },
  }
  // The vectors carry the keys the rule reads; the rest of a row is what the tab's view adds.
  const rows = READ.rows.map((row) => ({
    ...row,
    today_mode: 'none',
    lines: row.lines.map((line) => ({
      account_id: 1,
      open_qty_u: line.qty_u,
      in_today_paise: 0,
      out_today_paise: 0,
      over_year_qty_u: 0,
      ...line,
    })),
  }))
  return (live
    ? {
        ...base,
        live: { read_at: READ_AT, max_holding_pct: '15.0000', max_sector_pct: '35.0000', rows },
      }
    : base) as unknown as RiskReport
}

const tick = (ltp: number, at: number, live = true): SymbolData => ({
  symbol: 'X',
  exchange: 'NSE',
  data: { ltp, prev_close: 240 },
  lastUpdate: at,
  updateSource: live ? 'websocket' : 'rest',
})

function show(live = true, open: boolean | null = true) {
  render(<Concentration report={report(live)} readAt={READ_AT + 400} marketOpen={() => open} />)
  return within(screen.getByRole('region', { name: 'Concentration' }))
}

/** The bars on screen as `[label, weight to one decimal, above the limit]`. */
function bars(card: ReturnType<typeof show>, title: string) {
  const list = card.getByRole('heading', { name: title }).parentElement as HTMLElement
  return [...list.children].slice(1).map((bar) => {
    const [label, weight] = [...(bar.firstElementChild as HTMLElement).children]
    return [
      label.textContent,
      weight.textContent,
      within(bar as HTMLElement).queryByText('Above your limit') !== null,
    ]
  })
}
const wanted = (rows: RiskConcentration['holdings']) =>
  rows.map((row) => [row.label, `${Number(row.weight_pct).toFixed(1)}%`, row.over_limit])

beforeEach(() => {
  mocks.lines = new Map()
  mocks.connected = true
  mocks.seen.length = 0
})

describe('the concentration card', () => {
  it('before any tick shows the service’s own weights from the rows, and is not Real-time', () => {
    const card = show()
    expect(bars(card, 'Largest holdings')).toEqual(wanted(READ.expect.holdings))
    expect(bars(card, 'Largest sectors')).toEqual(wanted(READ.expect.sectors))
    for (const flag of READ.expect.flags) expect(card.getByText(flag)).toBeTruthy()
    expect(card.queryByText('Real-time')).toBeNull()
    expect(card.getByText('Waiting for prices')).toBeTruthy()
    expect(card.getByText(/are as read at 14:05:09 on 13 Nov 2026\./)).toBeTruthy()
    // Only the listed rows that are held are subscribed: the fund, the loan and the sold one are not.
    expect(mocks.seen.at(-1)).toEqual([
      { symbol: 'ALPHA', exchange: 'NSE' },
      { symbol: 'GAMMA', exchange: 'NSE' },
      { symbol: 'ALPHA', exchange: 'NSE' },
    ])
  })

  it('a tick moves every weight, the limit marks and the sentences, by the service’s rule', () => {
    mocks.lines = new Map([
      ['NSE:ALPHA', tick(500, Date.now())],
      ['NSE:GAMMA', tick(150, Date.now())],
    ])
    const card = show()
    expect(bars(card, 'Largest holdings')).toEqual(wanted(TICKED.expect.holdings))
    expect(bars(card, 'Largest sectors')).toEqual(wanted(TICKED.expect.sectors))
    expect(TICKED.expect.flags).toHaveLength(4)
    for (const flag of TICKED.expect.flags) expect(card.getByText(flag)).toBeTruthy()
    expect(card.queryByText(/Gamma Ltd is/)).toBeNull() // 10.34% now: no longer above 15%
    expect(card.getByText('20.7% has no sector tag and is in no sector bar.')).toBeTruthy()
    expect(card.getByText('Real-time')).toBeTruthy()
  })

  it('a tick from before the read, or a REST answer, leaves the figures as read', () => {
    mocks.lines = new Map([['NSE:ALPHA', tick(500, READ_AT - 1)]])
    const early = show()
    expect(bars(early, 'Largest holdings')).toEqual(wanted(READ.expect.holdings))
    expect(early.queryByText('Real-time')).toBeNull()
    document.body.innerHTML = ''
    mocks.lines = new Map([['NSE:ALPHA', tick(500, Date.now(), false)]])
    const rest = show()
    expect(bars(rest, 'Largest holdings')).toEqual(wanted(READ.expect.holdings))
    expect(rest.queryByText('Real-time')).toBeNull()
  })

  it('says the market is closed, or that it is reconnecting, never Real-time', () => {
    expect(show(true, false).getByText('Market closed')).toBeTruthy()
    document.body.innerHTML = ''
    mocks.connected = false
    const down = show()
    expect(down.getByText(/Connecting|Reconnecting/)).toBeTruthy()
    expect(down.queryByText('Real-time')).toBeNull()
  })

  it('an answer with no rows is the service’s own bars, with the second they were read', () => {
    mocks.lines = new Map([['NSE:ALPHA', tick(500, Date.now())]])
    const card = show(false)
    expect(bars(card, 'Largest holdings')).toEqual([['Old figure', '100.0%', true]])
    expect(card.getByText(SNAPSHOT.flags[0])).toBeTruthy()
    expect(
      card.getByText(
        'As of 14:05:09 on 13 Nov 2026, when this view was read. These bars do not move with prices; read again for newer values.'
      )
    ).toBeTruthy()
    expect(card.queryByText(/Real-time|Waiting for prices|Market closed/)).toBeNull()
    expect(mocks.seen.every((instruments) => instruments.length === 0)).toBe(true)
    expect(readWords(READ_AT)).toBe('14:05:09 on 13 Nov 2026')
  })
})
