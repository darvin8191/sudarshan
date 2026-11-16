// SUDARSHAN M13.7 (P8 U2): the TRINETRA page, owner only. What TRINETRA, the owner's other system on
// the same server, chooses to publish, beside his own screens: the header line with section 3.6's
// words and the reading's age, one card per (module, index), his positions against the regime, the
// recent alerts, the timeline with "Why", and the Performance tab. Everything comes through
// OpenAlgo's bridge (blueprints/sudarshan_signals.py) from sudarshan-signals; nothing of
// TRINETRA's own is read. Switched off, the page shows its own screen, as other features do.
import {
  BellRing,
  Clock,
  Eye,
  Info,
  LoaderCircle,
  Power,
  RefreshCw,
  TriangleAlert,
  Unplug,
} from 'lucide-react'
import { type ReactNode, useState } from 'react'
import { useSearchParams } from 'react-router'
import { Segmented } from '@/sudarshan/journal/Segmented'
import { FreshnessLine } from '@/sudarshan/signals/FreshnessLine'
import { Performance } from '@/sudarshan/signals/Performance'
import { PositionsCard } from '@/sudarshan/signals/PositionsCard'
import { ReadingCard } from '@/sudarshan/signals/ReadingCard'
import { Timeline } from '@/sudarshan/signals/Timeline'
import {
  type AlertRow,
  DEFAULT_LANG,
  KNOWN_MODULES,
  LEVEL_WORDS,
  type Note,
  OTHER_MODULE_NAME,
  type State,
  WORDS,
} from '@/sudarshan/signals/types'
import { lapsedAt, orderReadings, useSignals } from '@/sudarshan/signals/useSignals'
import {
  changeSubject,
  readingSubject,
  WhySheet,
  type WhySubject,
} from '@/sudarshan/signals/WhySheet'
import { type FreshNow, fill, moduleName, timeWords } from '@/sudarshan/signals/words'

type View = 'readings' | 'performance'

const PARTS: ReadonlyArray<{ id: View; label: string }> = [
  { id: 'readings', label: 'Readings' },
  { id: 'performance', label: 'Performance' },
]

const CARD = 'rounded-[1.125rem] border bg-card px-4 py-6 text-center shadow-[var(--shadow-card)]'
const ACTION =
  'inline-flex min-h-12 items-center justify-center gap-2 rounded-[0.875rem] border border-input bg-card px-4 text-[0.9375rem] font-semibold outline-none hover:bg-accent focus-visible:ring-[3px] focus-visible:ring-ring/50'

const KNOWN = new Set<string>(KNOWN_MODULES)

function Notice({
  icon,
  title,
  children,
}: {
  icon: ReactNode
  title: string
  children?: ReactNode
}) {
  return (
    <div className={CARD} aria-live="polite" data-testid="trinetra-notice">
      <div className="mx-auto mb-2 flex size-10 items-center justify-center text-[color:var(--text-secondary)]">
        {icon}
      </div>
      <p className="text-base font-bold">{title}</p>
      {children}
    </div>
  )
}

/** The view switched off, not set up, or its records unreadable: the service's sentence. */
function OffScreen({ message }: { message: string }) {
  const off = message === WORDS.off
  return (
    <Notice
      icon={
        off ? (
          <Power aria-hidden="true" className="size-6" />
        ) : (
          <Unplug aria-hidden="true" className="size-6" />
        )
      }
      title={message}
    >
      {off && (
        <p className="mx-auto mt-1 max-w-sm text-sm text-[color:var(--text-secondary)]">
          SUDARSHAN reads nothing from TRINETRA while this is off. It is turned on on the server;
          the README's TRINETRA section has the steps.
        </p>
      )}
    </Notice>
  )
}

/** A note under the header: the service's sentence, rebuilt from its key where it sent none. */
function noteWords(note: Note, heard: boolean): string {
  if (typeof note.words === 'string' && note.words.trim() !== '') return note.words
  const count = note.count ?? 0
  if (note.key === 'refused') {
    if (heard) return count === 1 ? WORDS.refused_one : fill(WORDS.refused, { count })
    return count === 1 ? WORDS.refused_none_one : fill(WORDS.refused_none, { count })
  }
  if (note.key === 'gaps') return count === 1 ? WORDS.gaps_one : fill(WORDS.gaps, { count })
  if (note.key === 'newer_format') return WORDS.newer_format
  return ''
}

