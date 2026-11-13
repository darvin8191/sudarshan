import { Clock, RefreshCw, TrendingUp } from 'lucide-react'
import { useEffect, useState } from 'react'
import { Link } from 'react-router'
import { cn } from '@/lib/utils'
import type { Ranking } from './useRanked'
import {
  ageLine,
  HOLD_MS,
  leftOutSentence,
  readAgo,
  timesLine,
  underlyingChip,
  underlyingOf,
} from './v2/ranked'

/**
 * M8.9 F16: what a ranked list says over its rows: the age line, the server's words for each
 * state, the Sign in button, the Refresh ranking button, the underlying chips and the "Ranking
 * updated" line. Every word that is not the age line is the server's, as sent. A ranking is "Ranked
 * by Upstox" with its age, never "Live"; only the prices under it are real-time, and the rows carry
 * their own price lines for that.
 */

const BUTTON =
  'inline-flex min-h-12 items-center gap-2 rounded-[0.875rem] border border-input bg-card px-4 text-[0.9375rem] font-semibold text-primary outline-none hover:bg-accent focus-visible:ring-[3px] focus-visible:ring-ring/50 disabled:opacity-50'

/**
 * The tick the age label is redrawn from: once a minute, and once a second only while the Refresh
 * button counts its minute. It asks for nothing; a hidden page draws nothing (it catches up when
 * shown).
 */
function useTick(fast: boolean): void {
  const [, setNow] = useState(() => Date.now())
  useEffect(() => {
    let id: ReturnType<typeof setInterval> | null = null
    const stop = () => {
      if (id !== null) clearInterval(id)
      id = null
    }
    const start = () => {
      stop()
      id = setInterval(() => setNow(Date.now()), fast ? 1000 : 60_000)
    }
    const change = () => {
      if (document.visibilityState === 'hidden') stop()
      else {
        setNow(Date.now())
        start()
      }
    }
    if (document.visibilityState !== 'hidden') start()
    document.addEventListener('visibilitychange', change)
    return () => {
      stop()
      document.removeEventListener('visibilitychange', change)
    }
  }, [fast])
}

/** Pulling the page down from its top asks for a read, as the Refresh button does. */
const PULL_PX = 80

function usePullDown(onPull: () => void): boolean {
  const [pulling, setPulling] = useState(false)
  useEffect(() => {
    let from: number | null = null
    let far = false
    const start = (event: TouchEvent) => {
      from = window.scrollY <= 0 ? (event.touches[0]?.clientY ?? null) : null
      far = false
    }
    const move = (event: TouchEvent) => {
      if (from === null) return
      far = (event.touches[0]?.clientY ?? from) - from >= PULL_PX
      setPulling(far)
    }
    const end = () => {
      if (far) onPull()
      from = null
      far = false
      setPulling(false)
    }
    window.addEventListener('touchstart', start, { passive: true })
    window.addEventListener('touchmove', move, { passive: true })
    window.addEventListener('touchend', end)
    window.addEventListener('touchcancel', end)
    return () => {
      window.removeEventListener('touchstart', start)
      window.removeEventListener('touchmove', move)
      window.removeEventListener('touchend', end)
      window.removeEventListener('touchcancel', end)
    }
  }, [onPull])
  return pulling
}

