import { useQueryClient } from '@tanstack/react-query'
import { CircleCheck, Loader2, TriangleAlert } from 'lucide-react'
import { type ReactNode, useEffect, useId, useRef, useState } from 'react'
import { type RiskSettings, riskApi, type SettingsChange } from '@/api/risk'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Switch } from '@/components/ui/switch'
import { RISK_AUDIT_KEY, RISK_STATUS_KEY } from './parts'

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
export const PHRASE_FOR_LOOSENING =
  'This lets more risk through, so it needs your confirm phrase. It is STOP unless you changed it.'

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
 */
export function useSaveSettings() {
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
      if (answer.needsPhrase) {
        setPending(changes)
        return false
      }
      setPending(null)
      setPhrase('')
      if (answer.ok || answer.code === 409) {
        void queryClient.invalidateQueries({ queryKey: RISK_STATUS_KEY })
        void queryClient.invalidateQueries({ queryKey: RISK_AUDIT_KEY })
      }
      return answer.ok
    } catch {
      setSaid({ ok: false, message: NOT_SAVED })
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
}: {
  saver: Saver
  label: string
  onSave: () => void
  disabled?: boolean
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
          <p className="text-[0.8125rem] text-[color:var(--text-secondary)]">
            {PHRASE_FOR_LOOSENING}
          </p>
          <Input
            id={`${id}-phrase`}
            className="h-12 text-base"
            autoComplete="off"
            autoCapitalize="characters"
            value={saver.phrase}
            onChange={(event) => saver.setPhrase(event.target.value)}
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

/** The fields as the settings were saved, again whenever they are saved anew. */
function useFields<T extends Record<string, string | boolean>>(
  settings: RiskSettings,
  from: (settings: RiskSettings) => T
): [T, (patch: Partial<T>) => void] {
  const [fields, setFields] = useState<T>(() => from(settings))
  // biome-ignore lint/correctness/useExhaustiveDependencies: refilled per saved version only
  useEffect(() => {
    setFields(from(settings))
  }, [settings.updated_at])
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

/** The daily loss limit: set, change or turn off (the card above shows today's figure). */
export function DailyLimitEditor({ settings }: { settings: RiskSettings }) {
  const saver = useSaveSettings()
  const current = settings.daily_loss_limit
  const [text, setText] = useState(current === null ? '' : String(current))
  // biome-ignore lint/correctness/useExhaustiveDependencies: refilled per saved version only
  useEffect(() => {
    setText(current === null ? '' : String(current))
  }, [settings.updated_at])
  const typed = numberOf(text)
  return (
    <div className="flex flex-col gap-3 border-t border-[color:var(--divider)] pt-3">
      <NumberField
        label={current === null ? 'Set a limit for the day' : 'Limit for the day'}
        unit="₹ of loss"
        value={text}
        onChange={setText}
        whole
      />
      <SaveRow
        saver={saver}
        label={current === null ? 'Turn the daily limit on' : 'Save the daily limit'}
        onSave={() => void saver.save({ daily_loss_limit: typed })}
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
  const [fields, set] = useFields(settings, (s) => ({
    band_on: s.band_on,
    band_opt_pct: String(s.band_opt_pct),
    band_opt_min_rs: String(s.band_opt_min_rs),
    band_fut_pct: String(s.band_fut_pct),
    band_cash_pct: String(s.band_cash_pct),
    band_both_sides: s.band_both_sides,
  }))
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
  const [fields, set] = useFields(settings, (s) => ({
    size_on: s.size_on,
    max_lots_fo: String(s.max_lots_fo),
    max_qty_cash: String(s.max_qty_cash),
  }))
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
  const [fields, set] = useFields(settings, (s) => ({
    burst_on: s.burst_on,
    burst_n: String(s.burst_n),
    burst_window_s: String(s.burst_window_s),
  }))
  const changes = changed(settings, fields)
  return (
    <div className="flex flex-col gap-3" data-testid="pacing-card">
      <p className="text-[0.9375rem] leading-snug" data-testid="pacing">
        {perSecond === null
          ? 'Orders go to Upstox at the pace this server sets.'
          : `Orders go to Upstox at most ${perSecond} a second, below the 10 Upstox allows. This is set on the server.`}
      </p>
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
      <p className="text-[0.8125rem] text-[color:var(--text-secondary)]">
        Exits are never paused; a basket counts once.
      </p>
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
      <TextField label="New phrase" value={next} onChange={setNext} />
      <SaveRow
        saver={saver}
        label="Change the phrase"
        onSave={() => void saver.save({ confirm_phrase: next.trim() })}
        disabled={next.trim() === ''}
      />
    </div>
  )
}

function TextField({
  label,
  value,
  onChange,
}: {
  label: string
  value: string
  onChange: (next: string) => void
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
      <Input
        id={id}
        className="h-12 text-base"
        autoComplete="off"
        autoCapitalize="characters"
        value={value}
        onChange={(event) => onChange(event.target.value)}
      />
    </div>
  )
}
