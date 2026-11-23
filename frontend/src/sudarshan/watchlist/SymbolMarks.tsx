import { useQueryClient } from '@tanstack/react-query'
import { Clock } from 'lucide-react'
import { useEffect, useState } from 'react'
import { showToast } from '@/utils/toast'
import { FlagPicker } from './FlagPicker'
import { NoteField } from './NoteField'
import { dropNoteDraft, keepNoteDraft, noteDraft } from './noteDrafts'
import { callOp } from './v2/api'
import { checkNote, inLists, PRIVATE_WORDS, readAgain, useSymbolInfo } from './v2/marks'
import type { FlagName, MarkReq } from './v2/types'

/** Said once a note typed in the sheet was saved after the sheet closed (M8.6, GA10.8). */
export const NOTE_SAVED = 'Note saved'
/** Said when it could not be: the reason follows, then what to do (v0.16.0 phone review L2). */
export const NOTE_NOT_SAVED = 'Note not saved:'
/** What to do then: the words are kept for the symbol's sheet (L2). */
export const NOTE_KEPT = 'Open the symbol to save it again: what you typed is kept there.'
const NOTE_TOO_LONG_LEFT =
  'It is over 500 characters. Open the symbol to shorten it: what you typed is kept there.'

/**
 * The symbol sheet's flag, note and "In lists" (M8.6 F8). Every write names the symbol's version
 * it was decided on; a refusal (a `conflict` among them) shows the server's sentence and reads the
 * symbol again, so the screen shows what the other screen did. Not drawn in a family render.
 */
export function SymbolMarks({ k, names }: { k: string; names: readonly FlagName[] }) {
  const client = useQueryClient()
  const info = useSymbolInfo(k)
  const [problem, setProblem] = useState<string | null>(null)
  // Review L7: one flag write at a time; a second tap meanwhile would be decided on the old
  // version and come back as "Changed on another screen" about his own tap.
  const [flagging, setFlagging] = useState(false)
  const data = info.data

  const send = async (change: Pick<MarkReq, 'flag' | 'note'>): Promise<string | null> => {
    let said: string | null = null
    try {
      await callOp('mark', { k, ...change, version: data?.version ?? null })
    } catch (error) {
      said = error instanceof Error ? error.message : 'The change could not be saved.'
    }
    // Read again either way: after a refusal the symbol is the other screen's.
    await readAgain(client, k)
    return said
  }
  const write = (change: Pick<MarkReq, 'flag' | 'note'>): Promise<string | null> => {
    setProblem(null)
    return send(change)
  }
  // GA10.8: the sheet closed with a note typed and not saved. It is saved now, the owner's
  // default, and said in one quiet line once it is. L2: a note the server does not take after
  // the sheet has gone, whether saved on closing or by a Save still on its way when Back came,
  // is kept for the symbol's sheet, and the line says why and what to do.
  const answeredAway = (note: string, said: string | null) => {
    if (said === null) {
      dropNoteDraft(k)
      showToast.info(NOTE_SAVED, undefined, { duration: 2_000 })
      return
    }
    keepNoteDraft(k, note)
    showToast.error(`${NOTE_NOT_SAVED} ${said} ${NOTE_KEPT}`)
  }
  const leave = (note: string) => {
    if (checkNote(note)) {
      keepNoteDraft(k, note)
      showToast.error(`${NOTE_NOT_SAVED} ${NOTE_TOO_LONG_LEFT}`)
      return
    }
    void send({ note }).then((said) => answeredAway(note, said))
  }
  // A note kept from before is in the field from its first frame; the store lets it go then.
  const draft = noteDraft(k)
  const shown = !info.isPending && !info.isError && data !== undefined
  useEffect(() => {
    if (shown) dropNoteDraft(k)
  }, [shown, k])

  if (info.isPending) {
    return <p className="text-sm font-medium text-muted-foreground">Reading flag and note.</p>
  }
  if (info.isError || !data) {
    return (
      <p role="alert" className="text-sm font-semibold">
        {info.error?.message ?? 'The flag and note could not be read.'}
      </p>
    )
  }
  return (
    <section aria-label="Flag and note" className="flex flex-col gap-3">
      {data.expired && (
        <p
          data-expired="true"
          className="flex items-center gap-1.5 text-[0.8125rem] font-semibold text-[color:var(--warning-text)]"
        >
          <Clock aria-hidden="true" className="size-4" />
          Expired
          <span className="font-medium text-[color:var(--text-secondary)]">
            This contract is no longer listed. Its flag and note stay until you clear them.
          </span>
        </p>
      )}
      <div className="flex flex-col gap-1">
        <h3 className="text-sm font-bold">Flag</h3>
        <FlagPicker
          value={data.flag}
          names={names}
          disabled={flagging}
          onChange={(flag) => {
            if (flagging) return
            setFlagging(true)
            void write({ flag })
              .then(setProblem)
              .finally(() => setFlagging(false))
          }}
        />
      </div>
      {problem && (
        <p role="alert" className="text-[0.8125rem] font-semibold text-[color:var(--down)]">
          {problem}
        </p>
      )}
      <NoteField
        saved={data.note ?? ''}
        draft={draft}
        onSave={(note) => write({ note: note || null })}
        onLeave={leave}
        onAnsweredAway={answeredAway}
      />
      <p className="text-xs font-medium text-[color:var(--text-tertiary)]">{PRIVATE_WORDS}</p>
      <p className="text-[0.8125rem] font-medium">
        <span className="font-bold">In lists: </span>
        {inLists(data)}
      </p>
    </section>
  )
}
