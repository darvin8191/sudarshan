// SUDARSHAN M14, JD1: the Journal page, phone first. Every trade as Upstox reports it, paired into
// round trips by the journal service (blueprints/sudarshan_journal.py is its bridge). This is the
// shell: the title, the parts, the Sandbox switch, what is open kept in the address and the
// states in words. Each part arrives as its own change and replaces its stub.
import { useQuery } from '@tanstack/react-query'
import { LoaderCircle, Power, RefreshCw, TriangleAlert } from 'lucide-react'
import { type ReactNode, useCallback, useEffect, useState } from 'react'
import { Analytics } from '@/sudarshan/journal/Analytics'
import { journalCall, problemOf } from '@/sudarshan/journal/api'
import { JOURNAL_STATE_KEY } from '@/sudarshan/journal/keys'
import { ModeChip } from '@/sudarshan/journal/ModeChip'
import { PlaybookSheetHost } from '@/sudarshan/journal/PlaybookSheet'
import { Playbooks } from '@/sudarshan/journal/Playbooks'
import { Segmented } from '@/sudarshan/journal/Segmented'
import { TripSheet } from '@/sudarshan/journal/TripSheet'
import { TripsList } from '@/sudarshan/journal/TripsList'
import {
  type JournalOverlay,
  type JournalView,
  useJournalOverlay,
} from '@/sudarshan/journal/useJournalOverlay'

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

export default function Journal() {
  const { view, mode, overlay, setView, setMode, open, close } = useJournalOverlay()
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
    body =
      view === 'trades' ? (
        <TripsList mode={mode} onOpen={(tradeId) => open({ kind: 'trade', tradeId })} />
      ) : view === 'analytics' ? (
        <Analytics mode={mode} />
      ) : (
        <Playbooks
          mode={mode}
          onOpen={(playbookId) => open({ kind: 'playbook', playbookId })}
          onNew={() => open({ kind: 'playbook', playbookId: null })}
        />
      )
  }

  const usable = result?.enabled === true

  // Review H2: a sheet holding changes that are not saved is not shut by Back. When Back takes its
  // entry, the sheet stays, the entry is put back and the sheet asks, as it does for Close and a
  // tap outside. (Back out of a sheet opened by a link leaves the page; its changes are kept in the
  // tab and come back when it is opened again.)
  const [held, setHeld] = useState<JournalOverlay | null>(null)
  const [asked, setAsked] = useState(0)
  const sheet = usable ? (overlay ?? held) : null
  const gone = overlay === null && held !== null
  useEffect(() => {
    if (!gone || !held) return
    open(held)
    setAsked((n) => n + 1)
  }, [gone, held, open])
  const closeSheet = useCallback(() => {
    setHeld(null)
    close()
  }, [close])
  const holding = useCallback(
    (shown: JournalOverlay) => (dirty: boolean) => setHeld(dirty ? shown : null),
    []
  )

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
      {sheet?.kind === 'playbook' && (
        <PlaybookSheetHost
          key={sheet.playbookId ?? 'new'}
          playbookId={sheet.playbookId}
          onClose={closeSheet}
          onDirty={holding(sheet)}
          asked={asked}
        />
      )}
      {sheet?.kind === 'trade' && (
        <TripSheet
          key={sheet.tradeId}
          tradeId={sheet.tradeId}
          open
          onClose={closeSheet}
          onDirty={holding(sheet)}
          asked={asked}
        />
      )}
    </div>
  )
}
