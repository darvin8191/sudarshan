import { useQueryClient } from '@tanstack/react-query'
import { ChevronLeft, Plus } from 'lucide-react'
import { useState } from 'react'
import { Input } from '@/components/ui/input'
import { cn } from '@/lib/utils'
import { toast } from '@/utils/toast'
import { BottomSheet } from './BottomSheet'
import { ownId } from './ListStrip'
import { callOp } from './v2/api'
import {
  arrangeKeys,
  copiedWords,
  LISTS_FULL,
  NAME_LONG,
  NOTHING,
  roomWords,
  writeThenRead,
} from './v2/arrange'
import { type CopyReq, type Entry, LIMITS, type View } from './v2/types'
import { LISTS_KEY, useLists, useListView, viewKey } from './v2/useListView'

const ROW =
  'flex min-h-12 w-full items-center gap-3 rounded-xl px-3 text-left outline-none hover:bg-accent focus-visible:ring-2 focus-visible:ring-ring disabled:opacity-50'
const BUTTON =
  'inline-flex min-h-12 flex-1 items-center justify-center rounded-[0.875rem] px-5 text-[0.9375rem] font-semibold outline-none focus-visible:ring-[3px] focus-visible:ring-ring/50 disabled:opacity-50'

/** "Swing · 24 of 250": a list and how full it is. */
const fullness = (entry: Pick<Entry, 'name' | 'count'>) =>
  `${entry.name} · ${entry.count ?? 0} of ${LIMITS.list_items}`

/**
 * Where symbols go (M8.4 F5): one list of the owner's own lists with how full each is, and New list.
 * Picking one asks for the last details (a section of the target, or the new list's name) and sends
 * one `copy` (`move` for a move). The 250-symbol room is checked here before sending, in the
 * server's own sentence; the target's version is sent when a section of it is chosen. The lists
 * are read again whatever the answer.
 */
