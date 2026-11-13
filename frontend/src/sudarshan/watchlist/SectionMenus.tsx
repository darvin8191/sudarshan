import { useQueryClient } from '@tanstack/react-query'
import { Check, Copy, MoveRight, Pencil, Trash2 } from 'lucide-react'
import { type FormEvent, useEffect, useState } from 'react'
import { Input } from '@/components/ui/input'
import { cn } from '@/lib/utils'
import { BottomSheet } from './BottomSheet'
import { MARK_IDS, MARKS, MarkGlyph } from './marks'
import type { Overlay, OverlayRequest } from './useOverlay'
import { callOp } from './v2/api'
import { NOTHING } from './v2/arrange'
import { NAME_LONG, removeWords, SECTIONS_FULL, writeSection } from './v2/sections'
import { LIMITS, type Section, type View } from './v2/types'

const ROW =
  'flex min-h-12 w-full items-center gap-3 rounded-xl px-3 text-left text-[0.9375rem] font-semibold outline-none hover:bg-accent focus-visible:ring-2 focus-visible:ring-ring disabled:opacity-50'
const BUTTON =
  'inline-flex min-h-12 flex-1 items-center justify-center rounded-[0.875rem] px-5 text-[0.9375rem] font-semibold outline-none focus-visible:ring-[3px] focus-visible:ring-ring/50 disabled:opacity-50'

function Problem({ text }: { text: string | null }) {
  return text ? (
    <p role="alert" className="px-1 pt-2 text-sm font-medium text-[color:var(--down)]">
      {text}
    </p>
  ) : null
}

/** Writes one section op, holding what a sheet shows meanwhile: a sentence, or nothing. */
function useWrite(view: View) {
  const client = useQueryClient()
  const [busy, setBusy] = useState(false)
  const [problem, setProblem] = useState<string | null>(null)
  const run = async (send: (version: string) => Promise<unknown>) => {
    setBusy(true)
    setProblem(null)
    const said = await writeSection(client, view, send)
    setBusy(false)
    setProblem(said)
    return said === null
  }
  return { busy, problem, setProblem, run }
}

/** A section's menu: Rename, its mark (the five and None, each with glyph and name) and Remove. */
function SectionMenu({
  view,
  section,
  onSwap,
  onClose,
}: {
  view: View
  section: Section
  onSwap: (next: OverlayRequest) => void
  onClose: () => void
}) {
  const { busy, problem, run } = useWrite(view)
  const fixed = view.rows.filter((row) => row.item !== null && row.section_id === section.id).length
  const setMark = async (mark: number) => {
    if (mark !== section.mark)
      await run((version) => callOp('section_edit', { id: section.id, mark, version }))
  }
  return (
    <BottomSheet
      open
      onOpenChange={(next) => !next && onClose()}
      title={section.name}
      description="Section"
    >
      <button
        type="button"
        className={ROW}
        onClick={() => onSwap({ kind: 'sectionName', sectionId: section.id })}
      >
        <Pencil aria-hidden="true" className="size-5" />
        Rename
      </button>
      {/* F5: the section's symbols to another list. */}
      {fixed === 0 ? (
        <p className="px-3 py-2 text-[0.8125rem] font-medium text-[color:var(--text-tertiary)]">
          {NOTHING}
        </p>
      ) : (
        <>
          <button
            type="button"
            className={ROW}
            onClick={() =>
              onSwap({ kind: 'pickList', item: null, section: section.id, move: true })
            }
          >
            <MoveRight aria-hidden="true" className="size-5" />
            Move to another list
          </button>
          <button
            type="button"
            className={ROW}
            onClick={() =>
              onSwap({ kind: 'pickList', item: null, section: section.id, move: false })
            }
          >
            <Copy aria-hidden="true" className="size-5" />
            Copy to another list
          </button>
        </>
      )}
      <fieldset className="m-0 border-0 p-0">
        <legend className="px-3 pt-3 pb-1 text-[0.8125rem] font-bold text-[color:var(--text-secondary)]">
          Mark
        </legend>
        {[0, ...MARK_IDS].map((mark) => (
          <button
            key={mark}
            type="button"
            aria-pressed={section.mark === mark}
            disabled={busy}
            onClick={() => void setMark(mark)}
            className={cn(ROW, section.mark === mark && 'bg-[color:var(--primary-tint)] font-bold')}
          >
            {mark === 0 ? (
              <span aria-hidden="true" className="size-4 shrink-0" />
            ) : (
              <MarkGlyph mark={mark} />
            )}
            {mark === 0 ? 'None' : MARKS[mark as keyof typeof MARKS].name}
            {section.mark === mark && <Check aria-hidden="true" className="ml-auto size-5" />}
          </button>
        ))}
      </fieldset>
      <button
        type="button"
        className={cn(ROW, 'mt-1 text-[color:var(--down)]')}
        onClick={() => onSwap({ kind: 'sectionRemove', sectionId: section.id })}
      >
        <Trash2 aria-hidden="true" className="size-5" />
        Remove section
      </button>
      <Problem text={problem} />
    </BottomSheet>
  )
}

