import {
  Briefcase,
  ChartLine,
  Check,
  Coins,
  Gem,
  House,
  Landmark,
  type LucideIcon,
  Target,
  Wallet,
} from 'lucide-react'
import { cn } from '@/lib/utils'
import {
  NAME_MAX,
  TAB_COLOURS,
  TAB_ICONS,
  TAB_KIND_LABELS,
  TAB_KINDS,
  type TabColour,
  type TabIcon,
  type TabKind,
} from '../types'

export const ICONS: Record<TabIcon, { icon: LucideIcon; label: string }> = {
  briefcase: { icon: Briefcase, label: 'Briefcase' },
  chart: { icon: ChartLine, label: 'Chart' },
  landmark: { icon: Landmark, label: 'Bank' },
  coins: { icon: Coins, label: 'Coins' },
  home: { icon: House, label: 'Home' },
  gem: { icon: Gem, label: 'Gem' },
  wallet: { icon: Wallet, label: 'Wallet' },
  target: { icon: Target, label: 'Target' },
}

/** Decoration only: every colour is also named, and the tab is told by its name and icon. */
export const COLOURS: Record<TabColour, { label: string; swatch: string }> = {
  brand: { label: 'Blue', swatch: 'bg-[color:var(--brand)]' },
  gold: { label: 'Gold', swatch: 'bg-[color:var(--gold)]' },
  info: { label: 'Sky', swatch: 'bg-[color:var(--info)]' },
  neutral: { label: 'Grey', swatch: 'bg-[color:var(--text-tertiary)]' },
}

export const CUSTOM_KINDS: readonly TabKind[] = TAB_KINDS.filter((kind) => kind !== 'upstox')

const FIELD =
  'min-h-12 w-full rounded-[0.875rem] border border-input bg-card px-3 text-base outline-none focus-visible:ring-[3px] focus-visible:ring-ring/50'
const LABEL = 'text-sm font-semibold text-[color:var(--text-secondary)]'
const CHOICE =
  'inline-flex min-h-12 items-center gap-2 rounded-full border px-4 text-[0.9375rem] outline-none focus-visible:ring-[3px] focus-visible:ring-ring/50'
const ON =
  'border-2 border-[color:var(--brand)] bg-[color:var(--brand-tint)] font-bold text-[color:var(--brand)]'
const OFF =
  'border-[color:var(--border-strong)] bg-card font-medium text-[color:var(--text-secondary)]'

export function NameFields({
  name,
  nameAlt,
  onName,
  onNameAlt,
  disabled,
}: {
  name: string
  nameAlt: string
  onName: (value: string) => void
  onNameAlt: (value: string) => void
  disabled?: boolean
}) {
  return (
    <>
      <label className="flex flex-col gap-1">
        <span className={LABEL}>Name</span>
        <input
          className={FIELD}
          value={name}
          maxLength={40}
          disabled={disabled}
          onChange={(event) => onName(event.target.value)}
          autoComplete="off"
        />
        <span className="text-xs text-[color:var(--text-tertiary)]">
          {name.length} of 40 letters
        </span>
      </label>
      <label className="flex flex-col gap-1">
        <span className={LABEL}>Second name (optional)</span>
        <input
          className={FIELD}
          value={nameAlt}
          maxLength={NAME_MAX}
          disabled={disabled}
          onChange={(event) => onNameAlt(event.target.value)}
          autoComplete="off"
          placeholder="In Devanagari or Gujarati"
        />
      </label>
    </>
  )
}

export function KindChips({
  value,
  onChange,
  disabled,
}: {
  value: TabKind
  onChange: (kind: TabKind) => void
  disabled?: boolean
}) {
  return (
    <fieldset className="flex flex-col gap-1.5" disabled={disabled}>
      <legend className={LABEL}>Kind</legend>
      <div className="flex flex-wrap gap-2">
        {CUSTOM_KINDS.map((kind) => (
          <button
            key={kind}
            type="button"
            aria-pressed={value === kind}
            onClick={() => onChange(kind)}
            className={cn(CHOICE, value === kind ? ON : OFF)}
          >
            {value === kind && <Check aria-hidden="true" className="size-4" />}
            {TAB_KIND_LABELS[kind]}
          </button>
        ))}
      </div>
    </fieldset>
  )
}

