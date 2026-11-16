// SUDARSHAN M13.8 (P8 U5): a fair score of TRINETRA's readings, computed nightly by the service from
// what was published and what the index then did (sudarshan/signals/perf.py): per module, index
// and bias, how many cases, the share that moved the way the bias said and the median move, after
// 1, 3 and 5 sessions (and the open's gap for the gap bias). Every figure has its number of cases
// beside it; under 30 cases a row says "too few cases to judge" and shows no figure. A neutral
// reading names no direction, so its row has a median move and never a share.
import { BarChart3, LoaderCircle, RefreshCw, TriangleAlert } from 'lucide-react'
import { problemOf, sentenceOf } from './api'
import { BiasMark } from './BiasMark'
import { INSTRUMENTS, KNOWN_MODULES, MONTHS, type PerfRow, WORDS } from './types'
import { usePerformance } from './useSignals'
import { moduleName } from './words'

/** The fewest cases a row is judged on (contract PERF min_cases). */
export const MIN_CASES = 30

const ACTION =
  'inline-flex min-h-12 items-center justify-center gap-2 rounded-[0.875rem] border border-input bg-card px-4 text-[0.9375rem] font-semibold outline-none hover:bg-accent focus-visible:ring-[3px] focus-visible:ring-ring/50'

/** "61.54%", and a signed move with a true minus: "−0.42%", "+0.18%". */
function share(value: string | null): string | null {
  return value === null || value.trim() === '' ? null : `${value.trim()}%`
}

function move(value: string | null): string | null {
  if (value === null || value.trim() === '') return null
  const said = value.trim()
  const n = Number(said)
  if (!Number.isFinite(n)) return null
  if (n === 0) return `${said.replace(/^[+-]/, '')}%`
  return n < 0 ? `−${said.replace(/^-/, '')}%` : `+${said.replace(/^\+/, '')}%`
}

/** The day a score runs to: 20261114 as "14 Nov 2026". */
function dayOf(day: number | null): string | null {
  if (day === null) return null
  const year = Math.floor(day / 10_000)
  const month = Math.floor(day / 100) % 100
  const date = day % 100
  return month >= 1 && month <= 12 ? `${date} ${MONTHS[month - 1]} ${year}` : null
}

function after(sessions: number): string {
  if (sessions === 0) return 'at the open (the gap)'
  return sessions === 1 ? 'after 1 session' : `after ${sessions} sessions`
}

/**
 * What a row may say: scored (the service judged it, on at least 30 cases), too few cases to judge,
 * or a neutral reading's median move with no share (it names no direction to score).
 */
export type Verdict = 'scored' | 'too_few' | 'no_direction'

export function verdictOf(row: PerfRow): Verdict {
  if (row.cases < MIN_CASES) return 'too_few'
  if (row.judged) return 'scored'
  return row.bias === 'neutral' || row.words === WORDS.no_direction ? 'no_direction' : 'too_few'
}

/** A row's line of figures, the number of cases first. */
export function rowWords(row: PerfRow): string {
  const cases = row.cases === 1 ? '1 case' : `${row.cases} cases`
  const verdict = verdictOf(row)
  if (verdict === 'too_few') return `${cases} · ${WORDS.too_few}`
  const median = move(row.median_move_pct)
  const parts = [cases]
  if (verdict === 'scored') {
    const hit = share(row.hit_pct)
    if (hit !== null) parts.push(`${hit} moved the way it said`)
  }
  if (median !== null) parts.push(`median move ${median}`)
  if (verdict === 'no_direction') parts.push(WORDS.no_direction)
  return parts.join(' · ')
}

function Row({ row }: { row: PerfRow }) {
  return (
    <li data-verdict={verdictOf(row)} className="flex flex-col gap-0.5 py-2">
      <span className="flex flex-wrap items-center gap-x-1.5 text-[0.875rem] font-semibold">
        {row.instrument}
        <BiasMark bias={row.bias} colour={false} />
        <span className="font-medium text-[color:var(--text-secondary)]">
          {after(row.sessions)}
        </span>
      </span>
      <span className="text-[0.875rem]">{rowWords(row)}</span>
    </li>
  )
}

const MODULE_ORDER = new Map<string, number>(KNOWN_MODULES.map((module, at) => [module, at]))
const INSTRUMENT_ORDER = new Map<string, number>(INSTRUMENTS.map((name, at) => [name, at]))
const BIAS_ORDER: Record<string, number> = { bull: 0, bear: 1, neutral: 2 }

export function Performance() {
  const score = usePerformance(true)
  if (score.isPending) {
    return (
      <output className="flex min-h-12 items-center gap-2 text-[0.875rem] text-[color:var(--text-secondary)]">
        <LoaderCircle aria-hidden="true" className="size-4 animate-spin" />
        Reading the score
      </output>
    )
  }
  if (score.isError || !score.data) {
    return (
      <div className="flex flex-col items-start gap-2 rounded-[1.125rem] border bg-card px-4 py-3 shadow-[var(--shadow-card)]">
        <p role="alert" className="flex items-start gap-1.5 text-[0.9375rem] font-medium">
          <TriangleAlert
            aria-hidden="true"
            className="mt-0.5 size-4 shrink-0 text-[color:var(--warning-text)]"
          />
          {problemOf(score.error).message}
        </p>
        <button type="button" onClick={() => void score.refetch()} className={ACTION}>
          <RefreshCw aria-hidden="true" className="size-4" />
          Try again
        </button>
      </div>
    )
  }
  const data = score.data
  const rows = Array.isArray(data.rows) ? data.rows : []
  const modules = [...new Set(rows.map((row) => row.module))].sort(
    (a, b) =>
      (MODULE_ORDER.get(a) ?? KNOWN_MODULES.length) -
        (MODULE_ORDER.get(b) ?? KNOWN_MODULES.length) || a.localeCompare(b)
  )
  const upTo = dayOf(data.as_of_day)
  return (
    <div className="flex flex-col gap-3">
      <p className="text-[0.875rem] text-[color:var(--text-secondary)]">
        How often each reading moved the way its bias said, from what TRINETRA published and what
        the index then did. The number of cases is beside every figure.
        {upTo ? ` Scored up to ${upTo}.` : ''}
      </p>
      {rows.length === 0 ? (
        <p className="rounded-[1.125rem] border bg-card px-4 py-3 text-[0.9375rem] shadow-[var(--shadow-card)]">
          {sentenceOf(data.words) ?? WORDS.no_scores}
        </p>
      ) : (
        modules.map((module) => (
          <section
            key={module}
            aria-label={moduleName(module)}
            className="flex flex-col rounded-[1.125rem] border bg-card px-4 pt-3 pb-1.5 shadow-[var(--shadow-card)]"
          >
            <h3 className="flex items-center gap-2 text-[0.9375rem] font-bold">
              <BarChart3 aria-hidden="true" className="size-4" />
              {moduleName(module)}
            </h3>
            <ul className="flex flex-col divide-y divide-[color:var(--divider)]">
              {rows
                .filter((row) => row.module === module)
                .sort(
                  (a, b) =>
                    (INSTRUMENT_ORDER.get(a.instrument) ?? 9) -
                      (INSTRUMENT_ORDER.get(b.instrument) ?? 9) ||
                    (BIAS_ORDER[a.bias] ?? 9) - (BIAS_ORDER[b.bias] ?? 9) ||
                    a.sessions - b.sessions
                )
                .map((row) => (
                  <Row key={`${row.instrument}:${row.bias}:${row.sessions}`} row={row} />
                ))}
            </ul>
          </section>
        ))
      )}
    </div>
  )
}
