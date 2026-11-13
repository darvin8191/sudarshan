import { useQueryClient } from '@tanstack/react-query'
import { ArrowDown, ArrowUp, Check, FolderInput, Trash2 } from 'lucide-react'
import { useCallback, useEffect, useMemo, useState } from 'react'
import type { WatchlistItem } from '@/api/watchlist'
import { cn } from '@/lib/utils'
import { BottomSheet } from './BottomSheet'
import { ListPicker } from './ListPicker'
import { describe } from './rows'
import type { Overlay } from './useOverlay'
import { callOp } from './v2/api'
import {
  arrangeKeys,
  arrangeReq,
  type Draft,
  draftOf,
  fitTo,
  inGroup,
  moveRow,
  moveSection,
  sameDraft,
  sendRow,
  writeThenRead,
} from './v2/arrange'
import type { View } from './v2/types'
import { viewKey } from './v2/useListView'

const ICON_BUTTON =
  'flex size-12 shrink-0 items-center justify-center rounded-xl text-[color:var(--text-secondary)] outline-none hover:bg-accent active:bg-accent focus-visible:ring-2 focus-visible:ring-ring disabled:opacity-35'
const BUTTON =
  'inline-flex min-h-12 items-center justify-center gap-2 rounded-[0.875rem] px-5 text-[0.9375rem] font-semibold outline-none focus-visible:ring-[3px] focus-visible:ring-ring/50 disabled:opacity-50'

/** What edit mode draws while the arrangement is a draft (v2, an own list, the view read). */
export interface ArrangeEdit {
  draft: Draft
  names: ReadonlyMap<number, string>
  dirty: boolean
  busy: boolean
  said: string | null
  onMove: (id: number, by: -1 | 1) => void
  onSection: (id: number, by: -1 | 1) => void
  onPick: (id: number) => void
  onSave: () => void
}

/**
 * M8.4 F5: the arrangement of the open own list while editing is a draft held here, saved with one
 * `arrange` (the section order, each symbol's section, the item order, the version `view` answered).
 * Outside edit mode `sendTo` writes the one change at once (the symbol sheet's Move to section).
 * Either way the list is read again whatever the answer.
 */
export function useArrange({
  ref,
  listId,
  view,
  items,
  editing,
  open,
}: {
  ref: string | null
  listId: number | null
  view: View | undefined
  items: readonly WatchlistItem[]
  editing: boolean
  open: (id: number) => void
}) {
  const client = useQueryClient()
  const [held, setHeld] = useState<{ draft: Draft; base: Draft } | null>(null)
  const [busy, setBusy] = useState(false)
  const [said, setSaid] = useState<string | null>(null)
  const on = editing && view !== undefined && ref !== null

  useEffect(() => {
    if (!on) {
      setHeld(null)
      setSaid(null)
    } else if (view)
      setHeld((was) => was ?? { draft: draftOf(view, items), base: draftOf(view, items) })
  }, [on, view, items])

  const change = useCallback(
    (fn: (draft: Draft) => Draft) =>
      setHeld((was) => (was ? { ...was, draft: fn(fitTo(was.draft, items)) } : was)),
    [items]
  )

  const write = useCallback(
    async (draft: Draft) => {
      if (!view || !ref || listId === null) return { said: null, code: null }
      setBusy(true)
      const out = await writeThenRead(client, arrangeKeys(ref), () =>
        callOp('arrange', arrangeReq(draft, listId, view.version))
      )
      setBusy(false)
      return out
    },
    [client, view, ref, listId]
  )

  /** Save order: true once the order is written (review M4: leaving edit mode waits for it). */
  const save = useCallback(async (): Promise<boolean> => {
    if (!held || !ref) return false
    setSaid(null)
    const { draft } = held
    const out = await write(draft)
    setSaid(out.said)
    if (out.code === null) setHeld((was) => was && { ...was, base: draft })
    else if (out.code === 'conflict') {
      // The list is the other screen's now: start the draft again from it.
      const fresh = client.getQueryData<View>(viewKey(ref))
      if (fresh) setHeld({ draft: draftOf(fresh, items), base: draftOf(fresh, items) })
    }
    return out.code === null
  }, [held, ref, write, client, items])

  const names = useMemo(
    () => new Map((view?.sections ?? []).map((section) => [section.id, section.name])),
    [view]
  )
  const draft = held ? fitTo(held.draft, items) : null
  const edit: ArrangeEdit | undefined =
    on && draft && held
      ? {
          draft,
          names,
          dirty: !sameDraft(draft, fitTo(held.base, items)),
          busy,
          said,
          onMove: (id, by) => change((d) => moveRow(d, id, by)),
          onSection: (id, by) => change((d) => moveSection(d, id, by)),
          onPick: open,
          onSave: () => void save(),
        }
      : undefined

  /** A symbol into a section (or No section): into the draft while editing, else written now. */
  const sendTo = async (id: number, section: number | null): Promise<string | null> => {
    if (edit) {
      change((d) => sendRow(d, id, section))
      return null
    }
    if (!view) return null
    return (await write(sendRow(draftOf(view, items), id, section))).said
  }
  const sectionOf = (id: number): number | null =>
    (edit ? edit.draft.rows.find((row) => row.id === id)?.section : undefined) ??
    view?.rows.find((row) => row.item === id)?.section_id ??
    null
  return { edit, sendTo, sectionOf, save }
}

