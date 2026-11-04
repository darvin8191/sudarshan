import { TriangleAlert } from 'lucide-react'
import { useEffect, useRef, useState } from 'react'
import { BottomSheet } from '@/sudarshan/watchlist/BottomSheet'
import { portfolioCall } from '../api'
import { MAX_TABS, type TabColour, type TabIcon, type TabInfo, type TabKind, WORDS } from '../types'
import {
  ColourChoices,
  IconChoices,
  KindChips,
  NameFields,
  PinnedField,
  PRIMARY,
  ProblemLine,
  SECONDARY,
} from './TabFields'
import { useSend } from './useSend'

/**
 * "New tab": the name (up to 40 letters) and an optional second name, the kind, an icon, a
 * colour (decoration only) and whether it is pinned to the dashboard. Save is sent once, with a
 * request id made when the sheet opens, so a resend after a lost answer returns the tab already
 * made and never a second one. At 50 tabs the sheet says so and does not send.
 */
export function NewTabSheet({
  open,
  onClose,
  tabCount,
  onCreated,
}: {
  open: boolean
  onClose: () => void
  /** The tabs that count towards the 50: custom ones that are not archived. */
  tabCount: number
  onCreated: (tab: TabInfo) => void
}) {
  const [name, setName] = useState('')
  const [nameAlt, setNameAlt] = useState('')
  const [kind, setKind] = useState<TabKind>('stocks')
  const [icon, setIcon] = useState<TabIcon>('briefcase')
  const [colour, setColour] = useState<TabColour>('brand')
  const [pinned, setPinned] = useState(false)
  const requestId = useRef(crypto.randomUUID())
  const send = useSend()
  const full = tabCount >= MAX_TABS

  // biome-ignore lint/correctness/useExhaustiveDependencies: a fresh form on each opening only
  useEffect(() => {
    if (!open) return
    requestId.current = crypto.randomUUID()
    setName('')
    setNameAlt('')
    setKind('stocks')
    setIcon('briefcase')
    setColour('brand')
    setPinned(false)
    send.clear()
  }, [open])

  const ready = name.trim().length > 0 && !full
  const save = async () => {
    if (!ready) return
    let made: TabInfo | null = null
    const ok = await send.run(async () => {
      const result = await portfolioCall('tab_create', {
        request_id: requestId.current,
        name: name.trim(),
        name_alt: nameAlt.trim() || null,
        kind,
        icon,
        colour,
        pinned,
      })
      if (!result.enabled) throw new Error(result.message)
      made = result.data
    })
    if (ok && made) onCreated(made)
  }

  return (
    <BottomSheet open={open} onOpenChange={(next) => !next && onClose()} title="New tab">
      <form
        className="flex flex-col gap-4 overflow-y-auto px-4 pb-4"
        onSubmit={(event) => {
          event.preventDefault()
          void save()
        }}
      >
        {full && (
          <p className="flex items-start gap-2 text-sm font-medium text-[color:var(--warning-text)]">
            <TriangleAlert aria-hidden="true" className="mt-0.5 size-4 shrink-0" />
            {WORDS.tab_limit}
          </p>
        )}
        <NameFields
          name={name}
          nameAlt={nameAlt}
          onName={setName}
          onNameAlt={setNameAlt}
          disabled={send.pending}
        />
        <KindChips value={kind} onChange={setKind} disabled={send.pending} />
        <IconChoices value={icon} onChange={setIcon} disabled={send.pending} />
        <ColourChoices value={colour} onChange={setColour} disabled={send.pending} />
        <PinnedField value={pinned} onChange={setPinned} disabled={send.pending} />
        <ProblemLine problem={send.problem} />
        <div className="flex gap-2">
          <button type="button" className={`${SECONDARY} flex-1`} onClick={onClose}>
            Cancel
          </button>
          <button type="submit" className={`${PRIMARY} flex-1`} disabled={!ready || send.pending}>
            {send.pending ? 'Saving' : 'Create tab'}
          </button>
        </div>
      </form>
    </BottomSheet>
  )
}
