import { Check } from 'lucide-react'
import { useEffect, useId, useRef, useState } from 'react'
import { cn } from '@/lib/utils'
import { checkNote } from './v2/marks'
import { LIMITS } from './v2/types'

/**
 * What a write of the note came back with, when it was not kept: the sentence to show, and
 * whether no answer came at all (a timeout, no network), so it may have been saved after all
 * (v0.17.0 verification V4).
 */
export interface NoteProblem {
  message: string
  unanswered: boolean
}

/**
 * Note (M8.6 F8): a text field with "123 of 500", Save and a "Saved" state. A note over the limit
 * is refused before anything is sent, in the server's words. `onSave` answers null when the note
 * was kept, or the problem to show. A note typed and not saved when the field goes (the sheet
 * closed) is handed to `onLeave`, the owner's default (GA10.8); an empty or unchanged one is not,
 * nor one already being saved: that one's answer goes to `onAnsweredAway` when it comes, since
 * the field is no longer there to show it (v0.16.0 phone review L2).
 */
export function NoteField({
  saved,
  draft,
  disabled,
  onSave,
  onLeave,
  onAnsweredAway,
}: {
  /** The note as the server holds it. */
  saved: string
  /** A note typed before and not taken, shown in place of the saved one, ready to save (L2). */
  draft?: string
  disabled?: boolean
  onSave: (note: string) => Promise<NoteProblem | null>
  onLeave?: (note: string) => void
  /** A Save on its way when the field went has come back: null when kept, else the problem. */
  onAnsweredAway?: (note: string, problem: NoteProblem | null) => void
}) {
  const id = useId()
  const [text, setText] = useState(draft ?? saved)
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

  // What the field holds as it goes: read once, when the sheet closes.
  const leaving = useRef({ text, saved, busy, disabled, onLeave })
  useEffect(() => {
    leaving.current = { text, saved, busy, disabled, onLeave }
  })
  // Whether the field is still on screen when a Save comes back (L2).
  const shown = useRef(true)
  // The leave is acted on a moment later, and dropped if the field comes straight back: React's
  // development check mounts it twice, and a kept note is unsaved from the first frame.
  const leave = useRef<ReturnType<typeof setTimeout> | null>(null)
  useEffect(() => {
    shown.current = true
    if (leave.current !== null) clearTimeout(leave.current)
    leave.current = null
    return () => {
      shown.current = false
      const last = leaving.current
      const note = last.text.trim()
      const handOver = last.onLeave
      if (!handOver || last.busy || last.disabled) return
      if (note === '' || note === last.saved.trim()) return
      leave.current = setTimeout(() => handOver(note), 0)
    }
  }, [])

  const save = async () => {
    const refused = checkNote(text)
    setDone(false)
    if (refused) {
      setProblem(refused)
      return
    }
    const note = text.trim()
    setBusy(true)
    setProblem(null)
    const answer = await onSave(note)
    // L2: the field went while this was on its way (Back). A refusal shown here would be shown
    // to nobody and the words lost, so the answer goes where the owner is.
    if (!shown.current) {
      onAnsweredAway?.(note, answer)
      return
    }
    setBusy(false)
    if (answer) setProblem(answer.message)
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
