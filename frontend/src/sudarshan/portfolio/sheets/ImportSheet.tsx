// SUDARSHAN M7.22: bring entries in from a file and take them out again (design 4.11).
// Pick the format, upload (within 1.2 MB), read the preview (counts, then the problem rows in
// words), add the rows with the tab version the preview saw, and "Remove this import" if it was
// wrong. "Download entries" writes the tab's entries as a file of the generic format.

import { useQueryClient } from '@tanstack/react-query'
import { Download, FileUp, TriangleAlert, Undo2 } from 'lucide-react'
import { useEffect, useRef, useState } from 'react'
import { cn } from '@/lib/utils'
import { BottomSheet } from '@/sudarshan/watchlist/BottomSheet'
import { PortfolioError, portfolioCall } from '../api'
import { PORTFOLIO_KEY } from '../tab/ActivityTab'
import {
  type Account,
  IMPORT_FORMATS,
  IMPORT_MAX_FILE_BYTES,
  type ImportCommitted,
  type ImportFormat,
  type ImportPreview,
  type ImportRow,
  type TabInfo,
  WORDS,
} from '../types'
import { FIELD, LABEL, PRIMARY, ProblemLine, SECONDARY } from './TabFields'
import { useSend } from './useSend'

export const FORMAT_WORDS: Record<ImportFormat, { label: string; hint: string }> = {
  generic: {
    label: 'Generic CSV',
    hint: 'Columns: date, action, symbol, exchange, isin, quantity, price, amount, charges, account, tds, ratio, note.',
  },
  zerodha: {
    label: 'Zerodha tradebook',
    hint: 'Equity trades only. Zerodha gives 365 days per download, so several files are normal.',
  },
}

const ROW_WORDS = { new: 'New', dup: 'Already in this tab', bad: 'Problem' } as const

/** A file's text; `File.text` where the browser has it, a reader where it does not. */
function readText(file: File): Promise<string> {
  if (typeof file.text === 'function') return file.text()
  return new Promise((resolve, reject) => {
    const reader = new FileReader()
    reader.onload = () => resolve(String(reader.result ?? ''))
    reader.onerror = () => reject(reader.error)
    reader.readAsText(file)
  })
}

/** Hands the owner a file: a link click on an object URL, then the URL is released. */
function saveFile(filename: string, text: string) {
  const url = URL.createObjectURL(new Blob([text], { type: 'text/csv;charset=utf-8' }))
  const link = document.createElement('a')
  link.href = url
  link.download = filename
  document.body.appendChild(link)
  link.click()
  link.remove()
  URL.revokeObjectURL(url)
}

function unwrap<T>(result: { enabled: true; data: T } | { enabled: false; message: string }): T {
  if (!result.enabled) {
    throw new PortfolioError({ code: 'unavailable', message: result.message, entryId: null })
  }
  return result.data
}

function Counts({ preview }: { preview: ImportPreview }) {
  const chips: Array<[string, number]> = [
    ['New', preview.rows_new],
    ['Already in this tab', preview.rows_dup],
    ['With problems', preview.rows_bad],
  ]
  return (
    <ul aria-label="Counts" className="flex flex-wrap gap-2">
      {chips.map(([label, n]) => (
        <li
          key={label}
          className="inline-flex min-h-8 items-center gap-1.5 rounded-full border border-[color:var(--border-strong)] bg-card px-3 text-sm font-semibold"
        >
          {label === 'With problems' && n > 0 && (
            <TriangleAlert aria-hidden="true" className="size-4 text-[color:var(--warning-text)]" />
          )}
          {label}: {n}
        </li>
      ))}
    </ul>
  )
}

function RowLine({ row }: { row: ImportRow }) {
  return (
    <li className="flex min-h-12 items-start gap-2 py-2 text-sm">
      <span
        className={cn(
          'mt-0.5 inline-flex shrink-0 items-center rounded-full border px-2 text-xs font-bold',
          row.status === 'bad'
            ? 'border-[color:var(--warning-text)] text-[color:var(--warning-text)]'
            : 'border-[color:var(--border-strong)] text-[color:var(--text-secondary)]'
        )}
      >
        {ROW_WORDS[row.status]}
      </span>
      <span>{row.words ?? `Row ${row.row}`}</span>
    </li>
  )
}

