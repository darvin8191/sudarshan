// SUDARSHAN M14, JD1: the Journal page, phone first. Every trade as Upstox reports it, paired into
// round trips by the journal service (blueprints/sudarshan_journal.py is its bridge). This is the
// shell: the title, the parts, the Sandbox switch, what is open kept in the address and the
// states in words. Each part arrives as its own change and replaces its stub.
import { useQuery } from '@tanstack/react-query'
import { LoaderCircle, Power, RefreshCw, TriangleAlert } from 'lucide-react'
import type { ReactNode } from 'react'
import { journalCall, problemOf } from '@/sudarshan/journal/api'
import { ModeChip } from '@/sudarshan/journal/ModeChip'
import { Segmented } from '@/sudarshan/journal/Segmented'
import { type JournalView, useJournalOverlay } from '@/sudarshan/journal/useJournalOverlay'

export const JOURNAL_KEY = ['sudarshan', 'journal'] as const
export const JOURNAL_STATE_KEY = [...JOURNAL_KEY, 'state'] as const

const PARTS: ReadonlyArray<{ id: JournalView; label: string }> = [
  { id: 'trades', label: 'Trades' },
  { id: 'analytics', label: 'Analytics' },
  { id: 'playbooks', label: 'Playbooks' },
]

const CARD = 'rounded-[1.125rem] border bg-card px-4 py-6 text-center shadow-[var(--shadow-card)]'
const ACTION =
  'inline-flex min-h-12 items-center justify-center gap-2 rounded-[0.875rem] px-4 text-[0.9375rem] font-semibold outline-none focus-visible:ring-[3px] focus-visible:ring-ring/50'

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
    <div className={CARD} aria-live="polite">
      <div className="mx-auto mb-2 flex size-10 items-center justify-center text-[color:var(--text-secondary)]">
        {icon}
      </div>
      <p className="text-base font-bold">{title}</p>
      {children}
    </div>
  )
}

/** What a part says until its own screen arrives. */
function Stub({ part }: { part: JournalView }) {
  const words: Record<JournalView, string> = {
    trades: 'Your trades will be listed here, open ones first.',
    analytics: 'Your results will be drawn here.',
    playbooks: 'Your setups will be listed here.',
  }
  return (
    <div className={CARD} data-testid={`journal-${part}`}>
      <p className="text-sm text-[color:var(--text-secondary)]">{words[part]}</p>
    </div>
  )
}

export default function Journal() {
  const { view, mode, setView, setMode } = useJournalOverlay()
  const state = useQuery({
    queryKey: JOURNAL_STATE_KEY,
    queryFn: async () => journalCall('state'),
    retry: false,
    staleTime: 30_000,
  })
  const result = state.data

  let body: ReactNode
  if (state.isPending) {
    body = (
      <Notice
        icon={<LoaderCircle aria-hidden="true" className="size-6 animate-spin" />}
        title="Loading the journal"
      />
    )
  } else if (state.isError) {
    body = (
      <Notice
        icon={<TriangleAlert aria-hidden="true" className="size-6" />}
        title={problemOf(state.error).message}
      >
        <button
          type="button"
          onClick={() => state.refetch()}
          className={`${ACTION} mt-3 border border-input bg-card hover:bg-accent`}
        >
          <RefreshCw aria-hidden="true" className="size-4" />
          Try again
        </button>
      </Notice>
    )
  } else if (result && !result.enabled) {
    body = (
      <Notice icon={<Power aria-hidden="true" className="size-6" />} title={result.message}>
        <p className="mx-auto mt-1 max-w-sm text-sm text-[color:var(--text-secondary)]">
          Your trades are kept. Turn the journal on in the server settings to see them.
        </p>
      </Notice>
    )
  } else {
    body = <Stub part={view} />
  }

  const usable = result?.enabled === true
  return (
    <div className="mx-auto flex w-full max-w-3xl flex-col gap-3 px-4 py-4 md:px-0">
      <div className="flex min-h-12 items-center justify-between gap-2">
        <h1 className="sd-display text-[1.75rem] leading-none font-bold md:text-[2rem]">Journal</h1>
        {usable && <ModeChip mode={mode} onChange={setMode} />}
      </div>
      {usable && (
        <Segmented label="Journal parts" value={view} options={PARTS} onChange={setView} />
      )}
      {body}
    </div>
  )
}
