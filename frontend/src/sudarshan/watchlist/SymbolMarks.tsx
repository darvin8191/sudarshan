import { useQueryClient } from '@tanstack/react-query'
import { Clock } from 'lucide-react'
import { useState } from 'react'
import { FlagPicker } from './FlagPicker'
import { NoteField } from './NoteField'
import { callOp } from './v2/api'
import { inLists, PRIVATE_WORDS, readAgain, useSymbolInfo } from './v2/marks'
import type { FlagName, MarkReq } from './v2/types'

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

  const write = async (change: Pick<MarkReq, 'flag' | 'note'>): Promise<string | null> => {
    setProblem(null)
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
      <NoteField saved={data.note ?? ''} onSave={(note) => write({ note: note || null })} />
      <p className="text-xs font-medium text-[color:var(--text-tertiary)]">{PRIVATE_WORDS}</p>
      <p className="text-[0.8125rem] font-medium">
        <span className="font-bold">In lists: </span>
        {inLists(data)}
      </p>
    </section>
  )
}
