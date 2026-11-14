import { useQueryClient } from '@tanstack/react-query'
import { CircleCheck, Loader2, TriangleAlert } from 'lucide-react'
import { type ReactNode, useEffect, useId, useRef, useState } from 'react'
import { type RiskSettings, type RiskStatus, riskApi, type SettingsChange } from '@/api/risk'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Switch } from '@/components/ui/switch'
import { PhraseInput } from './PhraseInput'
import { RISK_AUDIT_KEY, RISK_STATUS_KEY } from './parts'
import { PHRASE_FORGOTTEN, PHRASE_HINT } from './phrase'

/**
 * The owner's own limits on Settings > Risk (M20.7, M5.14): the daily loss limit, the price
 * band, the order size limit, the burst brake and the confirm phrase. Every guard ships off
 * (R4); the fields hold what turning one on would start from.
 *
 * The page decides nothing: it sends what changed, and the server says whether it loosens a
 * limit. When it does, the server asks for the confirm phrase and the page shows the field
 * (settings_rules.py); the phrase is never read back.
 */

const NOT_SAVED = 'The risk settings could not be saved. Try again in a few seconds.'
/** Limits review L5: what the phrase is asked for, never the phrase itself. */
export const PHRASE_FOR_LOOSENING = 'This lets more risk through, so it needs your confirm phrase.'
export const PHRASE_FOR_CHANGE = 'Changing the phrase needs the one it replaces.'
/** What the burst brake counts (M5.14), as the gate counts it. */
export const BRAKE_COUNTS =
  'It counts new orders that add risk; a basket, a split or a multi-leg order counts as one. Changes to an order and exits are never counted or paused.'
/** Upstox's own limit on orders a second: a pace above 9 is warned about. */
const UPSTOX_PER_SECOND = 10

/** The pace this server sends orders to Upstox at, in words (a warning at 10 or more). */
export function paceWords(perSecond: number | null): { said: string; warn: boolean } {
  if (perSecond === null) {
    return { said: 'Orders go to Upstox at the pace this server sets.', warn: false }
  }
  if (perSecond < UPSTOX_PER_SECOND) {
    return {
      said: `Orders go to Upstox at most ${perSecond} a second, below the ${UPSTOX_PER_SECOND} Upstox allows. This is set on the server.`,
      warn: false,
    }
  }
  return {
    said: `Orders go to Upstox at up to ${perSecond} a second, and Upstox allows ${UPSTOX_PER_SECOND}: in a burst Upstox refuses the orders past its limit. Set the pace to 9 or less on the server.`,
    warn: true,
  }
}

const wholeRupees = new Intl.NumberFormat('en-IN', {
  style: 'currency',
  currency: 'INR',
  maximumFractionDigits: 0,
})

interface Said {
  ok: boolean
  message: string
}

/**
 * Saving one card's changes. A change the server says loosens a limit is kept as it was asked,
 * and confirmed with the phrase as that same change: never the card's fields as they are by
 * then, so a "turn off" confirmed is a turn off. One save at a time, however it is tapped.
 * `onSettled` hears each change once its save has its last answer: saved, refused or not
 * answered, never the server asking for the phrase (the change still waits then).
 */
export function useSaveSettings(onSettled?: (changes: SettingsChange) => void) {
  const queryClient = useQueryClient()
  const sending = useRef(false)
  const [busy, setBusy] = useState(false)
  const [said, setSaid] = useState<Said | null>(null)
  const [pending, setPending] = useState<SettingsChange | null>(null)
  const [phrase, setPhrase] = useState('')

  const send = async (changes: SettingsChange, withPhrase?: string): Promise<boolean> => {
    if (sending.current) return false
    sending.current = true
    setBusy(true)
    try {
      const answer = await riskApi.saveSettings(changes, withPhrase)
      setSaid({ ok: answer.ok, message: answer.message })
      // History is read again after every answer, as the kill switch's sheets read it: a wrong
      // phrase is a line there (what was asked, never what was typed), and so is a saved change.
      void queryClient.invalidateQueries({ queryKey: RISK_AUDIT_KEY })
      if (answer.needsPhrase) {
        if (withPhrase !== undefined) setPhrase('') // a wrong phrase is typed again, not kept
        setPending(changes)
        return false
      }
      setPending(null)
      setPhrase('')
      if (answer.ok || answer.code === 409) {
        void queryClient.invalidateQueries({ queryKey: RISK_STATUS_KEY })
      }
      onSettled?.(changes)
      return answer.ok
    } catch {
      setSaid({ ok: false, message: NOT_SAVED })
      onSettled?.(changes)
      return false
    } finally {
      sending.current = false
      setBusy(false)
    }
  }

  return {
    save: (changes: SettingsChange) => send(changes),
    /** The change the server asked the phrase for, sent again with it. */
    confirm: () => (pending ? send(pending, phrase) : Promise.resolve(false)),
    busy,
    said,
    phraseNeeded: pending !== null,
    phrase,
    setPhrase,
  }
}