function Header({
  state,
  fresh,
  hearing,
  connected,
  problem,
  onRetry,
}: {
  state: State
  fresh: FreshNow
  hearing: ReturnType<typeof useSignals>['hearing']
  connected: boolean
  problem: string | null
  onRetry: () => void
}) {
  const notes = state.notes
    .map((note) => ({ note, words: noteWords(note, fresh.status !== 'never') }))
    .filter(({ words }) => words !== '')
  return (
    <section
      aria-label="TRINETRA's state"
      className="flex flex-col gap-2 rounded-[1.125rem] border bg-card px-4 py-3 shadow-[var(--shadow-card)]"
    >
      <FreshnessLine fresh={fresh} hearing={hearing} connected={connected} />
      {notes.length > 0 && (
        <ul className="flex flex-col gap-1">
          {notes.map(({ note, words }) => (
            <li
              key={note.key}
              data-note={note.key}
              className="flex items-start gap-1.5 text-[0.8125rem] leading-snug font-medium text-[color:var(--text-secondary)]"
            >
              {note.key === 'clock' ? (
                <Clock aria-hidden="true" className="mt-0.5 size-3.5 shrink-0" />
              ) : (
                <Info aria-hidden="true" className="mt-0.5 size-3.5 shrink-0" />
              )}
              {words}
            </li>
          ))}
        </ul>
      )}
      {problem && (
        <div className="flex flex-wrap items-center justify-between gap-2">
          <p role="alert" className="flex items-start gap-1.5 text-[0.875rem] font-medium">
            <TriangleAlert
              aria-hidden="true"
              className="mt-0.5 size-4 shrink-0 text-[color:var(--warning-text)]"
            />
            {problem}
          </p>
          <button type="button" onClick={onRetry} className={ACTION}>
            <RefreshCw aria-hidden="true" className="size-4" />
            Try again
          </button>
        </div>
      )}
    </section>
  )
}

const LEVEL_ICON = { info: Info, watch: Eye, act: BellRing } as const

/** TRINETRA's recent alerts: watch and act ones also went to Telegram, info ones stay here. */
function Alerts({ alerts, now }: { alerts: AlertRow[]; now: number }) {
  if (alerts.length === 0) return null
  return (
    <section
      aria-labelledby="sd-trinetra-alerts"
      className="flex flex-col gap-1 rounded-[1.125rem] border bg-card px-4 pt-3 pb-2 shadow-[var(--shadow-card)]"
    >
      <h2 id="sd-trinetra-alerts" className="flex items-center gap-2 text-[0.9375rem] font-bold">
        <BellRing aria-hidden="true" className="size-4" />
        TRINETRA's alerts
      </h2>
      <ul className="flex flex-col divide-y divide-[color:var(--divider)]">
        {alerts.map((alert) => {
          const Icon = LEVEL_ICON[alert.level] ?? Info
          return (
            <li key={alert.id} data-level={alert.level} className="flex flex-col gap-0.5 py-2">
              <span className="flex flex-wrap items-center gap-x-1.5 text-[0.75rem] font-semibold text-[color:var(--text-tertiary)]">
                <span className="inline-flex items-center gap-1 text-[color:var(--text-primary)]">
                  <Icon aria-hidden="true" className="size-3.5" />
                  {LEVEL_WORDS[alert.level] ?? LEVEL_WORDS.info}
                </span>
                · {timeWords(alert.at_ms, now)} · {moduleName(alert.module)}
                {alert.instrument ? ` · ${alert.instrument}` : ''}
              </span>
              <span className="text-[0.9375rem] font-semibold break-words">{alert.title}</span>
              {alert.text && (
                <span className="text-[0.875rem] break-words whitespace-pre-line text-[color:var(--text-secondary)]">
                  {alert.text}
                </span>
              )}
            </li>
          )
        })}
      </ul>
    </section>
  )
}

