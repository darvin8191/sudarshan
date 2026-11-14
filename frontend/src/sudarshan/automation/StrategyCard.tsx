// SUDARSHAN M12.1: one strategy on the Automation page: what it is, what it holds, its day and
// its last alert. Colour never speaks alone: every chip and line has its icon and its word.
import { useQuery } from '@tanstack/react-query'
import {
  CircleCheck,
  CircleMinus,
  FlaskConical,
  LogOut,
  Pause,
  Play,
  Radio,
  TriangleAlert,
} from 'lucide-react'
import type { ReactNode } from 'react'
import { journalCall } from '@/sudarshan/journal/api'
import { paiseAmount, paiseText } from '@/sudarshan/journal/money'
import { dayKey } from './api'
import type { Mode, Strategy, StrategyState } from './types'
import { contractName, type Tone, toneOf, whenText } from './words'

const CHIP =
  'inline-flex h-7 shrink-0 items-center gap-1 rounded-full border px-2.5 text-[0.75rem] font-bold'
const STATES: Record<StrategyState, { word: string; tone: string; icon: ReactNode }> = {
  active: {
    word: 'Active',
    tone: 'border-[color:var(--live-border)] bg-[color:var(--up-tint)] text-[color:var(--up)]',
    icon: <Play aria-hidden="true" className="size-3.5" />,
  },
  exits_only: {
    word: 'Stopped for new entries',
    tone: 'border-[color:var(--warning-text)] bg-[color:var(--gold-tint)] text-[color:var(--warning-text)]',
    icon: <LogOut aria-hidden="true" className="size-3.5" />,
  },
  stopped: {
    word: 'Stopped',
    tone: 'border-[color:var(--border-strong)] text-[color:var(--text-secondary)]',
    icon: <Pause aria-hidden="true" className="size-3.5" />,
  },
}

export function StateChip({ state }: { state: StrategyState }) {
  const { word, tone, icon } = STATES[state]
  return (
    <span data-testid="auto-state" className={`${CHIP} ${tone}`}>
      {icon}
      {word}
    </span>
  )
}

export function ModeChip({ mode }: { mode: Mode }) {
  return mode === 'live' ? (
    <span
      data-testid="auto-mode"
      className={`${CHIP} border-[color:var(--live-border)] bg-[color:var(--live-tint)] text-[color:var(--live-ink)]`}
    >
      <Radio aria-hidden="true" className="size-3.5" />
      Live
    </span>
  ) : (
    <span
      data-testid="auto-mode"
      className={`${CHIP} border-[color:var(--sandbox-border)] bg-[color:var(--sandbox-tint)] text-[color:var(--sandbox-ink)]`}
    >
      <FlaskConical aria-hidden="true" className="size-3.5" />
      Sandbox
    </span>
  )
}

const TONES: Record<Tone, { ink: string; icon: typeof CircleCheck }> = {
  ok: { ink: 'text-[color:var(--up)]', icon: CircleCheck },
  quiet: { ink: 'text-[color:var(--text-secondary)]', icon: CircleMinus },
  warn: { ink: 'text-[color:var(--warning-text)]', icon: TriangleAlert },
  bad: { ink: 'text-[color:var(--down)]', icon: TriangleAlert },
}

/** An icon for the verdict's tone, for a line whose sentence says the rest. */
export function ToneIcon({ tone }: { tone: Tone }) {
  const { ink, icon: Icon } = TONES[tone]
  return <Icon aria-hidden="true" className={`mt-0.5 size-4 shrink-0 ${ink}`} />
}

function Line({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="flex gap-3 py-2 text-[0.875rem] leading-snug">
      <dt className="w-[4.75rem] shrink-0 text-[color:var(--text-secondary)]">{label}</dt>
      <dd className="min-w-0 flex-1 break-words">{children}</dd>
    </div>
  )
}

/** From this many trading days without an alert, an Active strategy's card says so. */
export const QUIET_DAYS = 3

/** Today's closed trades of this strategy in its mode, as the journal counts them. */
function DayLine({ strategy }: { strategy: Strategy }) {
  const day = useQuery({
    queryKey: [...dayKey(strategy.id), strategy.name, strategy.mode],
    queryFn: () =>
      journalCall('analytics', { mode: strategy.mode, strategy: strategy.name, period: 'today' }),
    refetchOnWindowFocus: 'always',
    retry: false,
  })
  const alerts = `${strategy.alerts_today} of ${strategy.max_alerts_day} alerts`
  if (day.isPending) return <>{alerts}</>
  if (day.isError) return <>{alerts} · the journal did not answer</>
  if (!day.data.enabled) return <>{alerts} · Journal is off</>
  const { trips, net_paise: net, charges_paise: charges } = day.data.data.metrics
  if (trips === 0) return <>{alerts} · no trade closed</>
  return (
    <>
      {alerts} · {trips} {trips === 1 ? 'trade' : 'trades'} closed ·{' '}
      <span
        className={`font-semibold whitespace-nowrap ${net > 0 ? 'text-[color:var(--up)]' : net < 0 ? 'text-[color:var(--down)]' : ''}`}
      >
        {paiseText(net)}
      </span>{' '}
      after est. charges {paiseAmount(charges)}
    </>
  )
}

export function StrategyCard({ strategy, actions }: { strategy: Strategy; actions?: ReactNode }) {
  const last = strategy.last_alert
  const where = strategy.mode === 'live' ? 'at Upstox' : 'in the Sandbox'
  return (
    <li
      data-testid={`auto-card-${strategy.id}`}
      className="rounded-[1.125rem] border bg-card px-4 py-4 shadow-[var(--shadow-card)]"
    >
      <h2 className="sd-display text-[1.375rem] leading-tight font-semibold break-words">
        {strategy.name}
      </h2>
      <div className="mt-1.5 flex flex-wrap gap-1.5">
        <StateChip state={strategy.state} />
        <ModeChip mode={strategy.mode} />
      </div>
      <p className="mt-2 text-[0.9375rem] font-medium">{strategy.trades}</p>
      <dl className="mt-1 divide-y divide-[color:var(--divider)]">
        <Line label="Now">
          {strategy.held_symbol
            ? `${strategy.held_side === 'short' ? 'Short' : 'Long'} ${contractName(strategy.held_symbol)} ${where}`
            : 'Holds nothing'}
        </Line>
        <Line label="Today">
          <DayLine strategy={strategy} />
        </Line>
        <Line label="Last alert">
          {last ? (
            <span className="flex items-start gap-1.5" data-verdict={last.verdict}>
              <ToneIcon tone={toneOf(last.verdict)} />
              <span>
                {whenText(last.received_ms)} · {last.sentence}
              </span>
            </span>
          ) : (
            'No alert has arrived yet'
          )}
        </Line>
      </dl>
      {strategy.state === 'active' && strategy.quiet_days >= QUIET_DAYS && (
        <p
          data-testid="auto-quiet"
          className="mt-2 flex items-start gap-1.5 text-[0.875rem] leading-snug font-semibold text-[color:var(--warning-text)]"
        >
          <TriangleAlert aria-hidden="true" className="mt-0.5 size-4 shrink-0" />
          <span>
            No alert for {strategy.quiet_days} trading days. An alert that TradingView halted or
            that expired looks exactly like a quiet strategy: check it in TradingView.
          </span>
        </p>
      )}
      {actions}
    </li>
  )
}
