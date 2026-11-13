// SUDARSHAN M15.10, JD6: the Reports page, phone first. What the platform wrote for the owner and
// whether it reached Telegram (the Archive), and which reports are made (the Settings). The
// part shown and the report open are in the address, so Back closes the report first.
import { useQuery } from '@tanstack/react-query'
import { LoaderCircle, Power, RefreshCw, TriangleAlert } from 'lucide-react'
import { journalCall, problemOf } from '@/sudarshan/journal/api'
import { JOURNAL_STATE_KEY } from '@/sudarshan/journal/keys'
import { Segmented } from '@/sudarshan/journal/Segmented'
import { Archive } from '@/sudarshan/reports/Archive'
import { ReportSheet } from '@/sudarshan/reports/ReportSheet'
import { Settings } from '@/sudarshan/reports/Settings'
import { type ReportsView, useReportsOverlay } from '@/sudarshan/reports/useReportsOverlay'

const PARTS: ReadonlyArray<{ id: ReportsView; label: string }> = [
  { id: 'archive', label: 'Archive' },
  { id: 'settings', label: 'Settings' },
]

const CARD = 'rounded-[1.125rem] border bg-card px-4 py-6 text-center shadow-[var(--shadow-card)]'

export default function Reports() {
  const { view, reportId, setView, open, close } = useReportsOverlay()
  const state = useQuery({
    queryKey: JOURNAL_STATE_KEY,
    queryFn: async () => journalCall('state'),
    retry: false,
    staleTime: 30_000,
  })
  const result = state.data
  const usable = result?.enabled === true

  let body: React.ReactNode
  if (state.isPending) {
    body = (
      <div className={CARD} aria-live="polite">
        <LoaderCircle aria-hidden="true" className="mx-auto mb-2 size-6 animate-spin" />
        <p className="text-base font-bold">Loading the reports</p>
      </div>
    )
  } else if (state.isError) {
    body = (
      <div className={CARD} aria-live="polite">
        <TriangleAlert aria-hidden="true" className="mx-auto mb-2 size-6" />
        <p className="text-base font-bold">{problemOf(state.error).message}</p>
        <button
          type="button"
          onClick={() => state.refetch()}
          className="mt-3 inline-flex min-h-12 items-center justify-center gap-2 rounded-[0.875rem] border border-input bg-card px-4 text-[0.9375rem] font-semibold hover:bg-accent"
        >
          <RefreshCw aria-hidden="true" className="size-4" />
          Try again
        </button>
      </div>
    )
  } else if (result && !result.enabled) {
    body = (
      <div className={CARD} aria-live="polite">
        <Power aria-hidden="true" className="mx-auto mb-2 size-6" />
        <p className="text-base font-bold">{result.message}</p>
        <p className="mx-auto mt-1 max-w-sm text-sm text-[color:var(--text-secondary)]">
          Your reports are kept. Turn the journal on in the server settings to see them.
        </p>
      </div>
    )
  } else {
    body = view === 'archive' ? <Archive onOpen={open} /> : <Settings />
  }

  return (
    <div className="mx-auto flex w-full max-w-3xl flex-col gap-3 px-4 py-4 md:px-0">
      <div className="flex min-h-12 items-center">
        <h1 className="sd-display text-[1.75rem] leading-none font-bold md:text-[2rem]">Reports</h1>
      </div>
      {usable && (
        <Segmented label="Reports parts" value={view} options={PARTS} onChange={setView} />
      )}
      {body}
      {usable && reportId !== null && <ReportSheet key={reportId} id={reportId} onClose={close} />}
    </div>
  )
}