function Readings({
  state,
  fresh,
  now,
  onWhy,
}: {
  state: State
  fresh: FreshNow
  now: number
  onWhy: (subject: WhySubject) => void
}) {
  const ordered = orderReadings(state.readings, now)
  const live = ordered.filter((reading) => !lapsedAt(reading, now))
  const known = live.filter((reading) => KNOWN.has(reading.module))
  const other = live.filter((reading) => !KNOWN.has(reading.module))
  const lapsed = ordered.filter((reading) => lapsedAt(reading, now))
  const card = (reading: (typeof ordered)[number]) => (
    <ReadingCard
      key={`${reading.module}:${reading.instrument}`}
      reading={reading}
      fresh={fresh}
      now={now}
      onWhy={(shown) => onWhy(readingSubject(shown, now, fresh.colour && !lapsedAt(shown, now)))}
    />
  )
  return (
    <>
      {fresh.status === 'never' ? (
        <p className="px-1 text-[0.875rem] text-[color:var(--text-secondary)]">
          The first reading TRINETRA publishes shows here within a second or two.
        </p>
      ) : ordered.length === 0 ? (
        <p className="px-1 text-[0.875rem] text-[color:var(--text-secondary)]">
          TRINETRA's last document carried no readings.
        </p>
      ) : null}
      {known.length > 0 && (
        <div className="grid grid-cols-1 gap-2.5 md:grid-cols-2" data-testid="trinetra-cards">
          {known.map(card)}
        </div>
      )}
      {other.length > 0 && (
        <section aria-label={OTHER_MODULE_NAME} className="flex flex-col gap-2">
          <h2 className="px-1 text-[0.9375rem] font-bold">{OTHER_MODULE_NAME}</h2>
          <div className="grid grid-cols-1 gap-2.5 md:grid-cols-2">{other.map(card)}</div>
        </section>
      )}
      {lapsed.length > 0 && (
        <section aria-label="Lapsed readings" className="flex flex-col gap-2">
          <h2 className="px-1 text-[0.9375rem] font-bold text-[color:var(--text-secondary)]">
            Lapsed readings
          </h2>
          <div className="grid grid-cols-1 gap-2.5 md:grid-cols-2">{lapsed.map(card)}</div>
        </section>
      )}
      <PositionsCard state={state} fresh={fresh} now={now} />
      <Alerts alerts={state.alerts} now={now} />
      <Timeline now={now} onWhy={(change) => onWhy(changeSubject(change, now))} />
    </>
  )
}

export default function Trinetra() {
  const [params, setParams] = useSearchParams()
  const view: View = params.get('view') === 'performance' ? 'performance' : 'readings'
  const signals = useSignals()
  const { state, fresh, now, off, problem } = signals
  const [why, setWhy] = useState<WhySubject | null>(null)

  const choose = (next: View) => {
    const kept = new URLSearchParams(params)
    if (next === 'readings') kept.delete('view')
    else kept.set('view', next)
    setParams(kept, { replace: true })
  }

  let body: ReactNode
  if (signals.pending) {
    body = (
      <Notice
        icon={<LoaderCircle aria-hidden="true" className="size-6 animate-spin" />}
        title="Reading TRINETRA's state"
      />
    )
  } else if (off !== null) {
    body = <OffScreen message={off} />
  } else if (!state || !fresh) {
    body = (
      <Notice
        icon={<TriangleAlert aria-hidden="true" className="size-6" />}
        title={problem?.message ?? WORDS.not_answering}
      >
        <button type="button" onClick={signals.refetch} className={`${ACTION} mt-3`}>
          <RefreshCw aria-hidden="true" className="size-4" />
          Try again
        </button>
      </Notice>
    )
  } else {
    body = (
      <>
        <Header
          state={state}
          fresh={fresh}
          hearing={signals.hearing}
          connected={signals.connected}
          problem={problem?.message ?? null}
          onRetry={signals.refetch}
        />
        <Segmented label="TRINETRA parts" value={view} options={PARTS} onChange={choose} />
        {view === 'performance' ? (
          <Performance />
        ) : (
          <Readings state={state} fresh={fresh} now={now} onWhy={setWhy} />
        )}
      </>
    )
  }

  return (
    <div className="mx-auto flex w-full max-w-3xl flex-col gap-3 px-4 py-4 md:px-0">
      <div className="flex flex-col gap-1">
        <h1 className="sd-display flex items-center gap-2 text-[1.75rem] leading-none font-bold md:text-[2rem]">
          <Eye aria-hidden="true" className="size-6" />
          TRINETRA
        </h1>
        <p className="text-[0.875rem] text-[color:var(--text-secondary)]">
          The readings TRINETRA publishes, beside your own screens.
        </p>
      </div>
      {body}
      <WhySheet
        subject={why}
        stateLang={state?.lang ?? DEFAULT_LANG}
        onClose={() => setWhy(null)}
      />
    </div>
  )
}
