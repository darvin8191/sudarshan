import { useQueryClient } from '@tanstack/react-query'
import { Clock, Pencil } from 'lucide-react'
import { useState } from 'react'
import { MarkGlyph } from './marks'
import { useOpenGuard } from './openGuard'
import { callOp } from './v2/api'
import { FLAG_IDS, FLAG_LIST_WORDS, flagName, PRIVATE_WORDS, readAgain } from './v2/marks'
import type { FlagName } from './v2/types'
import { LIMITS } from './v2/types'

const BUTTON =
  'flex min-h-12 items-center gap-2 rounded-xl px-3 text-[0.9375rem] font-semibold text-primary outline-none hover:bg-accent focus-visible:ring-2 focus-visible:ring-ring disabled:opacity-50'

/** Review L8: notes are his words, so Clear expired asks first. */
export const CLEAR_EXPIRED_ASK =
  'Clear the flags and notes of contracts that are no longer listed? Your notes on them go too, and cannot be brought back.'

/**
 * The Flags block's own controls in All lists (M8.6 F8): what a flag list is, Rename flags (five
 * fields of up to 24 letters, saved with `flags`) and Clear expired (only the contracts no longer
 * listed). Each write names the flags version it was decided on; a refusal shows the server's
 * sentence and reads again. The rows of the block are the sheet's own.
 */
export function FlagTools({ names, version }: { names: readonly FlagName[]; version: number }) {
  const client = useQueryClient()
  const [renaming, setRenaming] = useState(false)
  const [draft, setDraft] = useState<string[]>([])
  const [busy, setBusy] = useState(false)
  const [said, setSaid] = useState<{ text: string; bad: boolean } | null>(null)
  const [asking, setAsking] = useState(false)
  // The question takes the button's place under the finger: half a second before it takes a tap.
  const guard = useOpenGuard(asking)

  const run = async (act: () => Promise<string | null>) => {
    setBusy(true)
    setSaid(null)
    try {
      const text = await act()
      if (text) setSaid({ text, bad: false })
      return true
    } catch (error) {
      setSaid({
        text: error instanceof Error ? error.message : 'The change could not be saved.',
        bad: true,
      })
      return false
    } finally {
      // Read again either way: after a refusal the flags are the other screen's.
      await readAgain(client)
      setBusy(false)
    }
  }

  const save = async () => {
    const ok = await run(async () => {
      await callOp('flags', {
        names: FLAG_IDS.map((mark, index) => ({ mark, name: draft[index] ?? '' })),
        version,
      })
      return null
    })
    if (ok) setRenaming(false)
  }

  return (
    <li className="flex flex-col gap-1 px-3 py-2">
      <p className="text-[0.8125rem] font-medium text-[color:var(--text-secondary)]">
        {FLAG_LIST_WORDS}
      </p>
      <p className="text-xs font-medium text-[color:var(--text-tertiary)]">{PRIVATE_WORDS}</p>
      {renaming ? (
        <form
          aria-label="Rename flags"
          className="flex flex-col gap-2 pt-1"
          onSubmit={(event) => {
            event.preventDefault()
            void save()
          }}
        >
          {FLAG_IDS.map((mark, index) => (
            <label key={mark} className="flex min-h-12 items-center gap-2 text-sm font-semibold">
              <MarkGlyph mark={mark} />
              <span className="sr-only">Flag {mark} name</span>
              <input
                value={draft[index] ?? ''}
                maxLength={LIMITS.flag_name}
                aria-label={`Name of flag ${mark}`}
                onChange={(event) =>
                  setDraft((now) =>
                    now.map((name, at) => (at === index ? event.target.value : name))
                  )
                }
                className="min-h-12 min-w-0 flex-1 rounded-[0.875rem] border border-input bg-card px-3 text-[0.9375rem] font-medium outline-none focus-visible:ring-2 focus-visible:ring-ring"
              />
            </label>
          ))}
          <div className="flex gap-2">
            <button
              type="submit"
              disabled={busy}
              className={`${BUTTON} bg-primary text-primary-foreground hover:bg-primary/90`}
            >
              Save names
            </button>
            <button
              type="button"
              disabled={busy}
              onClick={() => setRenaming(false)}
              className={BUTTON}
            >
              Cancel
            </button>
          </div>
        </form>
      ) : (
        <div className="flex flex-wrap gap-2">
          <button
            type="button"
            disabled={busy}
            onClick={() => {
              setDraft(FLAG_IDS.map((mark) => flagName(names, mark)))
              setRenaming(true)
              setSaid(null)
            }}
            className={BUTTON}
          >
            <Pencil aria-hidden="true" className="size-5" />
            Rename flags
          </button>
          <button
            type="button"
            disabled={busy}
            onClick={() => {
              setSaid(null)
              setAsking(true)
            }}
            className={BUTTON}
          >
            <Clock aria-hidden="true" className="size-5" />
            Clear expired
          </button>
        </div>
      )}
      {asking && !renaming && (
        <fieldset
          aria-label="Clear expired"
          onPointerDownCapture={guard.handlers.onPointerDownCapture}
          onClickCapture={guard.handlers.onClickCapture}
          onKeyDownCapture={guard.handlers.onKeyDownCapture}
          data-settling={guard.settling || undefined}
          className="m-0 flex min-w-0 flex-col gap-2 border-0 p-0 pt-1"
        >
          <p className="text-sm font-medium">{CLEAR_EXPIRED_ASK}</p>
          <div className="flex flex-wrap gap-2">
            <button
              type="button"
              disabled={busy}
              onClick={() =>
                void run(async () => (await callOp('expired_clear', { version })).words).then(() =>
                  setAsking(false)
                )
              }
              className={`${BUTTON} text-[color:var(--down)]`}
            >
              Clear them
            </button>
            <button
              type="button"
              disabled={busy}
              onClick={() => setAsking(false)}
              className={BUTTON}
            >
              Keep them
            </button>
          </div>
        </fieldset>
      )}
      {said && (
        <p
          role={said.bad ? 'alert' : 'status'}
          className={
            said.bad ? 'text-sm font-semibold text-[color:var(--down)]' : 'text-sm font-medium'
          }
        >
          {said.text}
        </p>
      )}
    </li>
  )
}
