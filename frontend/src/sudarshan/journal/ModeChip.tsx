import { FlaskConical } from 'lucide-react'
import { cn } from '@/lib/utils'
import type { Mode } from './types'

/**
 * The journal's own mode: Live trades (the default) or Sandbox trades. The chip is a switch with
 * the flask icon and its word; on, it takes the sandbox tint and reads "Sandbox trades". A mode
 * is never mixed with the other: every read of the page carries it (design 6.2).
 */
export function ModeChip({ mode, onChange }: { mode: Mode; onChange: (mode: Mode) => void }) {
  const sandbox = mode === 'sandbox'
  return (
    <button
      type="button"
      role="switch"
      aria-checked={sandbox}
      data-mode={mode}
      onClick={() => onChange(sandbox ? 'live' : 'sandbox')}
      className={cn(
        'inline-flex min-h-12 items-center gap-1.5 rounded-full border px-3.5 text-[0.8125rem] font-bold outline-none focus-visible:ring-[3px] focus-visible:ring-ring/50',
        sandbox
          ? 'border-[color:var(--sandbox-border)] bg-[color:var(--sandbox-tint)] text-[color:var(--sandbox-ink)]'
          : 'border-[color:var(--border-strong)] bg-card text-[color:var(--text-secondary)]'
      )}
    >
      <FlaskConical aria-hidden="true" className="size-4" />
      {sandbox ? 'Sandbox trades' : 'Sandbox'}
    </button>
  )
}