export function RankedLine({ ranking, name }: { ranking: Ranking; name: string }) {
  const { data, problem, reading, got, held, rows, size, underlying } = ranking
  const holding =
    data !== null &&
    got !== null &&
    ['ok', 'stale', 'closed'].includes(data.state) &&
    Date.now() - got < HOLD_MS
  useTick(holding)
  const now = Date.now() // the tick above only redraws; the time is read at the draw
  const [both, setBoth] = useState(false)
  const pulling = usePullDown(ranking.pull)
  const inWindow = holding && got !== null && now - got < HOLD_MS
  const age =
    data?.made_at != null && ['ok', 'stale', 'unavailable', 'wait'].includes(data.state)
      ? ageLine(data.made_at, now)
      : null
  const left =
    data && (data.state === 'ok' || data.state === 'stale') ? leftOutSentence(data) : null
  // The server's words for every state but ok and stale, whose line is the age line drawn here.
  const said =
    data && data.state !== 'ok' && data.state !== 'stale' && data.words.trim() ? data.words : null

  // The underlyings in the rows, most contracts first.
  const counts = new Map<string, number>()
  for (const row of rows) {
    const each = underlyingOf(row.contract.symbol, row.contract.exchange)
    counts.set(each, (counts.get(each) ?? 0) + 1)
  }
  const chips = [...counts].sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0])).slice(0, 8)

  return (
    <section
      aria-label={`${name}, ranked by Upstox`}
      data-ranked-line="true"
      className="flex flex-col gap-1 px-1"
    >
      {!data && !problem && (
        <output className="text-[0.8125rem] font-medium">Reading the ranking from Upstox.</output>
      )}
      {problem && (
        <p role="alert" className="text-[0.8125rem] font-semibold">
          {problem}
        </p>
      )}
      {age && data && (
        <button
          type="button"
          aria-expanded={both}
          onClick={() => setBoth((on) => !on)}
          data-age-line={age.stale ? 'stale' : 'fresh'}
          className={cn(
            'flex min-h-12 w-full items-center gap-2 rounded-lg text-left text-[0.8125rem] outline-none focus-visible:ring-2 focus-visible:ring-ring',
            age.stale
              ? 'font-bold text-[color:var(--warning-text)]'
              : 'font-medium text-[color:var(--text-secondary)]'
          )}
        >
          {age.stale ? (
            <Clock aria-hidden="true" className="size-4 shrink-0" />
          ) : (
            <TrendingUp aria-hidden="true" className="size-4 shrink-0" />
          )}
          <span>
            {age.text}
            {both && data.made_at != null && data.read_at != null && (
              <span className="block font-medium text-[color:var(--text-secondary)]">
                {timesLine(data.made_at, data.read_at)}
              </span>
            )}
          </span>
        </button>
      )}
      {said && <p className="text-[0.8125rem] font-semibold">{said}</p>}
      {left && (
        <p className="text-[0.8125rem] font-medium text-[color:var(--text-secondary)]">{left}</p>
      )}
      {ranking.updated && (
        <p
          data-ranking-updated="true"
          className="text-[0.8125rem] font-semibold text-[color:var(--info)]"
        >
          {ranking.updated}
        </p>
      )}
      {pulling && (
        <output className="text-[0.8125rem] font-semibold">
          Release to read the ranking again
        </output>
      )}
      {data?.state === 'signin' && (
        <Link to="/autologin" className={cn(BUTTON, 'self-start')}>
          Sign in
        </Link>
      )}
      {chips.length > 1 && (
        <fieldset
          aria-label="Underlying"
          className="m-0 flex min-w-0 gap-2 overflow-x-auto border-0 p-0"
        >
          {chips.map(([each, count]) => (
            <button
              key={each}
              type="button"
              aria-pressed={underlying === each}
              onClick={() => ranking.setUnderlying(underlying === each ? null : each)}
              className={cn(
                'inline-flex min-h-12 shrink-0 items-center rounded-full border px-4 text-[0.8125rem] font-semibold outline-none focus-visible:ring-2 focus-visible:ring-ring',
                underlying === each
                  ? 'border-primary bg-[color:var(--brand-tint)] font-bold'
                  : 'border-input bg-card'
              )}
            >
              {underlying === each && <span aria-hidden="true">{'✓ '}</span>}
              {each} {count}
            </button>
          ))}
        </fieldset>
      )}
      {underlying !== null && (
        <p
          data-underlying-count="true"
          className="text-[0.8125rem] font-medium text-[color:var(--text-secondary)]"
        >
          {underlyingChip(counts.get(underlying) ?? 0, rows.length, underlying)}
        </p>
      )}
      <div className="flex flex-wrap items-center gap-2">
        <button
          type="button"
          disabled={reading || inWindow}
          onClick={ranking.pull}
          className={BUTTON}
        >
          <RefreshCw aria-hidden="true" className={cn('size-4', reading && 'animate-spin')} />
          {inWindow && got !== null ? readAgo(got, now) : 'Refresh ranking'}
        </button>
        {data && data.rows.length > 0 && (
          <button
            type="button"
            onClick={() => ranking.setSize(size === 20 ? 50 : 20)}
            className={BUTTON}
          >
            {size === 20 ? 'Show the top 50' : 'Show the top 20'}
          </button>
        )}
      </div>
      {held && inWindow && got !== null && (
        <output
          data-held="true"
          className="text-[0.8125rem] font-medium text-[color:var(--text-secondary)]"
        >
          {readAgo(got, now)}. Showing that read.
        </output>
      )}
    </section>
  )
}