export function IconChoices({
  value,
  onChange,
  disabled,
}: {
  value: TabIcon
  onChange: (icon: TabIcon) => void
  disabled?: boolean
}) {
  return (
    <fieldset className="flex flex-col gap-1.5" disabled={disabled}>
      <legend className={LABEL}>Icon</legend>
      <div className="flex flex-wrap gap-2">
        {TAB_ICONS.map((key) => {
          const { icon: Icon, label } = ICONS[key]
          return (
            <button
              key={key}
              type="button"
              aria-pressed={value === key}
              aria-label={label}
              onClick={() => onChange(key)}
              className={cn(
                'inline-flex size-12 items-center justify-center rounded-full border outline-none focus-visible:ring-[3px] focus-visible:ring-ring/50',
                value === key ? ON : OFF
              )}
            >
              <Icon aria-hidden="true" className="size-5" />
            </button>
          )
        })}
      </div>
    </fieldset>
  )
}

export function ColourChoices({
  value,
  onChange,
  disabled,
}: {
  value: TabColour
  onChange: (colour: TabColour) => void
  disabled?: boolean
}) {
  return (
    <fieldset className="flex flex-col gap-1.5" disabled={disabled}>
      <legend className={LABEL}>Colour (decoration only)</legend>
      <div className="flex flex-wrap gap-2">
        {TAB_COLOURS.map((key) => (
          <button
            key={key}
            type="button"
            aria-pressed={value === key}
            onClick={() => onChange(key)}
            className={cn(CHOICE, value === key ? ON : OFF)}
          >
            <span aria-hidden="true" className={cn('size-4 rounded-full', COLOURS[key].swatch)} />
            {COLOURS[key].label}
            {value === key && <Check aria-hidden="true" className="size-4" />}
          </button>
        ))}
      </div>
    </fieldset>
  )
}

export function PinnedField({
  value,
  onChange,
  disabled,
}: {
  value: boolean
  onChange: (pinned: boolean) => void
  disabled?: boolean
}) {
  return (
    <label className="flex min-h-12 items-center gap-3">
      <input
        type="checkbox"
        className="size-6 accent-[color:var(--brand)]"
        checked={value}
        disabled={disabled}
        onChange={(event) => onChange(event.target.checked)}
      />
      <span className="flex flex-col">
        <span className="text-base font-semibold">Show on the dashboard</span>
        <span className="text-xs text-[color:var(--text-tertiary)]">
          Up to 4 pinned tabs appear as rows on the dashboard.
        </span>
      </span>
    </label>
  )
}

export const PRIMARY =
  'inline-flex min-h-12 items-center justify-center gap-2 rounded-[0.875rem] bg-[color:var(--brand)] px-4 text-[0.9375rem] font-semibold text-white outline-none hover:bg-[color:var(--brand-hover)] focus-visible:ring-[3px] focus-visible:ring-ring/50 disabled:opacity-50'
export const SECONDARY =
  'inline-flex min-h-12 items-center justify-center gap-2 rounded-[0.875rem] border border-input bg-card px-4 text-[0.9375rem] font-semibold outline-none hover:bg-accent focus-visible:ring-[3px] focus-visible:ring-ring/50 disabled:opacity-50'
export const DANGER =
  'inline-flex min-h-12 items-center justify-center gap-2 rounded-[0.875rem] bg-destructive px-4 text-[0.9375rem] font-semibold text-white outline-none hover:bg-destructive/90 focus-visible:ring-[3px] focus-visible:ring-ring/50 disabled:opacity-50'
export { FIELD, LABEL }

export function ProblemLine({ problem }: { problem: { message: string } | null }) {
  if (!problem) return null
  return (
    <p
      role="alert"
      className="rounded-[0.875rem] border border-[color:var(--down)] bg-[color:var(--down-tint)] px-3 py-2 text-sm font-medium text-[color:var(--down)]"
    >
      {problem.message}
    </p>
  )
}
