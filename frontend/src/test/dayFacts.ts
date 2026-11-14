/**
 * The server's day facts (GET /sudarshan/session/today) for the days its own tests are pinned to
 * (sudarshan/tests/regimes_vectors.json, `day_facts`), as the server sends them: every instant in
 * epoch ms. The dashboard's tests read them, so the phone and the server agree on each day.
 */
import { readFileSync } from 'node:fs'
import { join } from 'node:path'

interface Case {
  now: string
  facts: Record<string, any>
}

const CASES = (
  JSON.parse(
    readFileSync(join(process.cwd(), '..', 'sudarshan', 'tests', 'regimes_vectors.json'), 'utf8')
  ).day_facts as { cases: Case[] }
).cases

/** IST wall time in epoch ms: at('2026-11-17', '09:15') or at('2026-11-17 09:15'). */
export function at(day: string, hhmm?: string): number {
  const [d, t] = hhmm === undefined ? day.split(' ') : [day, hhmm]
  return Date.parse(`${d}T${t}:00+05:30`)
}

/** The vector days, YYYY-MM-DD. */
export const VECTOR_DAYS = CASES.map((each) => each.now.slice(0, 10))

/** A vector day's answer as the server sends it. */
export function serverFacts(day: string): Record<string, unknown> {
  const found = CASES.find((each) => each.now.startsWith(day))
  if (!found) throw new Error(`no day facts vector for ${day}`)
  const raw = found.facts
  const stops = (rows: any[]) => rows.map((row) => ({ ...row, stops_at: at(row.stops_at) }))
  return {
    ...raw,
    exchanges: Object.fromEntries(
      Object.entries(raw.exchanges as Record<string, Record<string, string | null>>).map(
        ([code, hours]) => [
          code,
          Object.fromEntries(
            Object.entries(hours).map(([key, value]) => [key, value && at(day, value)])
          ),
        ]
      )
    ),
    expiries: stops(raw.expiries),
    stock_expiries: stops(raw.stock_expiries),
    next_open: raw.next_open && at(raw.next_open),
  }
}
