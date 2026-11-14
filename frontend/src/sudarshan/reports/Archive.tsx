import { useMutation, useQueryClient } from '@tanstack/react-query'
import {
  CalendarDays,
  CalendarRange,
  CheckCheck,
  CircleHelp,
  CircleSlash,
  Clock,
  Download,
  FileSpreadsheet,
  FileText,
  FileType2,
  LoaderCircle,
  RefreshCw,
  Sunrise,
  Sunset,
} from 'lucide-react'
import { type ReactNode, useEffect, useRef, useState } from 'react'
import { journalData, problemOf, reportFile, saveFile } from '../journal/api'
import { Problem } from '../journal/parts'
import type { ReportKind } from '../journal/types'
import { REPORTS_KEY, useArchive, useReportSettings } from './queries'
import {
  builtWords,
  DESK_WORDS,
  type DeskState,
  deliveryOf,
  deskOf,
  downloadName,
  emptyWords,
  hasCsv,
  kindTitle,
  periodWords,
  type ReportRow,
} from './words'

const KIND_ICON: Record<ReportKind, ReactNode> = {
  eod: <Sunset aria-hidden="true" className="size-4" />,
  brief: <Sunrise aria-hidden="true" className="size-4" />,
  weekly: <CalendarRange aria-hidden="true" className="size-4" />,
  monthly: <CalendarDays aria-hidden="true" className="size-4" />,
  fy: <FileType2 aria-hidden="true" className="size-4" />,
}

const DELIVERY_ICON = {
  sent: <CheckCheck aria-hidden="true" className="size-4 shrink-0 text-[color:var(--up)]" />,
  text: (
    <FileText aria-hidden="true" className="size-4 shrink-0 text-[color:var(--text-secondary)]" />
  ),
  not: <CircleSlash aria-hidden="true" className="size-4 shrink-0 text-[color:var(--down)]" />,
  unknown: (
    <CircleHelp aria-hidden="true" className="size-4 shrink-0 text-[color:var(--text-secondary)]" />
  ),
} as const

/** The glyph beside the desk's sentence: off says so, waiting waits, not known asks. */
const DESK_ICON: Record<Exclude<DeskState, 'on'>, ReactNode> = {
  starting: <Clock aria-hidden="true" className="mt-0.5 size-4 shrink-0" />,
  journal_off: <Clock aria-hidden="true" className="mt-0.5 size-4 shrink-0" />,
  journal_down: <Clock aria-hidden="true" className="mt-0.5 size-4 shrink-0" />,
  off: <CircleSlash aria-hidden="true" className="mt-0.5 size-4 shrink-0" />,
  unknown: <CircleHelp aria-hidden="true" className="mt-0.5 size-4 shrink-0" />,
}

const BUTTON =
  'inline-flex min-h-12 items-center justify-center gap-2 rounded-[0.875rem] border border-input bg-card px-4 text-[0.9375rem] font-semibold outline-none hover:bg-accent focus-visible:ring-[3px] focus-visible:ring-ring/50 disabled:opacity-60'

function Row({
  report,
  desk,
  onOpen,
}: {
  report: ReportRow
  desk: DeskState | undefined
  onOpen: (id: number) => void
}) {
  const delivery = deliveryOf(report, desk)
  const named = `${kindTitle(report.kind)} ${periodWords(report.period_key)}`
  // Review H4: the file is fetched and checked before it is saved, so a refusal is a sentence
  // under the row and never a file that will not open. One download at a time per row.
  const [busy, setBusy] = useState<'file' | 'csv' | null>(null)
  const [problem, setProblem] = useState<string | null>(null)
  const fetching = useRef(false)
  const download = async (format?: 'csv') => {
    if (fetching.current) return
    fetching.current = true
    setBusy(format ?? 'file')
    setProblem(null)
    try {
      saveFile(await reportFile(report.id, format), downloadName(report, format))
    } catch (error) {
      setProblem(problemOf(error).message)
    } finally {
      fetching.current = false
      setBusy(null)
    }
  }
  const glyph = (which: 'file' | 'csv', icon: ReactNode) =>
    busy === which ? <LoaderCircle aria-hidden="true" className="size-4 animate-spin" /> : icon
  return (
    <li
      data-testid="report"
      className="flex flex-col gap-2 rounded-[1.125rem] border bg-card px-4 py-3 shadow-[var(--shadow-card)]"
    >
      <div className="flex items-start justify-between gap-2">
        <span className="inline-flex items-center gap-1.5 rounded-full border px-2.5 py-1 text-sm font-bold">
          {KIND_ICON[report.kind]}
          {kindTitle(report.kind)}
        </span>
        <span className="text-sm text-[color:var(--text-secondary)] [font-variant-numeric:tabular-nums]">
          Built {builtWords(report.built_ms)}
        </span>
      </div>
      <p className="text-base font-bold [font-variant-numeric:tabular-nums]">
        {periodWords(report.period_key)}
      </p>
      <p className="flex items-start gap-1.5 text-sm font-semibold" data-testid="delivery">
        {DELIVERY_ICON[delivery.tone]}
        <span>
          {delivery.word}
          {delivery.detail && (
            <span className="block font-normal text-[color:var(--text-secondary)]">
              {delivery.detail}
            </span>
          )}
        </span>
      </p>
      <div className="grid grid-cols-2 gap-2">
        <button type="button" className={BUTTON} onClick={() => onOpen(report.id)}>
          <FileText aria-hidden="true" className="size-4" />
          Open
        </button>
        {report.file_name ? (
          <button
            type="button"
            className={BUTTON}
            disabled={busy !== null}
            aria-busy={busy === 'file'}
            onClick={() => void download()}
            aria-label={busy === 'file' ? `Preparing the file of ${named}` : `Download ${named}`}
          >
            {glyph('file', <Download aria-hidden="true" className="size-4" />)}
            {busy === 'file' ? 'Preparing the file' : 'Download'}
          </button>
        ) : (
          <span
            className={`${BUTTON} opacity-60`}
            role="note"
            aria-label={`${kindTitle(report.kind)} has no file; open it to read the text`}
          >
            No file
          </span>
        )}
        {hasCsv(report) && (
          <button
            type="button"
            className={`${BUTTON} col-span-2`}
            disabled={busy !== null}
            aria-busy={busy === 'csv'}
            onClick={() => void download('csv')}
            aria-label={
              busy === 'csv' ? `Preparing the CSV of ${named}` : `Download CSV of ${named}`
            }
          >
            {glyph('csv', <FileSpreadsheet aria-hidden="true" className="size-4" />)}
            {busy === 'csv' ? 'Preparing the file' : 'Download CSV'}
          </button>
        )}
      </div>
      {problem && <Problem>{problem}</Problem>}
    </li>
  )
}