/** Edit mode of an own list in v2: sections with up and down, and each symbol's own controls. */
export function ArrangeRows({
  edit,
  items,
  onRemove,
}: {
  edit: ArrangeEdit
  items: readonly WatchlistItem[]
  onRemove: (item: WatchlistItem) => void
}) {
  const { draft } = edit
  const byId = new Map(items.map((item) => [item.id, item]))
  const groups: (number | null)[] = [null, ...draft.sections]
  return (
    <>
      <ul className="divide-y divide-[color:var(--divider)]">
        {groups.flatMap((section) => {
          const rows = inGroup(draft, section)
          const at = section === null ? -1 : draft.sections.indexOf(section)
          const name = section === null ? '' : (edit.names.get(section) ?? '')
          return [
            ...(section === null
              ? []
              : [
                  <li
                    key={`s${section}`}
                    data-section={section}
                    className="flex min-h-12 items-center gap-1 bg-[color:var(--surface-2)] pl-4"
                  >
                    <span className="min-w-0 flex-1 truncate text-[0.9375rem] font-bold">
                      {name} · {rows.length}
                    </span>
                    <button
                      type="button"
                      className={ICON_BUTTON}
                      disabled={at === 0}
                      onClick={() => edit.onSection(section, -1)}
                      aria-label={`Move section ${name} up`}
                    >
                      <ArrowUp aria-hidden="true" className="size-5" />
                    </button>
                    <button
                      type="button"
                      className={ICON_BUTTON}
                      disabled={at === draft.sections.length - 1}
                      onClick={() => edit.onSection(section, 1)}
                      aria-label={`Move section ${name} down`}
                    >
                      <ArrowDown aria-hidden="true" className="size-5" />
                    </button>
                  </li>,
                ]),
            ...rows.map((row, index) => {
              const named = describe(row.symbol, row.exchange)
              const item = byId.get(row.id)
              return (
                <li key={row.id} className="flex min-h-14 items-center py-1 pr-1 pl-4">
                  <span className="flex min-w-0 flex-1 flex-col gap-0.5">
                    <span className="truncate text-[0.9375rem] leading-tight font-semibold">
                      {named.title}
                    </span>
                    <span className="truncate text-xs leading-tight font-medium text-[color:var(--text-tertiary)]">
                      {named.detail}
                    </span>
                  </span>
                  <button
                    type="button"
                    className={ICON_BUTTON}
                    disabled={index === 0}
                    onClick={() => edit.onMove(row.id, -1)}
                    aria-label={`Move ${named.title} up`}
                  >
                    <ArrowUp aria-hidden="true" className="size-5" />
                  </button>
                  <button
                    type="button"
                    className={ICON_BUTTON}
                    disabled={index === rows.length - 1}
                    onClick={() => edit.onMove(row.id, 1)}
                    aria-label={`Move ${named.title} down`}
                  >
                    <ArrowDown aria-hidden="true" className="size-5" />
                  </button>
                  {draft.sections.length > 0 && (
                    <button
                      type="button"
                      aria-haspopup="dialog"
                      className={ICON_BUTTON}
                      onClick={() => edit.onPick(row.id)}
                      aria-label={`Move ${named.title} to section`}
                    >
                      <FolderInput aria-hidden="true" className="size-5" />
                    </button>
                  )}
                  <button
                    type="button"
                    className={cn(ICON_BUTTON, 'text-[color:var(--down)]')}
                    onClick={() => item && onRemove(item)}
                    aria-label={`Remove ${named.title}`}
                  >
                    <Trash2 aria-hidden="true" className="size-5" />
                  </button>
                </li>
              )
            }),
          ]
        })}
      </ul>
      <div
        // Review M4: Save order stays on screen while the list scrolls, just above the bottom bar
        // (on a wide screen there is none). The card clips with overflow-clip in edit mode, as
        // overflow-hidden would hold a sticky bar to the end of the list.
        data-save-bar="true"
        className="sticky bottom-[calc(4.75rem+env(safe-area-inset-bottom))] z-10 flex flex-col gap-2 border-t border-[color:var(--divider)] bg-card p-3 md:bottom-0"
      >
        {edit.said && (
          <p role="alert" className="text-sm font-medium text-[color:var(--down)]">
            {edit.said}
          </p>
        )}
        <button
          type="button"
          disabled={!edit.dirty || edit.busy}
          onClick={edit.onSave}
          className={cn(BUTTON, 'bg-primary text-primary-foreground hover:bg-primary/90')}
        >
          {edit.busy ? 'Saving' : 'Save order'}
        </button>
        {edit.dirty && (
          <p className="text-xs font-medium text-muted-foreground">
            The new order is not saved yet.
          </p>
        )}
      </div>
    </>
  )
}

