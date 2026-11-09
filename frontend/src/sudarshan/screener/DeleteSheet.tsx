import { useQueryClient } from '@tanstack/react-query'
import { Trash2 } from 'lucide-react'
import { useEffect, useId, useState } from 'react'
import { cn } from '@/lib/utils'
import { ACTION, DANGER_FILL, OUTLINED } from '@/sudarshan/books/cards/parts'
import { BottomSheet } from '@/sudarshan/watchlist/BottomSheet'
import { sendChange, useHeld } from './actions'
import type { ScreenRow } from './types'

/** The sentence under the checkbox: 'Also remove the 7 instruments it added to ...'. */
export function alsoRemoveWords(
  row: Pick<ScreenRow, 'added_count' | 'target_name'>
): string | null {
  if (row.added_count <= 0 || !row.target_name) return null
  const things = row.added_count === 1 ? '1 instrument' : `${row.added_count} instruments`
  return `Also remove the ${things} it added to '${row.target_name}'?`
}

/**
 * Deleting a screen asks first (design 4.2): its history stays, and the instruments it added to
 * its list stay unless the owner says otherwise (the default is No). Sent once; the buttons hold
 * from the tap until the answer, and an answer that never came holds them a while longer.
 */
export function DeleteSheet({ row, onClose }: { row: ScreenRow | null; onClose: () => void }) {
  const client = useQueryClient()
  const held = useHeld(row?.id ?? 0)
  const [remove, setRemove] = useState(false)
  const box = useId()
  const rowId = row?.id
  // biome-ignore lint/correctness/useExhaustiveDependencies: a fresh choice for each screen only
  useEffect(() => {
    setRemove(false)
  }, [rowId])
  const also = row ? alsoRemoveWords(row) : null
  const sending = held?.action === 'delete'
  return (
    <BottomSheet
      open={row !== null}
      onOpenChange={(next) => !next && onClose()}
      title="Delete this screen?"
      description={row?.name}
    >
      <div className="flex flex-col gap-4">
        <p className="text-sm font-medium">
          It stops checking at once. Its past runs stay in History.
        </p>
        {also && (
          <label htmlFor={box} className="flex min-h-12 items-start gap-3 text-sm font-semibold">
            <input
              id={box}
              type="checkbox"
              checked={remove}
              disabled={sending}
              onChange={(event) => setRemove(event.target.checked)}
              className="mt-0.5 size-6 shrink-0 accent-[color:var(--brand)]"
            />
            <span>{also}</span>
          </label>
        )}
        <div className="flex gap-2">
          <button type="button" className={cn(OUTLINED, 'flex-1')} onClick={onClose}>
            Keep it
          </button>
          <button
            type="button"
            disabled={sending || held !== undefined}
            className={cn(ACTION, DANGER_FILL, 'flex-1')}
            onClick={() => {
              if (!row) return
              void sendChange(client, row, 'delete', { removeAdded: remove }).then((done) => {
                if (done) onClose()
              })
            }}
          >
            <Trash2 aria-hidden="true" className="size-5" />
            {sending ? 'Deleting' : held ? 'Not confirmed' : 'Delete'}
          </button>
        </div>
      </div>
    </BottomSheet>
  )
}