type Saver = ReturnType<typeof useSaveSettings>

/** The save button, the phrase when the server asked for it, and the server's sentence. */
function SaveRow({
  saver,
  label,
  onSave,
  disabled,
  phraseHint = PHRASE_FOR_LOOSENING,
}: {
  saver: Saver
  label: string
  onSave: () => void
  disabled?: boolean
  phraseHint?: string
}) {
  const id = useId()
  return (
    <div className="flex flex-col gap-2.5">
      <Button className="h-12 text-base" onClick={onSave} disabled={disabled || saver.busy}>
        {saver.busy && <Loader2 aria-hidden="true" className="size-5 animate-spin" />}
        {label}
      </Button>
      {saver.phraseNeeded && (
        <div className="flex flex-col gap-1.5">
          <Label htmlFor={`${id}-phrase`} className="text-[0.875rem] font-semibold">
            Confirm phrase
          </Label>
          <p
            id={`${id}-hint`}
            className="text-[0.8125rem] text-[color:var(--text-secondary)]"
            data-testid="phrase-hint"
          >
            <span>{phraseHint}</span> <span>{PHRASE_HINT}</span>
          </p>
          <PhraseInput
            id={`${id}-phrase`}
            describedBy={`${id}-hint`}
            value={saver.phrase}
            onChange={saver.setPhrase}
          />
          <Button
            variant="outline"
            className="h-12 text-base"
            onClick={() => void saver.confirm()}
            disabled={saver.busy || !saver.phrase.trim()}
          >
            Confirm with your phrase
          </Button>
        </div>
      )}
      {saver.said && (
        <output className="flex items-start gap-2 text-[0.875rem] leading-snug">
          {saver.said.ok ? (
            <CircleCheck
              aria-hidden="true"
              className="mt-0.5 size-4 shrink-0 text-[color:var(--live-ink)]"
            />
          ) : (
            <TriangleAlert
              aria-hidden="true"
              className="mt-0.5 size-4 shrink-0 text-[color:var(--warning-text)]"
            />
          )}
          <span>{saver.said.message}</span>
        </output>
      )}
    </div>
  )
}

/** An on or off row: the whole row is the touch target, and the state is said in a word. */
function OnOff({
  label,
  checked,
  onChange,
}: {
  label: string
  checked: boolean
  onChange: (next: boolean) => void
}) {
  const id = useId()
  return (
    <div className="flex min-h-12 items-center justify-between gap-3">
      <Label htmlFor={id} className="flex-1 cursor-pointer text-[0.9375rem] font-semibold">
        {label}
      </Label>
      <span className="text-[0.8125rem] font-bold uppercase tracking-[0.06em] text-[color:var(--text-secondary)]">
        {checked ? 'On' : 'Off'}
      </span>
      <Switch id={id} checked={checked} onCheckedChange={onChange} />
    </div>
  )
}

/** A number field: typed as text so a phone shows its number pad, sent as a number. */
function NumberField({
  label,
  unit,
  value,
  onChange,
  whole,
}: {
  label: string
  unit: ReactNode
  value: string
  onChange: (next: string) => void
  whole?: boolean
}) {
  const id = useId()
  return (
    <div className="flex flex-col gap-1">
      <Label
        htmlFor={id}
        className="text-[0.875rem] font-semibold text-[color:var(--text-secondary)]"
      >
        {label}
      </Label>
      <div className="flex items-center gap-2">
        <Input
          id={id}
          className="h-12 text-base"
          inputMode={whole ? 'numeric' : 'decimal'}
          autoComplete="off"
          value={value}
          onChange={(event) => onChange(event.target.value)}
        />
        <span className="shrink-0 text-[0.875rem] text-[color:var(--text-secondary)]">{unit}</span>
      </div>
    </div>
  )
}

