import { Check } from 'lucide-react'
import { useId, useState } from 'react'
import { cn } from '@/lib/utils'
import { checkNote } from './v2/marks'
import { LIMITS } from './v2/types'

/**
 * Note (M8.6 F8): a text field with "123 of 500", Save and a "Saved" state. A note over the limit
 * is refused before anything is sent, in the server's words. `onSave` answers null when the note
 * was kept, or the sentence to show.
 */
export function NoteField({
  saved,
  disabled,
  onSave,
}: {
  /** The note as the server holds it. */
  saved: string
  disabled?: boolean
  onSave: (note: string) => Promise<string | null>
}) {
  const id = useId()
  const [text, setText] = useState(saved)
  const [held, setHeld] = useState(saved)
  const [busy, setBusy] = useState(false)
  const [problem, setProblem] = useState<string | null>(null)
  const [done, setDone] = useState(false)
  // A note read again (another screen changed it) replaces what was held, unless he is typing.
  if (saved !== held) {
    setHeld(saved)
    if (text === held) setText(saved)
  }
  const dirty = text.trim() !== saved.trim()
  const length = text.length

  const save = async () => {
    const refused = checkNote(text)
    setDone(false)
    if (refused) {
      setProblem(refused)
      return
    }
    setBusy(true)
    setProblem(null)
    const answer = await onSave(text.trim())
    setBusy(false)
    if (answer) setProblem(answer)
    else setDone(true)
  }

  return (
    <div className="flex flex-col gap-1">
      <label htmlFor={id} className="text-sm font-bold">
        Note
      </label>
      <textarea
        id={id}
        value={text}
        rows={3}
        disabled={disabled}
        aria-invalid={problem !== null || undefined}
        onChange={(event) => {
          setText(event.target.value)
          setDone(false)
          setProblem(null)
        }}
        className="min-h-24 w-full resize-y rounded-[0.875rem] border border-input bg-card px-3 py-2 text-[0.9375rem] outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:opacity-50"
      />
      <div className="flex min-h-12 items-center justify-between gap-2">
        <span
          className={cn(
            'text-xs font-semibold',
            length > LIMITS.note ? 'text-[color:var(--down)]' : 'text-[color:var(--text-tertiary)]'
          )}
        >
          {length} of {LIMITS.note}
        </span>
        <span className="flex items-center gap-2">
          {done && !dirty && (
            <output className="flex items-center gap-1 text-xs font-semibold text-[color:var(--up)]">
              <Check aria-hidden="true" className="size-4" />
              Saved
            </output>
          )}
          <button
            type="button"
            disabled={disabled || busy || !dirty}
            onClick={() => void save()}
            className="min-h-12 rounded-[0.875rem] bg-primary px-5 text-[0.9375rem] font-semibold text-primary-foreground outline-none hover:bg-primary/90 focus-visible:ring-[3px] focus-visible:ring-ring/50 disabled:opacity-50"
          >
            Save
          </button>
        </span>
      </div>
      {problem && (
        <p role="alert" className="text-[0.8125rem] font-semibold text-[color:var(--down)]">
          {problem}
        </p>
      )}
    </div>
  )
}
