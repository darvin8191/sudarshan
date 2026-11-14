// SUDARSHAN P7 F4 (M7.36): "To confirm" (design p7 6.3; owner decision 10). A bonus or a split
// never changes a lot by itself: each waits here with the service's sentence on the owner's own
// quantity, a button for the other way round, and one tap to apply it or to leave the lots as
// they are. For one of his own tabs the card shows the entry ready to add, with what the entry
// form's dry run says of it, and writes nothing until he adds it; then it confirms.
import { ArrowLeftRight, Check } from 'lucide-react'
import { useRef, useState } from 'react'
import { decideCorporateAction, portfolioCall } from '../api'
import { Box } from '../sheets/EntryFields'
import { PRIMARY, ProblemLine, SECONDARY } from '../sheets/TabFields'
import { useSend } from '../sheets/useSend'
import { dayText } from '../tab/format'
import type { CaEvent, CaExpected, TabInfo } from '../types'
import { entriesFor, typedRatio, UPSTOX_SCOPE, wayOf } from './confirm'
import { KINDS } from './format'

const CARD = 'rounded-[1.125rem] border bg-card px-4 py-3.5 shadow-[var(--shadow-card)]'

function Holding({
  event,
  expected,
  tab,
  onDone,
}: {
  event: CaEvent
  expected: CaExpected
  tab: TabInfo | undefined
  onDone: () => void
}) {
  const [other, setOther] = useState(false)
  const [num, setNum] = useState('')
  const [den, setDen] = useState('')
  const [preview, setPreview] = useState<string[] | null>(null)
  const send = useSend()
  const requestId = useRef(crypto.randomUUID())
  const kind = KINDS[event.kind].word.toLowerCase()
  const ofTab = expected.scope !== UPSTOX_SCOPE
  const where = ofTab ? (expected.name ?? 'your tab') : 'Upstox'
  const way = wayOf(expected, other)
  const ratio = way ?? typedRatio(num, den)
  const entries = ofTab ? entriesFor(event.kind, expected, other) : null
  const flip = () => {
    setOther((was) => !was)
    setPreview(null)
    send.clear()
  }
  const decide = (state: 'applied' | 'dismissed') =>
    decideCorporateAction({
      id: event.id,
      scope: expected.scope,
      state,
      ratio_num: ratio?.ratio_num ?? 1,
      ratio_den: ratio?.ratio_den ?? 1,
      request_id: requestId.current,
    })
  const run = async (work: () => Promise<unknown>) => {
    if (await send.run(work)) onDone()
  }
  const look = () =>
    send.run(async () => {
      if (!tab || !entries) return
      const said: string[] = []
      for (const entry of entries) {
        const result = await portfolioCall('activity_create', {
          id: tab.id,
          version: tab.version,
          entry,
          dry_run: true,
          request_id: crypto.randomUUID(),
        })
        if (result.enabled) said.push(result.data.effect.words)
      }
      setPreview(said)
    })
  const addAndConfirm = () =>
    run(async () => {
      if (!tab || !entries) return
      let version = tab.version
      for (const [n, entry] of entries.entries()) {
        const result = await portfolioCall('activity_create', {
          id: tab.id,
          version,
          entry,
          request_id: `${requestId.current.slice(0, 33)}${String(n).padStart(3, '0')}`,
        })
        if (result.enabled) version = result.data.tab_version
      }
      await decide('applied')
    })
  return (
    <li className="flex flex-col gap-2 border-t border-[color:var(--divider)] pt-2.5">
      <h4 className="text-sm font-bold">{where}</h4>
      <p className="text-[0.9375rem]">{way?.words ?? expected.words}</p>
      {way?.fraction && (
        <p className="text-sm text-[color:var(--warning-text)]">
          Part of a share is left over. Enter what the company paid for it in the tab.
        </p>
      )}
      {way === null && !ofTab && (
        <div className="grid grid-cols-2 gap-2">
          <Box
            label={event.kind === 'bonus' ? 'New shares' : 'Shares after'}
            value={num}
            onChange={setNum}
            inputMode="numeric"
          />
          <Box
            label={event.kind === 'bonus' ? 'For every held' : 'For every held before'}
            value={den}
            onChange={setDen}
            inputMode="numeric"
          />
        </div>
      )}
      {preview?.map((line) => (
        <p key={line} className="rounded-[0.875rem] bg-[color:var(--brand-tint)] px-3 py-2 text-sm">
          {line}
        </p>
      ))}
      <ProblemLine problem={send.problem} />
      {ofTab && entries === null ? (
        <p className="text-sm text-[color:var(--text-secondary)]">
          Add this {kind} to {where} by hand in the tab, then confirm it here.
        </p>
      ) : ofTab && preview === null ? (
        <button
          type="button"
          className={PRIMARY}
          disabled={send.pending}
          onClick={() => void look()}
        >
          See the entry to add to {where}
        </button>
      ) : ofTab ? (
        <button type="button" className={PRIMARY} disabled={send.pending} onClick={addAndConfirm}>
          <Check aria-hidden="true" className="size-4" />
          Add to {where} and confirm
        </button>
      ) : (
        <button
          type="button"
          className={PRIMARY}
          disabled={send.pending || ratio === null}
          onClick={() => run(() => decide('applied'))}
        >
          <Check aria-hidden="true" className="size-4" />
          Yes, apply this {kind}
        </button>
      )}
      {expected.other !== null && (
        <button type="button" className={SECONDARY} disabled={send.pending} onClick={flip}>
          <ArrowLeftRight aria-hidden="true" className="size-4" />
          {other ? 'Back to the first reading' : 'The other way round'}
        </button>
      )}
      <button
        type="button"
        className={SECONDARY}
        disabled={send.pending}
        onClick={() => run(() => decide('dismissed'))}
      >
        Leave my lots as they are
      </button>
    </li>
  )
}

export function ToConfirm({
  events,
  tabs,
  onDone,
}: {
  events: readonly CaEvent[]
  tabs: readonly TabInfo[]
  onDone: () => void
}) {
  if (events.length === 0) return null
  return (
    <section aria-label="To confirm" className="flex flex-col gap-3">
      <h3 className="text-base font-bold">To confirm</h3>
      {events.map((event) => {
        const Icon = KINDS[event.kind].icon
        return (
          <article key={event.id} className={`${CARD} flex flex-col gap-2`}>
            <div className="flex items-start justify-between gap-3">
              <span className="flex min-w-0 flex-col">
                <span className="text-[0.9375rem] font-bold break-words">
                  {event.name ?? event.isin}
                </span>
                <span className="text-xs text-[color:var(--text-tertiary)]">
                  Ex-date {dayText(event.ex_day)}
                </span>
              </span>
              <span className="inline-flex shrink-0 items-center gap-1 rounded-full border border-[color:var(--border-strong)] px-2 py-0.5 text-xs font-semibold">
                <Icon aria-hidden="true" className="size-3.5" />
                {KINDS[event.kind].word}
              </span>
            </div>
            <ul className="flex flex-col gap-2.5">
              {(event.expected ?? []).map((expected) => (
                <Holding
                  key={expected.scope}
                  event={event}
                  expected={expected}
                  tab={tabs.find((t) => t.id === expected.tab_id)}
                  onDone={onDone}
                />
              ))}
            </ul>
          </article>
        )
      })}
    </section>
  )
}