/** A build on request: the row appears when the service has made it. */
function useBuild() {
  const queryClient = useQueryClient()
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null)
  const [asked, setAsked] = useState<ReportKind | null>(null)
  useEffect(
    () => () => {
      if (timer.current) clearTimeout(timer.current)
    },
    []
  )
  const build = useMutation({
    mutationFn: async (kind: 'fy' | 'monthly') => journalData('report_build', { kind }),
    onSuccess: (_done, kind) => {
      setAsked(kind)
      void queryClient.invalidateQueries({ queryKey: [...REPORTS_KEY, 'list'] })
      if (timer.current) clearTimeout(timer.current)
      timer.current = setTimeout(
        () => void queryClient.invalidateQueries({ queryKey: [...REPORTS_KEY, 'list'] }),
        4000
      )
    },
    onMutate: () => setAsked(null),
  })
  return { build, asked }
}

export function Archive({ onOpen }: { onOpen: (id: number) => void }) {
  const archive = useArchive()
  const settings = useReportSettings()
  const { build, asked } = useBuild()
  const desk = deskOf(settings.data)
  const deskWords = desk ? DESK_WORDS[desk] : null
  const rows = archive.data

  return (
    <div className="flex flex-col gap-3">
      <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
        <button
          type="button"
          className={BUTTON}
          disabled={build.isPending}
          onClick={() => build.mutate('fy')}
        >
          {build.isPending && build.variables === 'fy' ? (
            <LoaderCircle aria-hidden="true" className="size-4 animate-spin" />
          ) : (
            <FileType2 aria-hidden="true" className="size-4" />
          )}
          Build the FY pack now
        </button>
        <button
          type="button"
          className={BUTTON}
          disabled={build.isPending}
          onClick={() => build.mutate('monthly')}
        >
          {build.isPending && build.variables === 'monthly' ? (
            <LoaderCircle aria-hidden="true" className="size-4 animate-spin" />
          ) : (
            <CalendarDays aria-hidden="true" className="size-4" />
          )}
          Build this month&rsquo;s statement now
        </button>
      </div>
      {build.isError && <Problem>{problemOf(build.error).message}</Problem>}
      {asked && !build.isError && (
        <output className="block text-sm text-[color:var(--text-secondary)]">
          Asked for the {asked === 'fy' ? 'tax pack' : 'monthly statement'}. It appears below when
          it is ready.
        </output>
      )}
      {desk && desk !== 'on' && deskWords && (
        <p
          className="flex items-start gap-1.5 text-sm text-[color:var(--text-secondary)]"
          data-testid="desk"
        >
          {DESK_ICON[desk]}
          {deskWords}
        </p>
      )}
      {archive.isPending ? (
        <p className="flex items-center gap-2 py-6 text-base font-bold" aria-live="polite">
          <LoaderCircle aria-hidden="true" className="size-5 animate-spin" />
          Loading the reports
        </p>
      ) : archive.isError ? (
        <div className="flex flex-col gap-3">
          <Problem>{problemOf(archive.error).message}</Problem>
          <button type="button" className={BUTTON} onClick={() => archive.refetch()}>
            <RefreshCw aria-hidden="true" className="size-4" />
            Try again
          </button>
        </div>
      ) : rows && rows.length === 0 ? (
        <p className="rounded-[1.125rem] border bg-card px-4 py-6 text-center text-base shadow-[var(--shadow-card)]">
          {emptyWords(settings.data?.times)}
        </p>
      ) : (
        <ul className="flex flex-col gap-2" aria-label="Reports, newest first">
          {[...(rows ?? [])]
            .sort((a, b) => b.built_ms - a.built_ms)
            .map((report) => (
              <Row key={report.id} report={report} desk={desk} onOpen={onOpen} />
            ))}
        </ul>
      )}
    </div>
  )
}
