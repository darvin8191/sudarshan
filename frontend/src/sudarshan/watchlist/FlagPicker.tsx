import { Ban } from 'lucide-react'
import { cn } from '@/lib/utils'
import { MarkGlyph } from './marks'
import { FLAG_IDS, flagName } from './v2/marks'
import type { FlagName } from './v2/types'

/**
 * Flag (M8.6 F8): a radio group of the five flags and None. Each is 48 dp with its glyph and its
 * name, so a flag is never told by its colour alone. One flag per symbol across every list.
 */
export function FlagPicker({
  value,
  names,
  disabled,
  onChange,
}: {
  value: number | null
  names: readonly FlagName[]
  disabled?: boolean
  onChange: (flag: number | null) => void
}) {
  const options: { id: number | null; label: string }[] = [
    ...FLAG_IDS.map((id) => ({ id: id as number | null, label: flagName(names, id) })),
    { id: null, label: 'None' },
  ]
  return (
    <div role="radiogroup" aria-label="Flag" className="grid grid-cols-2 gap-2">
      {options.map(({ id, label }) => {
        const on = id === value
        return (
          <label
            key={id ?? 'none'}
            className={cn(
              'flex min-h-12 items-center gap-2 rounded-[0.875rem] border px-3 text-left text-[0.9375rem] has-[:focus-visible]:ring-2 has-[:focus-visible]:ring-ring',
              disabled && 'opacity-50',
              on
                ? 'border-primary bg-[color:var(--primary-tint)] font-bold text-primary'
                : 'border-input bg-card font-semibold'
            )}
          >
            <input
              type="radio"
              name="sd-flag"
              className="sr-only"
              checked={on}
              disabled={disabled}
              onChange={() => onChange(id)}
            />
            {id === null ? (
              <Ban aria-hidden="true" className="size-4 shrink-0" />
            ) : (
              <MarkGlyph mark={id} />
            )}
            <span className="min-w-0 truncate">{label}</span>
          </label>
        )
      })}
    </div>
  )
}