/**
 * Review M4: leaving edit mode, or the list, while the moves are not saved asks first (`?sheet=
 * unsaved`, v2 only). Save order writes the draft and goes on; Discard moves drops it and goes on;
 * Keep editing, the close button, a tap outside or Back stays in edit mode with the moves.
 */
export function UnsavedSheet({
  open,
  listName,
  busy,
  onSave,
  onDiscard,
  onKeep,
}: {
  open: boolean
  listName: string
  busy: boolean
  onSave: () => void
  onDiscard: () => void
  onKeep: () => void
}) {
  return (
    <BottomSheet
      open={open}
      onOpenChange={(next) => !next && onKeep()}
      title="Save the new order?"
      description={listName}
    >
      <div className="flex flex-col gap-3">
        <p className="text-sm font-medium">
          You moved symbols in {listName} and have not saved the new order.
        </p>
        <div className="flex flex-col gap-2">
          <button
            type="button"
            disabled={busy}
            onClick={onSave}
            className={cn(BUTTON, 'bg-primary text-primary-foreground hover:bg-primary/90')}
          >
            {busy ? 'Saving' : 'Save order'}
          </button>
          <button
            type="button"
            disabled={busy}
            onClick={onDiscard}
            className={cn(BUTTON, 'border border-input bg-card hover:bg-accent')}
          >
            Discard moves
          </button>
          <button
            type="button"
            onClick={onKeep}
            className={cn(BUTTON, 'border border-input bg-card hover:bg-accent')}
          >
            Keep editing
          </button>
        </div>
      </div>
    </BottomSheet>
  )
}

