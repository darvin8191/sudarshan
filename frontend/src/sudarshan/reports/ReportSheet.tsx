import { useQuery } from '@tanstack/react-query'
import { LoaderCircle } from 'lucide-react'
import { journalData, problemOf } from '../journal/api'
import { Problem } from '../journal/parts'
import { BottomSheet } from '../watchlist/BottomSheet'
import { REPORTS_KEY } from './queries'
import { builtWords, kindTitle, periodWords, type ReportRow } from './words'

/** One report's text, as it went out, to read on the phone. The file is the Download button's. */
export function ReportSheet({ id, onClose }: { id: number; onClose: () => void }) {
  const query = useQuery({
    queryKey: [...REPORTS_KEY, 'one', id],
    queryFn: async () => (await journalData('report_get', { id })) as ReportRow,
    retry: false,
    staleTime: 0,
  })
  const report = query.data
  return (
    <BottomSheet
      open
      onOpenChange={(next) => !next && onClose()}
      title={report ? kindTitle(report.kind) : 'Report'}
      description={
        report
          ? `${periodWords(report.period_key)}, built ${builtWords(report.built_ms)}`
          : undefined
      }
    >
      {query.isPending ? (
        <p className="flex items-center gap-2 py-6 text-base font-bold" aria-live="polite">
          <LoaderCircle aria-hidden="true" className="size-5 animate-spin" />
          Loading the report
        </p>
      ) : query.isError ? (
        <div className="flex flex-col gap-3 pb-4">
          <Problem>{problemOf(query.error).message}</Problem>
          <button
            type="button"
            onClick={() => query.refetch()}
            className="min-h-12 rounded-[0.875rem] border border-input bg-card px-4 text-[0.9375rem] font-semibold hover:bg-accent"
          >
            Try again
          </button>
        </div>
      ) : (
        <pre
          data-testid="report-text"
          className="mb-4 max-h-[60dvh] overflow-auto whitespace-pre-wrap break-words rounded-[0.875rem] border bg-background p-3 font-mono text-sm leading-relaxed [font-variant-numeric:tabular-nums]"
        >
          {report?.text}
        </pre>
      )}
    </BottomSheet>
  )
}
