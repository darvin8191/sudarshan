import { Download } from 'lucide-react'
import { useEffect, useState } from 'react'
import { cn } from '@/lib/utils'
import { BottomSheet } from './BottomSheet'
import { downloadText, exportTv } from './tvTransfer'
import { problemOf } from './v2/api'
import type { TvExport } from './v2/types'

const BUTTON =
  'inline-flex min-h-12 items-center justify-center gap-2 rounded-[0.875rem] px-5 text-[0.9375rem] font-semibold outline-none focus-visible:ring-[3px] focus-visible:ring-ring/50 disabled:opacity-50'

/**
 * M8.12 (F15): Export for TradingView (`?sheet=export`). Reads `tv_export` for the list on screen
 * each time it opens (nothing is written), shows the server's words as sent, and offers the file
 * as the server named it. With no symbols there is no download.
 */
export function ExportSheet({
  open,
  onClose,
  listRef,
}: {
  open: boolean
  onClose: () => void
  /** The ref of the list on screen (`list:7`, `flag:2`, `preset:..`, `ranked:..`; a section's is `list:7/section:3`). */
  listRef: string | null
}) {
  const [out, setOut] = useState<TvExport | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    setOut(null)
    setError(null)
    if (!open || listRef === null) return
    const stop = new AbortController()
    exportTv(listRef, stop.signal).then(
      (got) => setOut(got),
      (failure) => {
        if (!stop.signal.aborted) setError(problemOf(failure).message)
      }
    )
    return () => stop.abort()
  }, [open, listRef])

  return (
    <BottomSheet
      open={open}
      onOpenChange={(next) => !next && onClose()}
      title="Export for TradingView"
      description={out?.filename}
    >
      <div className="flex flex-col gap-3">
        {!out && !error && <p className="text-sm text-muted-foreground">Reading the list</p>}
        {out && (
          <ul aria-label="Export" className="flex flex-col gap-2 text-sm leading-snug">
            {out.words.map((line, index) => (
              <li key={`${index}-${line}`}>{line}</li>
            ))}
          </ul>
        )}
        {error && (
          <p role="alert" className="text-sm font-medium text-[color:var(--down)]">
            {error}
          </p>
        )}
        {out && out.symbols > 0 && (
          <button
            type="button"
            className={cn(BUTTON, 'bg-primary text-primary-foreground hover:bg-primary/90')}
            onClick={() => downloadText(out.text, out.filename)}
          >
            <Download aria-hidden="true" className="size-5" />
            Download {out.filename}
          </button>
        )}
      </div>
    </BottomSheet>
  )
}