/** A field's text as the number the server takes; NaN when it is not one (the server says so). */
function numberOf(text: string): number {
  const cleaned = text.replace(/[,\s₹%]/g, '')
  return cleaned === '' ? Number.NaN : Number(cleaned)
}

/**
 * The fields as the settings were saved, again whenever they are saved anew, except while a
 * change waits for the confirm phrase (``holding``): the card then keeps showing the change
 * the phrase will confirm, never a version saved meanwhile from another card or screen, and
 * is refilled once that change is answered (limits review L5).
 */
function useFields<T extends Record<string, string | boolean>>(
  settings: RiskSettings,
  from: (settings: RiskSettings) => T,
  holding: boolean
): [T, (patch: Partial<T>) => void] {
  const [fields, setFields] = useState<T>(() => from(settings))
  // biome-ignore lint/correctness/useExhaustiveDependencies: refilled per saved version only
  useEffect(() => {
    if (!holding) setFields(from(settings))
  }, [settings.updated_at, holding])
  return [fields, (patch) => setFields((current) => ({ ...current, ...patch }))]
}

/** What changed, as the server takes it: numbers as numbers, switches as true or false. */
function changed<T extends Record<string, string | boolean>>(
  settings: RiskSettings,
  fields: T
): SettingsChange {
  const out: Record<string, unknown> = {}
  for (const [name, value] of Object.entries(fields)) {
    const before = settings[name as keyof RiskSettings]
    const typed = typeof value === 'boolean' ? value : numberOf(value)
    // Not a number: sent as typed, so the server's sentence says what the field takes.
    const next = typeof typed === 'number' && Number.isNaN(typed) ? value : typed
    if (next !== before) out[name] = next
  }
  return out as SettingsChange
}

// -- the cards' bodies ---------------------------------------------------------------------------

/**
 * Today's loss in a book the limit would lock at the next new order: one in session, not
 * locked yet (outside the session no order looks at the figure). The largest, or null.
 */
export function lossToday(days: RiskStatus['daily']): number | null {
  let largest: number | null = null
  for (const day of Object.values(days ?? {})) {
    if (!day || day.lock || !day.in_session || day.figure === null || day.figure >= 0) continue
    largest = Math.max(largest ?? 0, -day.figure)
  }
  return largest
}

/** Limits review M4: a limit at or below today's loss locks the day at the next new order. */
export function locksTodayWords(loss: number): string {
  return `Today's loss is already ${wholeRupees.format(loss)}: saving locks new orders until tomorrow.`
}

