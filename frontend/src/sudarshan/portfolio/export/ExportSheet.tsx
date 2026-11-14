// SUDARSHAN P7 F9 (M7.40): the Export sheet of one of the owner's own tabs (design p7 9.6): a view
// (Holdings, Entries, Performance, Allocation, Income) and a format (CSV, PDF), handed to the phone
// as a file. The whole tab is exported whatever filter the screen shows, and the sheet says so.
// While the server's switch for the new exports is off, only the entries as CSV are offered, with
// the sentence that says why. Never offered on the Upstox tab, and no family viewer reaches it.
import { Download } from 'lucide-react'
import { useEffect, useState } from 'react'
import { cn } from '@/lib/utils'
import { BottomSheet } from '@/sudarshan/watchlist/BottomSheet'
import { getExportPdf, portfolioCall, saveDownload } from '../api'
import { CHOICE, LABEL, OFF, ON, PRIMARY, ProblemLine } from '../sheets/TabFields'
import { useSend } from '../sheets/useSend'
import { EXPORT_VIEWS, type ExportView, type TabInfo } from '../types'

export const VIEW_LABELS: Record<ExportView, string> = {
  holdings: 'Holdings',
  activity: 'Entries',
  performance: 'Performance',
  allocation: 'Allocation',
  income: 'Income',
}
export const FORMATS = ['csv', 'pdf'] as const
export type ExportFormat = (typeof FORMATS)[number]
export const WHOLE_TAB = 'The whole tab is exported, whatever filter the screen shows.'
export const EXPORTS_OFF =
  'The other views and the PDF are switched off on the server. They show here once a release switches them on.'

/** What can be chosen: everything while the switch is on, else the entries as CSV only. */
export function choices(on: boolean): {
  views: readonly ExportView[]
  formats: readonly ExportFormat[]
} {
  return on ? { views: EXPORT_VIEWS, formats: FORMATS } : { views: ['activity'], formats: ['csv'] }
}

function Options<V extends string>({
  legend,
  options,
  value,
  onChange,
}: {
  legend: string
  options: readonly { value: V; label: string }[]
  value: V
  onChange: (value: V) => void
}) {
  return (
    <fieldset className="flex min-w-0 flex-col gap-1.5">
      <legend className={cn(LABEL, 'pb-1.5')}>{legend}</legend>
      <div className="flex flex-wrap gap-2">
        {options.map((option) => (
          <button
            key={option.value}
            type="button"
            aria-pressed={option.value === value}
            onClick={() => onChange(option.value)}
            className={cn(CHOICE, option.value === value ? ON : OFF)}
          >
            {option.label}
          </button>
        ))}
      </div>
    </fieldset>
  )
}

export function ExportSheet({
  open,
  onClose,
  tab,
  on,
}: {
  open: boolean
  onClose: () => void
  tab: TabInfo | null
  /** The server's switch for the new exports, as the portfolio's state says it. */
  on: boolean
}) {
  const offered = choices(on)
  // Nothing chosen yet: the first view on offer, which changes once the switch is known to be on.
  const [view, setView] = useState<ExportView | null>(null)
  const [format, setFormat] = useState<ExportFormat>('csv')
  const send = useSend()
  const { clear } = send
  useEffect(() => {
    if (open) clear()
  }, [open, clear])
  const shownView = view !== null && offered.views.includes(view) ? view : offered.views[0]
  const shownFormat = offered.formats.includes(format) ? format : offered.formats[0]
  const download = async () => {
    if (tab === null) return
    const done = await send.run(async () => {
      const result =
        shownFormat === 'pdf'
          ? await getExportPdf({ id: tab.id, view: shownView })
          : await portfolioCall('export_csv', { id: tab.id, view: shownView })
      if (result.enabled) saveDownload(result.data)
    })
    if (done) onClose()
  }
  return (
    <BottomSheet
      open={open && tab !== null}
      onOpenChange={(next) => !next && onClose()}
      title="Export"
      description={tab?.name}
    >
      <div className="flex flex-col gap-4">
        <Options
          legend="What to export"
          options={offered.views.map((v) => ({ value: v, label: VIEW_LABELS[v] }))}
          value={shownView}
          onChange={setView}
        />
        <Options
          legend="As"
          options={offered.formats.map((f) => ({ value: f, label: f.toUpperCase() }))}
          value={shownFormat}
          onChange={setFormat}
        />
        {!on && <p className="text-sm text-[color:var(--text-secondary)]">{EXPORTS_OFF}</p>}
        <p className="text-sm text-[color:var(--text-secondary)]">{WHOLE_TAB}</p>
        <ProblemLine problem={send.problem} />
        <button
          type="button"
          className={PRIMARY}
          disabled={send.pending}
          onClick={() => void download()}
        >
          <Download aria-hidden="true" className="size-4" />
          {send.pending
            ? 'Preparing the file'
            : `Download ${VIEW_LABELS[shownView]} as ${shownFormat.toUpperCase()}`}
        </button>
      </div>
    </BottomSheet>
  )
}
