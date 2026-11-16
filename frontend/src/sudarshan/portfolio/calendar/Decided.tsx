// SUDARSHAN P7 F4 (M7.36; v0.14.0 verification M3, N1): what the owner confirmed for a bonus or a
// split of the Upstox account, and "Change what I confirmed": To confirm's own choices, sent from
// the decision's version. Shown under Confirmed in the Calendar for every one, whatever its day,
// and on a Check row that asks him to look at a reading again. What opens takes no tap for its
// first half second (`watchlist/openGuard.ts`), so the second tap of a double tap never presses a
// choice that appeared under the finger.
import { PencilLine } from 'lucide-react'
import { useState } from 'react'
import { useOpenGuard } from '@/sudarshan/watchlist/openGuard'
import { SECONDARY } from '../sheets/TabFields'
import type { CaDecided, CaKind } from '../types'
import { Choices } from './ToConfirm'

export function Decided({
  kind,
  decided,
  onDone,
  words = true,
}: {
  kind: CaKind
  decided: CaDecided
  /** After a change is sent: read the list (and whatever rests on the lots) again. */
  onDone: () => void
  /** Whether to say what was confirmed above the button; a Check row has said it already. */
  words?: boolean
}) {
  const [open, setOpen] = useState(false)
  const guard = useOpenGuard(open)
  const { onPointerDownCapture, onClickCapture, onKeyDownCapture } = guard.handlers
  return (
    <div className="flex flex-col gap-2 pt-1.5">
      {words && <p className="text-sm">{decided.words}</p>}
      {open ? (
        <div
          className="flex flex-col gap-2"
          onPointerDownCapture={onPointerDownCapture}
          onClickCapture={onClickCapture}
          onKeyDownCapture={onKeyDownCapture}
          data-settling={guard.settling || undefined}
        >
          <ul className="flex flex-col">
            <Choices
              kind={kind}
              eventId={decided.event_id}
              expected={decided.expected}
              tab={undefined}
              version={decided.version}
              current={decided}
              onDone={() => {
                setOpen(false)
                onDone()
              }}
            />
          </ul>
          <button type="button" className={SECONDARY} onClick={() => setOpen(false)}>
            Keep it as it is
          </button>
        </div>
      ) : (
        <button type="button" className={SECONDARY} onClick={() => setOpen(true)}>
          <PencilLine aria-hidden="true" className="size-4" />
          Change what I confirmed
        </button>
      )}
    </div>
  )
}