/** The daily loss limit: set, change or turn off (the card above shows today's figure). */
export function DailyLimitEditor({
  settings,
  days,
}: {
  settings: RiskSettings
  days?: RiskStatus['daily']
}) {
  const current = settings.daily_loss_limit
  const [text, setText] = useState(current === null ? '' : String(current))
  // The limit the lock warning was confirmed for: a second tap on the same figure saves it.
  const [confirmed, setConfirmed] = useState<number | null>(null)
  // The limit whose save has its answer, saved or refused: the warning is about a save still to
  // make, so it goes with that answer, not when the status is next read (which can take a while,
  // or fail), and an edit or the first tap of a new try brings it back.
  const [answered, setAnswered] = useState<unknown>(undefined)
  const saver = useSaveSettings((changes) => {
    setAnswered(changes.daily_loss_limit)
    setConfirmed(null)
  })
  // biome-ignore lint/correctness/useExhaustiveDependencies: refilled per saved version only
  useEffect(() => {
    if (saver.phraseNeeded) return // the change waiting for the phrase stays on the card
    setText(current === null ? '' : String(current))
    setConfirmed(null)
    setAnswered(undefined)
  }, [settings.updated_at, saver.phraseNeeded])
  const typed = numberOf(text)
  const loss = lossToday(days)
  // Only while the typed limit is not the saved one: once saved, the day card says the rest.
  const locks =
    !Number.isNaN(typed) && typed > 0 && typed !== current && loss !== null && typed <= loss
  const warns = locks && answered !== typed
  const asking = warns && confirmed === typed
  const edit = (next: string) => {
    setText(next)
    setAnswered(undefined)
  }
  const save = () => {
    if (locks && !asking) {
      setAnswered(undefined)
      setConfirmed(typed) // the first tap: the card says what saving does, the second saves
      return
    }
    void saver.save({ daily_loss_limit: typed })
  }
  let label = current === null ? 'Turn the daily limit on' : 'Save the daily limit'
  if (asking) label = 'Save, and lock new orders until tomorrow'
  return (
    <div className="flex flex-col gap-3 border-t border-[color:var(--divider)] pt-3">
      <NumberField
        label={current === null ? 'Set a limit for the day' : 'Limit for the day'}
        unit="₹ of loss"
        value={text}
        onChange={edit}
        whole
      />
      {warns && loss !== null && (
        <p role="alert" className="sd-risk-note sd-risk-note--day" data-testid="daily-locks-today">
          <TriangleAlert aria-hidden="true" className="size-5 shrink-0" />
          {locksTodayWords(loss)}
        </p>
      )}
      <SaveRow
        saver={saver}
        label={label}
        onSave={save}
        disabled={Number.isNaN(typed) || typed === current}
      />
      {current !== null && (
        <Button
          variant="outline"
          className="h-12 text-base"
          onClick={() => void saver.save({ daily_loss_limit: null })}
          disabled={saver.busy}
        >
          Turn the daily limit off ({wholeRupees.format(current)})
        </Button>
      )}
    </div>
  )
}

/** The price band (design section 6). */
export function BandCard({ settings }: { settings: RiskSettings }) {
  const saver = useSaveSettings()
  const [fields, set] = useFields(
    settings,
    (s) => ({
      band_on: s.band_on,
      band_opt_pct: String(s.band_opt_pct),
      band_opt_min_rs: String(s.band_opt_min_rs),
      band_fut_pct: String(s.band_fut_pct),
      band_cash_pct: String(s.band_cash_pct),
      band_both_sides: s.band_both_sides,
    }),
    saver.phraseNeeded
  )
  const changes = changed(settings, fields)
  return (
    <div className="flex flex-col gap-3" data-testid="band-card">
      <p className="text-[0.9375rem] leading-snug">
        A new order priced too far from the market is refused before it is sent. Exits and market
        orders are never held to it.
      </p>
      <OnOff
        label="Check prices against the market"
        checked={fields.band_on}
        onChange={(band_on) => set({ band_on })}
      />
      <div className="grid grid-cols-2 gap-3">
        <NumberField
          label="Options"
          unit="%"
          value={fields.band_opt_pct}
          onChange={(band_opt_pct) => set({ band_opt_pct })}
        />
        <NumberField
          label="Options, at least"
          unit="₹"
          value={fields.band_opt_min_rs}
          onChange={(band_opt_min_rs) => set({ band_opt_min_rs })}
        />
        <NumberField
          label="Futures"
          unit="%"
          value={fields.band_fut_pct}
          onChange={(band_fut_pct) => set({ band_fut_pct })}
        />
        <NumberField
          label="Cash"
          unit="%"
          value={fields.band_cash_pct}
          onChange={(band_cash_pct) => set({ band_cash_pct })}
        />
      </div>
      <OnOff
        label="Also a price far on the passive side"
        checked={fields.band_both_sides}
        onChange={(band_both_sides) => set({ band_both_sides })}
      />
      <SaveRow
        saver={saver}
        label="Save the price band"
        onSave={() => void saver.save(changes)}
        disabled={Object.keys(changes).length === 0}
      />
    </div>
  )
}