export function ImportSheet({
  open,
  onClose,
  tab,
  accounts,
  onChanged,
}: {
  open: boolean
  onClose: () => void
  tab: TabInfo | null
  accounts: readonly Account[]
  /** The tab's entries changed (added or removed): the page reads them again. */
  onChanged: () => void
}) {
  const own = accounts.filter((a) => !a.system)
  const [format, setFormat] = useState<ImportFormat>('generic')
  const [accountId, setAccountId] = useState<number | ''>('')
  const [preview, setPreview] = useState<ImportPreview | null>(null)
  const [committed, setCommitted] = useState<ImportCommitted | null>(null)
  const [removed, setRemoved] = useState<number | null>(null)
  const [tooBig, setTooBig] = useState(false)
  const picker = useRef<HTMLInputElement>(null)
  const send = useSend()
  const queries = useQueryClient()

  // biome-ignore lint/correctness/useExhaustiveDependencies: a fresh sheet on each opening only
  useEffect(() => {
    if (!open) return
    setFormat('generic')
    setAccountId(own.length === 1 ? own[0].id : '')
    setPreview(null)
    setCommitted(null)
    setRemoved(null)
    setTooBig(false)
    send.clear()
  }, [open])

  const changed = () => {
    void queries.invalidateQueries({ queryKey: PORTFOLIO_KEY })
    onChanged()
  }

  const choose = async (file: File | undefined) => {
    if (!file || !tab) return
    setTooBig(false)
    send.clear()
    if (file.size > IMPORT_MAX_FILE_BYTES) {
      setTooBig(true)
      return
    }
    await send.run(async () => {
      const text = await readText(file)
      const result = await portfolioCall('import_preview', {
        id: tab.id,
        format,
        filename: file.name,
        text,
        ...(accountId === '' ? {} : { account_id: accountId }),
      })
      setPreview(unwrap(result))
    })
    if (picker.current) picker.current.value = ''
  }

  const add = async () => {
    if (!preview) return
    await send.run(async () => {
      // The version the preview saw: the service checks it again and writes in one step.
      const result = await portfolioCall('import_commit', {
        id: preview.batch_id,
        version: preview.tab_version,
      })
      setCommitted(unwrap(result))
      changed()
    })
  }

  const discard = async () => {
    if (!preview) return
    const ok = await send.run(async () => {
      unwrap(await portfolioCall('import_discard', { id: preview.batch_id }))
    })
    if (ok) setPreview(null)
  }

  const undo = async () => {
    if (!preview || !committed) return
    await send.run(async () => {
      const result = await portfolioCall('import_discard', {
        id: preview.batch_id,
        version: committed.tab_version,
      })
      setRemoved(unwrap(result).removed)
      changed()
    })
  }

  const download = async () => {
    if (!tab) return
    await send.run(async () => {
      const file = unwrap(await portfolioCall('export_csv', { id: tab.id }))
      saveFile(file.filename, file.text)
    })
  }

  const stage = removed !== null ? 'removed' : committed ? 'added' : preview ? 'preview' : 'pick'
  return (
    <BottomSheet
      open={open}
      onOpenChange={(next) => !next && onClose()}
      title="Import entries"
      description={tab?.name}
    >
      <div className="flex flex-col gap-4 overflow-y-auto px-4 pb-4">
        {stage === 'pick' && (
          <>
            <fieldset className="flex flex-col gap-2" disabled={send.pending}>
              <legend className={LABEL}>Format</legend>
              {IMPORT_FORMATS.map((key) => (
                <label
                  key={key}
                  className={cn(
                    'flex min-h-12 items-start gap-3 rounded-[0.875rem] px-3 py-2',
                    format === key
                      ? 'border-2 border-[color:var(--brand)] bg-[color:var(--brand-tint)]'
                      : 'border border-[color:var(--border-strong)] bg-card'
                  )}
                >
                  <input
                    type="radio"
                    name="import-format"
                    className="mt-1 size-5 accent-[color:var(--brand)]"
                    checked={format === key}
                    onChange={() => setFormat(key)}
                  />
                  <span className="flex flex-col">
                    <span className="text-base font-semibold">{FORMAT_WORDS[key].label}</span>
                    <span className="text-xs text-[color:var(--text-secondary)]">
                      {FORMAT_WORDS[key].hint}
                    </span>
                  </span>
                </label>
              ))}
            </fieldset>
            {own.length > 1 && (
              <label className="flex flex-col gap-1.5">
                <span className={LABEL}>Account for rows that do not name one</span>
                <select
                  className={FIELD}
                  value={accountId}
                  disabled={send.pending}
                  onChange={(event) =>
                    setAccountId(event.target.value === '' ? '' : Number(event.target.value))
                  }
                >
                  <option value="">Choose an account</option>
                  {own.map((a) => (
                    <option key={a.id} value={a.id}>
                      {a.name}
                    </option>
                  ))}
                </select>
              </label>
            )}
            <input
              ref={picker}
              type="file"
              accept=".csv,text/csv"
              className="sr-only"
              aria-label="Choose a file"
              tabIndex={-1}
              disabled={send.pending}
              onChange={(event) => void choose(event.target.files?.[0])}
            />
            <button
              type="button"
              className={PRIMARY}
              disabled={send.pending || !tab}
              onClick={() => picker.current?.click()}
            >
              <FileUp aria-hidden="true" className="size-4" />
              {send.pending ? 'Checking the file' : 'Choose a file'}
            </button>
            <p className="text-xs text-[color:var(--text-tertiary)]">
              Up to 1.2 MB and 5,000 rows a file. Nothing is saved until you add the rows.
            </p>
            {tooBig && (
              <p
                role="alert"
                className="rounded-[0.875rem] border border-[color:var(--down)] bg-[color:var(--down-tint)] px-3 py-2 text-sm font-medium text-[color:var(--down)]"
              >
                {WORDS.import_too_big}
              </p>
            )}
          </>
        )}

        {stage === 'preview' && preview && (
          <>
            <p className="text-base font-bold">{preview.words}</p>
            <p className="text-xs text-[color:var(--text-tertiary)]">
              {preview.filename} · {FORMAT_WORDS[preview.format].label}
            </p>
            <Counts preview={preview} />
            {preview.rows.length > 0 && (
              <ul
                aria-label="Rows"
                className="max-h-64 divide-y divide-[color:var(--divider)] overflow-y-auto rounded-[0.875rem] border px-3"
              >
                {preview.rows.map((row) => (
                  <RowLine key={row.row} row={row} />
                ))}
              </ul>
            )}
            {preview.truncated && (
              <p className="text-xs text-[color:var(--text-tertiary)]">
                Only the first {preview.rows.length} of {preview.rows_total} rows are shown. All{' '}
                {preview.rows_new} new rows are added.
              </p>
            )}
          </>
        )}

        {stage === 'added' && committed && (
          <p aria-live="polite" className="text-base font-bold">
            {committed.added === 1
              ? '1 entry was added.'
              : `${committed.added} entries were added.`}
          </p>
        )}
        {stage === 'removed' && (
          <p aria-live="polite" className="text-base font-bold">
            {removed === 1 ? '1 entry was removed.' : `${removed} entries were removed.`} The tab is
            as it was before this import.
          </p>
        )}

        <ProblemLine problem={send.problem} />

        {stage === 'preview' && preview && (
          <div className="flex gap-2">
            <button
              type="button"
              className={`${SECONDARY} flex-1`}
              disabled={send.pending}
              onClick={() => void discard()}
            >
              Choose another file
            </button>
            <button
              type="button"
              className={`${PRIMARY} flex-1`}
              disabled={send.pending || preview.rows_new === 0}
              onClick={() => void add()}
            >
              {send.pending
                ? 'Adding'
                : preview.rows_new === 0
                  ? 'Nothing new to add'
                  : `Add ${preview.rows_new} ${preview.rows_new === 1 ? 'entry' : 'entries'}`}
            </button>
          </div>
        )}
        {stage === 'added' && (
          <div className="flex gap-2">
            <button
              type="button"
              className={`${SECONDARY} flex-1`}
              disabled={send.pending}
              onClick={() => void undo()}
            >
              <Undo2 aria-hidden="true" className="size-4" />
              Remove this import
            </button>
            <button type="button" className={`${PRIMARY} flex-1`} onClick={onClose}>
              Done
            </button>
          </div>
        )}
        {stage === 'removed' && (
          <button type="button" className={PRIMARY} onClick={onClose}>
            Done
          </button>
        )}

        <div className="border-t border-[color:var(--divider)] pt-3">
          <button
            type="button"
            className={`${SECONDARY} w-full`}
            disabled={send.pending || !tab}
            onClick={() => void download()}
          >
            <Download aria-hidden="true" className="size-4" />
            Download entries
          </button>
          <p className="mt-1.5 text-xs text-[color:var(--text-tertiary)]">
            All entries of this tab as a CSV file in the generic format.
          </p>
        </div>
      </div>
    </BottomSheet>
  )
}
