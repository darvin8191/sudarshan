/**
 * Pure tick-merge for the scalping terminal.
 *
 * The live WebSocket Quote feed and the REST MultiQuotes fallback each provide a
 * different subset of fields (the WS tick often omits change/change_percent, and
 * high/low for indices; MultiQuotes provides them). Merging FIELD BY FIELD — prefer
 * a FRESH WebSocket value only when that field exists, else MultiQuotes, else the
 * last WS snapshot — keeps prices live during market hours and refreshing after
 * hours WITHOUT blanking fields on every tick (which caused a visible flicker).
 *
 * Kept pure (no React/Date) so it is unit-testable; the caller passes `nowMs`.
 */
import type { QuotesData } from '@/api/trading'

export interface TickView {
  ltp?: number
  change?: number
  change_percent?: number
  open?: number
  high?: number
  low?: number
}

export function mergeTick(
  wsData: TickView | undefined,
  wsLastUpdate: number | undefined,
  mq: QuotesData | undefined,
  nowMs: number,
  staleMs: number,
  // SUDARSHAN: when the MultiQuotes snapshot was asked for. Given, the newer of
  // the tick and the snapshot wins, however old the tick is: the snapshot is
  // read once, not every 30 s, so "under 5 s" would hand a quiet leg back to a
  // snapshot older than the tick it already has.
  mqAt?: number | null
): TickView | undefined {
  if (!wsData && !mq) return undefined

  let wsFresh = false
  if (wsData?.ltp != null && wsLastUpdate != null) {
    wsFresh =
      mqAt != null
        ? mq === undefined || wsLastUpdate >= mqAt // SUDARSHAN: the newer wins
        : nowMs - wsLastUpdate < staleMs
  }

  // Per field: a fresh WS value if present, else MultiQuotes, else the WS snapshot.
  const pick = (wsVal?: number, mqVal?: number) =>
    wsFresh && wsVal != null ? wsVal : (mqVal ?? wsVal)

  const ltp = pick(wsData?.ltp, mq?.ltp)
  // SUDARSHAN: a tick's own previous close (prev_close, or cp in LTP mode) when
  // it carries one, else the snapshot's.
  const fields = (wsData ?? {}) as Record<string, unknown>
  const tickPrev = [fields.prev_close, fields.cp].find(
    (value): value is number => typeof value === 'number' && Number.isFinite(value) && value > 0
  )
  const prev = tickPrev ?? (mq?.prev_close || 0)
  const mqChange = prev && ltp ? ltp - prev : undefined
  const mqChangePct = prev && ltp ? ((ltp - prev) / prev) * 100 : undefined

  return {
    ltp,
    open: pick(wsData?.open, mq?.open),
    high: pick(wsData?.high, mq?.high),
    low: pick(wsData?.low, mq?.low),
    // SUDARSHAN: from the previous close whenever one is known, so the change
    // agrees with it; a tick's own change only when none is.
    change: mqChange ?? wsData?.change,
    change_percent: mqChangePct ?? wsData?.change_percent,
  }
}