/** The order size limit. */
export function SizeCard({ settings }: { settings: RiskSettings }) {
  const saver = useSaveSettings()
  const [fields, set] = useFields(
    settings,
    (s) => ({
      size_on: s.size_on,
      max_lots_fo: String(s.max_lots_fo),
      max_qty_cash: String(s.max_qty_cash),
    }),
    saver.phraseNeeded
  )
  const changes = changed(settings, fields)
  return (
    <div className="flex flex-col gap-3" data-testid="size-card">
      <p className="text-[0.9375rem] leading-snug">
        A new order bigger than this is refused before it is sent; a basket or a split is held to it
        whole. Exits are never limited.
      </p>
      <OnOff
        label="Limit the size of a new order"
        checked={fields.size_on}
        onChange={(size_on) => set({ size_on })}
      />
      <div className="grid grid-cols-2 gap-3">
        <NumberField
          label="F&O order"
          unit="lots"
          value={fields.max_lots_fo}
          onChange={(max_lots_fo) => set({ max_lots_fo })}
          whole
        />
        <NumberField
          label="Cash order"
          unit="shares"
          value={fields.max_qty_cash}
          onChange={(max_qty_cash) => set({ max_qty_cash })}
          whole
        />
      </div>
      <SaveRow
        saver={saver}
        label="Save the size limit"
        onSave={() => void saver.save(changes)}
        disabled={Object.keys(changes).length === 0}
      />
    </div>
  )
}

/** Orders per second (read only: set on the server) and the burst brake. */
export function PacingCard({
  settings,
  perSecond,
}: {
  settings: RiskSettings
  perSecond: number | null
}) {
  const saver = useSaveSettings()
  const [fields, set] = useFields(
    settings,
    (s) => ({
      burst_on: s.burst_on,
      burst_n: String(s.burst_n),
      burst_window_s: String(s.burst_window_s),
    }),
    saver.phraseNeeded
  )
  const changes = changed(settings, fields)
  const pace = paceWords(perSecond)
  return (
    <div className="flex flex-col gap-3" data-testid="pacing-card">
      {pace.warn ? (
        <p role="alert" className="sd-risk-note sd-risk-note--day" data-testid="pacing">
          <TriangleAlert aria-hidden="true" className="size-5 shrink-0" />
          {pace.said}
        </p>
      ) : (
        <p className="text-[0.9375rem] leading-snug" data-testid="pacing">
          {pace.said}
        </p>
      )}
      <OnOff
        label="Pause new orders after a burst"
        checked={fields.burst_on}
        onChange={(burst_on) => set({ burst_on })}
      />
      <div className="grid grid-cols-2 gap-3">
        <NumberField
          label="After"
          unit="orders"
          value={fields.burst_n}
          onChange={(burst_n) => set({ burst_n })}
          whole
        />
        <NumberField
          label="Within"
          unit="seconds"
          value={fields.burst_window_s}
          onChange={(burst_window_s) => set({ burst_window_s })}
          whole
        />
      </div>
      <p className="text-[0.8125rem] text-[color:var(--text-secondary)]">{BRAKE_COUNTS}</p>
      <SaveRow
        saver={saver}
        label="Save the burst brake"
        onSave={() => void saver.save(changes)}
        disabled={Object.keys(changes).length === 0}
      />
    </div>
  )
}

/** The confirm phrase: changing it needs the one it replaces. */
export function PhraseCard() {
  const saver = useSaveSettings()
  const [next, setNext] = useState('')
  useEffect(() => {
    if (saver.said?.ok) setNext('') // saved: the new phrase is not left on the screen
  }, [saver.said])
  return (
    <div className="flex flex-col gap-3" data-testid="phrase-card">
      <p className="text-[0.9375rem] leading-snug">
        Typed to turn the kill switch on or off and to loosen any limit here. It is never shown
        again once saved.
      </p>
      <p className="text-[0.8125rem] text-[color:var(--text-secondary)]">{PHRASE_FORGOTTEN}</p>
      <NewPhraseField value={next} onChange={setNext} />
      <SaveRow
        saver={saver}
        label="Change the phrase"
        onSave={() => void saver.save({ confirm_phrase: next.trim() })}
        disabled={next.trim() === ''}
        phraseHint={PHRASE_FOR_CHANGE}
      />
    </div>
  )
}

/** The phrase being set, masked like the one it replaces. */
function NewPhraseField({ value, onChange }: { value: string; onChange: (next: string) => void }) {
  const id = useId()
  return (
    <div className="flex flex-col gap-1">
      <Label
        htmlFor={id}
        className="text-[0.875rem] font-semibold text-[color:var(--text-secondary)]"
      >
        New phrase
      </Label>
      <PhraseInput id={id} value={value} onChange={onChange} what="new phrase" />
    </div>
  )
}
