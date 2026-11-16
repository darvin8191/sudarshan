/**
 * What the chart's sync with the server has to say (M4.18), in a strip above the chart: which copy
 * of a document was kept after a conflict, with the way to put this phone's back, or why a
 * document stays on this phone only. Each kind of sentence has its own mark beside its words, so
 * none is told by colour alone; every control is 48 px or more.
 */
import { AlertTriangle, CheckCircle2, Info, X } from 'lucide-react'
import { useSyncExternalStore } from 'react'
import { chartSyncNotices, type NoticeBoard, type NoticeTone } from './docSync'

const MARK: Record<NoticeTone, { Icon: typeof Info; ink: string; label: string }> = {
  conflict: {
    Icon: AlertTriangle,
    ink: 'text-[color:var(--warning-text)]',
    label: 'Changed elsewhere',
  },
  kept: { Icon: CheckCircle2, ink: 'text-[color:var(--up)]', label: 'Saved' },
  local: { Icon: Info, ink: 'text-[color:var(--text-secondary)]', label: 'On this phone only' },
}

export function SyncNotice({ board = chartSyncNotices }: { board?: NoticeBoard }) {
  const notice = useSyncExternalStore(board.subscribe, board.latest, board.latest)
  if (!notice) return null
  const { Icon, ink, label } = MARK[notice.tone]
  // Upright: the words with Dismiss beside them, the action under them. On a wide screen (a phone
  // on its side) one row: words, action, Dismiss, so the chart keeps its height.
  return (
    <output
      aria-live="polite"
      data-testid="chart-sync-notice"
      className="flex shrink-0 flex-wrap items-start border-b bg-card pl-3 text-sm text-[color:var(--text-primary)]"
    >
      <Icon aria-hidden="true" className={`mt-4 size-4 shrink-0 ${ink}`} />
      <span className="min-w-0 flex-1 basis-0 px-2 py-3">
        <span className="sr-only">{label}: </span>
        {notice.text}
      </span>
      <button
        type="button"
        aria-label="Dismiss this note"
        onClick={() => board.clear(notice.id)}
        className="inline-flex size-12 shrink-0 items-center justify-center rounded-full text-[color:var(--text-secondary)] hover:bg-accent focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring sm:order-2"
      >
        <X aria-hidden="true" className="size-4" />
      </button>
      {notice.action && (
        <span className="flex w-full justify-end pr-1 pb-1 sm:order-1 sm:w-auto sm:pb-0">
          <button
            type="button"
            onClick={notice.action.run}
            className="inline-flex min-h-12 items-center rounded-full px-4 font-semibold text-primary hover:bg-accent focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
          >
            {notice.action.label}
          </button>
        </span>
      )}
    </output>
  )
}
