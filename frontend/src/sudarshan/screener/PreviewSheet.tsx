import { Clock, LoaderCircle, TriangleAlert } from 'lucide-react'
import { BottomSheet } from '@/sudarshan/watchlist/BottomSheet'
import type { Preview } from './types'
import type { CheckState } from './usePreviewCheck'

/** The count and the first matches of a preview, with what could not be checked and why. */
export function PreviewBody({ preview }: { preview: Preview }) {
  return (
    <div className="flex flex-col gap-3">
      <p className="flex items-start gap-1.5 text-[0.8125rem] font-semibold text-[color:var(--text-secondary)]">
        <Clock aria-hidden="true" className="mt-0.5 size-4 shrink-0" />
        {preview.label}
      </p>
      <p className="text-base font-bold">
        {preview.count === 1 ? '1 match' : `${preview.count} match`}
      </p>
      {preview.first.length > 0 && (
        <ul className="flex flex-col divide-y rounded-xl border">
          {preview.first.map((row) => (
            <li
              key={`${row.exchange}:${row.symbol}`}
              className="flex min-h-12 items-center justify-between gap-2 px-3 py-1.5"
            >
              <span className="min-w-0">
                <span className="font-bold">{row.symbol}</span>{' '}
                <span className="text-[0.75rem] font-medium text-[color:var(--text-tertiary)]">
                  {row.exchange}
                </span>
              </span>
              <span className="text-[0.8125rem] font-semibold text-[color:var(--text-secondary)]">
                {row.values}
                {row.forming && ' · forming'}
              </span>
            </li>
          ))}
        </ul>
      )}
      {preview.not_checked.count > 0 && (
        <div className="flex flex-col gap-1 rounded-xl bg-[color:var(--gold-tint)] px-3 py-2">
          <p className="text-sm font-bold text-[color:var(--warning-text)]">
            Not checked ({preview.not_checked.count})
          </p>
          <ul className="flex flex-col gap-0.5 text-[0.8125rem] font-medium">
            {preview.not_checked.reasons.map((reason) => (
              <li key={reason}>{reason}</li>
            ))}
          </ul>
        </div>
      )}
    </div>
  )
}

/** "Test on the last candle": what the draft would find on the last closed candle, not saved. */
export function PreviewSheet({
  open,
  state,
  onClose,
}: {
  open: boolean
  state: CheckState
  onClose: () => void
}) {
  return (
    <BottomSheet
      open={open}
      onOpenChange={(next) => !next && onClose()}
      title="Test on the last candle"
    >
      {state.status === 'ok' ? (
        <PreviewBody preview={state.preview} />
      ) : state.status === 'problem' ? (
        <p role="alert" className="flex items-start gap-1.5 text-sm font-semibold text-destructive">
          <TriangleAlert aria-hidden="true" className="mt-0.5 size-4 shrink-0" />
          {state.problem.message}
        </p>
      ) : (
        <p className="flex items-center gap-2 text-sm font-semibold">
          <LoaderCircle aria-hidden="true" className="size-4 animate-spin" />
          Checking the last candle
        </p>
      )}
    </BottomSheet>
  )
}