/** Where a symbol goes: No section, or one of the list's sections; the current one is ticked. */
function SectionPick({
  title,
  current,
  sections,
  onPick,
  onClose,
}: {
  title: string
  current: number | null
  sections: { id: number; name: string }[]
  onPick: (section: number | null) => Promise<string | null>
  onClose: () => void
}) {
  const [busy, setBusy] = useState(false)
  const [problem, setProblem] = useState<string | null>(null)
  const choose = async (section: number | null) => {
    if (section === current) return onClose()
    setBusy(true)
    const said = await onPick(section)
    setBusy(false)
    setProblem(said)
    if (said === null) onClose()
  }
  const options = [{ id: null, name: 'No section' }, ...sections]
  return (
    <BottomSheet
      open
      onOpenChange={(next) => !next && onClose()}
      title="Move to section"
      description={title}
    >
      <div className="flex flex-col">
        {options.map((option) => (
          <button
            key={option.id ?? 'none'}
            type="button"
            disabled={busy}
            aria-pressed={option.id === current}
            onClick={() => void choose(option.id)}
            className={cn(
              'flex min-h-12 w-full items-center gap-3 rounded-xl px-3 text-left text-[0.9375rem] font-semibold outline-none hover:bg-accent focus-visible:ring-2 focus-visible:ring-ring disabled:opacity-50',
              option.id === current && 'bg-[color:var(--primary-tint)] font-bold'
            )}
          >
            {option.name}
            {option.id === current && (
              <>
                <Check aria-hidden="true" className="ml-auto size-5" />
                <span className="sr-only">(now)</span>
              </>
            )}
          </button>
        ))}
      </div>
      {problem && (
        <p role="alert" className="px-1 pt-2 text-sm font-medium text-[color:var(--down)]">
          {problem}
        </p>
      )}
    </BottomSheet>
  )
}

/** The F5 sheets (`?sheet=to-section`, `?sheet=to-list`): shown over the list while v2 is on. */
export function ArrangeSheets({
  overlay,
  view,
  items,
  listId,
  sendTo,
  sectionOf,
  onClose,
}: {
  overlay: Overlay | null
  view: View | undefined
  items: readonly WatchlistItem[]
  listId: number | null
  sendTo: (id: number, section: number | null) => Promise<string | null>
  sectionOf: (id: number) => number | null
  onClose: () => void
}) {
  const itemId =
    overlay?.kind === 'pickSection'
      ? overlay.item
      : overlay?.kind === 'pickList'
        ? overlay.item
        : null
  const item = itemId === null ? null : (items.find((each) => each.id === itemId) ?? null)
  const sectionId = overlay?.kind === 'pickList' ? overlay.section : null
  const section =
    sectionId === null ? null : (view?.sections.find((each) => each.id === sectionId) ?? null)
  // What was picked is gone (removed on another screen): the sheet closes rather than stay over nothing.
  const gone =
    view !== undefined &&
    items.length > 0 &&
    ((itemId !== null && item === null) || (sectionId !== null && section === null))
  useEffect(() => {
    if (gone) onClose()
  }, [gone, onClose])
  if (!overlay || !view || listId === null || gone) return null
  if (overlay.kind === 'pickSection' && item) {
    return (
      <SectionPick
        title={describe(item.symbol, item.exchange).title}
        current={sectionOf(item.id)}
        sections={[...view.sections].sort((a, b) => a.position - b.position)}
        onPick={(next) => sendTo(item.id, next)}
        onClose={onClose}
      />
    )
  }
  if (overlay.kind === 'pickList') {
    const keys = item
      ? [`${item.exchange}:${item.symbol}`]
      : view.rows
          .filter((row) => row.item !== null && row.section_id === sectionId)
          .map((row) => row.contract.key)
    return (
      <ListPicker
        view={view}
        keys={keys}
        sectionId={sectionId}
        label={item ? describe(item.symbol, item.exchange).title : (section?.name ?? '')}
        move={overlay.move}
        onClose={onClose}
      />
    )
  }
  return null
}
