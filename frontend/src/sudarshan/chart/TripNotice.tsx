import { Info, X } from 'lucide-react'

/**
 * SUDARSHAN M14.8: one line above the phone chart saying why a trade opened
 * from its sheet is not shown as asked (older than the candles at the chosen
 * interval, or not read). It stays until dismissed; the chart is never covered.
 */
export function TripNotice({ text, onDismiss }: { text: string; onDismiss: () => void }) {
  return (
    <output
      data-testid="trip-notice"
      className="flex shrink-0 items-start gap-2 border-b bg-card pl-3 text-sm"
    >
      <Info aria-hidden="true" className="mt-4 size-4 shrink-0 text-[color:var(--info)]" />
      <span className="min-w-0 flex-1 py-3 text-[color:var(--text-primary)]">{text}</span>
      <button
        type="button"
        onClick={onDismiss}
        aria-label="Dismiss this note"
        className="inline-flex size-12 shrink-0 items-center justify-center rounded-full text-[color:var(--text-secondary)] hover:bg-accent focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
      >
        <X aria-hidden="true" className="size-4" />
      </button>
    </output>
  )
}