export function ListPicker({
  view,
  keys,
  sectionId,
  label,
  move,
  onClose,
}: {
  view: View
  /** The picked symbols as EXCHANGE:SYMBOL (one symbol, or a section's). */
  keys: string[]
  /** Set when a whole section is picked. */
  sectionId: number | null
  label: string
  move: boolean
  onClose: () => void
}) {
  const client = useQueryClient()
  const lists = useLists(true)
  const [target, setTarget] = useState<Entry | 'new' | null>(null)
  const [name, setName] = useState('')
  const [section, setSection] = useState<number | null>(null)
  const [busy, setBusy] = useState(false)
  const [problem, setProblem] = useState<string | null>(null)
  const targetView = useListView(target && target !== 'new' ? target.ref : null).data

  const own = (lists.data?.own ?? []).filter((entry) => entry.ref !== view.ref)
  const verb = move ? 'Move' : 'Copy'

  // The room the target has for what is new to it: from its own rows when read, else its count.
  let refusal: string | null = keys.length === 0 ? NOTHING : null
  if (!refusal && target && target !== 'new') {
    const held = targetView
      ? new Set(targetView.rows.filter((row) => row.item !== null).map((row) => row.contract.key))
      : null
    const fresh = held ? keys.filter((key) => !held.has(key)).length : keys.length
    const left = LIMITS.list_items - (held ? held.size : (target.count ?? 0))
    if (fresh > left) refusal = roomWords(target.name, left, keys.length, sectionId !== null)
  }
  const newName = name.trim()
  const tooLong = [...newName].length > 64

  const send = async () => {
    if (!target || busy || refusal) return
    if (target === 'new' && (!newName || tooLong)) return
    const ref = view.ref
    const to =
      target === 'new'
        ? { name: newName }
        : {
            list: ownId(target.ref) as number,
            ...(section !== null ? { section_id: section } : {}),
          }
    const req: CopyReq = {
      from: sectionId !== null ? { ref, section_id: sectionId } : { ref, keys },
      to,
      ...(move ? { move: true } : {}),
      // The target's version is what a section of it is set on.
      ...(target !== 'new' && section !== null && targetView
        ? { version: targetView.version }
        : {}),
    }
    setBusy(true)
    setProblem(null)
    const read = [...arrangeKeys(ref), LISTS_KEY]
    if (target !== 'new') read.push(viewKey(target.ref))
    const out = await writeThenRead(client, read, () => callOp('copy', req))
    setBusy(false)
    if (out.done) {
      const to = target === 'new' ? newName : target.name
      toast.success(out.done.words ?? copiedWords(move, out.done.added, out.done.skipped, to))
      onClose()
    } else setProblem(out.said)
  }

  return (
    <BottomSheet
      open
      onOpenChange={(next) => !next && onClose()}
      title={`${verb} to another list`}
      description={label}
    >
      {target === null ? (
        <ul className="flex flex-col">
          {lists.isPending && (
            <li className="px-3 py-2 text-sm font-medium">Reading your lists.</li>
          )}
          {lists.isError && (
            <li role="alert" className="px-3 py-2 text-sm font-medium text-[color:var(--down)]">
              {lists.error.message}
            </li>
          )}
          {own.map((entry) => (
            <li key={entry.ref}>
              <button type="button" className={ROW} onClick={() => setTarget(entry)}>
                <span className="text-[0.9375rem] font-semibold">{fullness(entry)}</span>
              </button>
            </li>
          ))}
          {lists.data && (
            <li>
              <button
                type="button"
                className={ROW}
                disabled={lists.data.own.length >= LIMITS.lists}
                onClick={() => setTarget('new')}
              >
                <Plus aria-hidden="true" className="size-5" />
                <span className="text-[0.9375rem] font-semibold">New list</span>
              </button>
              {lists.data.own.length >= LIMITS.lists && (
                <p className="px-3 pb-2 text-xs font-medium text-muted-foreground">{LISTS_FULL}</p>
              )}
            </li>
          )}
        </ul>
      ) : (
        <div className="flex flex-col gap-3">
          <button
            type="button"
            className={cn(ROW, 'w-auto self-start text-sm font-semibold')}
            onClick={() => {
              setTarget(null)
              setSection(null)
              setProblem(null)
            }}
          >
            <ChevronLeft aria-hidden="true" className="size-5" />
            Pick another list
          </button>
          {target === 'new' ? (
            <Input
              aria-label="List name"
              value={name}
              placeholder="Name of the new list"
              onChange={(event) => setName(event.target.value)}
              className="h-12 text-base"
            />
          ) : (
            <>
              <p className="px-1 text-[0.9375rem] font-bold">{fullness(target)}</p>
              {targetView && targetView.sections.length > 0 && (
                <fieldset className="m-0 flex flex-col border-0 p-0">
                  <legend className="px-3 pb-1 text-[0.8125rem] font-bold text-[color:var(--text-secondary)]">
                    Section in {target.name}
                  </legend>
                  {[{ id: null, name: 'No section' }, ...targetView.sections].map((option) => (
                    <button
                      key={option.id ?? 'none'}
                      type="button"
                      aria-pressed={option.id === section}
                      className={cn(
                        ROW,
                        'text-[0.9375rem] font-semibold',
                        option.id === section && 'bg-[color:var(--primary-tint)] font-bold'
                      )}
                      onClick={() => setSection(option.id)}
                    >
                      {option.name}
                    </button>
                  ))}
                </fieldset>
              )}
            </>
          )}
          {(refusal || tooLong || problem) && (
            <p role="alert" className="px-1 text-sm font-medium text-[color:var(--down)]">
              {refusal ?? (tooLong ? NAME_LONG : problem)}
            </p>
          )}
          <div className="flex gap-2">
            <button
              type="button"
              onClick={onClose}
              className={cn(BUTTON, 'border border-input bg-card hover:bg-accent')}
            >
              Cancel
            </button>
            <button
              type="button"
              disabled={busy || !!refusal || (target === 'new' && (!newName || tooLong))}
              onClick={() => void send()}
              className={cn(BUTTON, 'bg-primary text-primary-foreground hover:bg-primary/90')}
            >
              {busy ? 'Saving' : `${verb} ${keys.length}`}
            </button>
          </div>
        </div>
      )}
    </BottomSheet>
  )
}