/** The name of a new section, or of an existing one. */
function NameSheet({
  view,
  listId,
  section,
  onClose,
}: {
  view: View
  listId: number
  section: Section | null
  onClose: () => void
}) {
  const { busy, problem, setProblem, run } = useWrite(view)
  const [value, setValue] = useState(section?.name ?? '')
  const submit = async (event: FormEvent) => {
    event.preventDefault()
    const name = value.trim()
    if (!name || busy) return
    if ([...name].length > LIMITS.section_name) return setProblem(NAME_LONG)
    if (!section && view.sections.length >= LIMITS.sections_per_list)
      return setProblem(SECTIONS_FULL)
    const done = await run((version) =>
      section
        ? callOp('section_edit', { id: section.id, name, version })
        : callOp('section_add', { list: listId, name, version })
    )
    if (done) onClose()
  }
  return (
    <BottomSheet
      open
      onOpenChange={(next) => !next && onClose()}
      title={section ? 'Rename section' : 'New section'}
    >
      <form onSubmit={submit} className="flex flex-col gap-3">
        <Input
          aria-label="Section name"
          value={value}
          placeholder="For example, Banks"
          onChange={(event) => {
            setValue(event.target.value)
            setProblem(null)
          }}
          className="h-12 text-base"
        />
        <Problem text={problem} />
        <div className="flex gap-2">
          <button
            type="button"
            onClick={onClose}
            className={cn(BUTTON, 'border border-input bg-card hover:bg-accent')}
          >
            Cancel
          </button>
          <button
            type="submit"
            disabled={!value.trim() || busy}
            className={cn(BUTTON, 'bg-primary text-primary-foreground hover:bg-primary/90')}
          >
            {busy ? 'Saving' : 'Save'}
          </button>
        </div>
      </form>
    </BottomSheet>
  )
}

/** Remove a section, after saying what stays. */
function RemoveSheet({
  view,
  section,
  onClose,
}: {
  view: View
  section: Section
  onClose: () => void
}) {
  const { busy, problem, run } = useWrite(view)
  return (
    <BottomSheet open onOpenChange={(next) => !next && onClose()} title="Remove section">
      <p className="px-1 text-[0.9375rem] font-medium">{removeWords(section)}</p>
      <Problem text={problem} />
      <div className="flex gap-2 pt-3">
        <button
          type="button"
          onClick={onClose}
          className={cn(BUTTON, 'border border-input bg-card hover:bg-accent')}
        >
          Cancel
        </button>
        <button
          type="button"
          disabled={busy}
          onClick={async () => {
            if (await run((version) => callOp('section_remove', { id: section.id, version })))
              onClose()
          }}
          className={cn(BUTTON, 'bg-destructive text-white hover:bg-destructive/90')}
        >
          {busy ? 'Removing' : 'Remove'}
        </button>
      </div>
    </BottomSheet>
  )
}

/**
 * The sheets of the section overlays (`?sheet=section`, `section-name`, `section-remove`), shown over
 * the list while v2 is on. They are bottom sheets so Back closes them first and they ignore taps for
 * the first half second. A section that is no longer in the list (removed on another screen, seen
 * after the list was read again) closes its sheet.
 */
export function SectionSheets({
  overlay,
  view,
  listId,
  onSwap,
  onClose,
}: {
  overlay: Overlay | null
  view: View | undefined
  listId: number | null
  onSwap: (next: OverlayRequest) => void
  onClose: () => void
}) {
  const id = overlay && 'sectionId' in overlay ? overlay.sectionId : null
  const section = id === null ? null : (view?.sections.find((each) => each.id === id) ?? null)
  const gone = view !== undefined && id !== null && section === null
  useEffect(() => {
    if (gone) onClose()
  }, [gone, onClose])
  if (!overlay || !view || listId === null) return null
  if (overlay.kind === 'sectionName' && (overlay.sectionId === null || section)) {
    return (
      <NameSheet
        key={id ?? 'new'}
        view={view}
        listId={listId}
        section={section}
        onClose={onClose}
      />
    )
  }
  if (overlay.kind === 'section' && section)
    return <SectionMenu view={view} section={section} onSwap={onSwap} onClose={onClose} />
  if (overlay.kind === 'sectionRemove' && section)
    return <RemoveSheet view={view} section={section} onClose={onClose} />
  return null
}
