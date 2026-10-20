import type { AppMode } from '@/stores/themeStore'

/**
 * The Live / Analyze badge as the spec's status chips: a tint, a dark text of
 * the same hue (8.2:1 and 8.9:1) and an edge. Utilities rather than a class, so
 * they merge over the Badge variant's own colours instead of losing to them.
 */
export const MODE_CHIP: Record<AppMode, string> = {
  live: 'border-[color:var(--live-border)] bg-[color:var(--live-tint)] text-[color:var(--live-ink)]',
  analyzer:
    'border-[color:var(--sandbox-border)] bg-[color:var(--sandbox-tint)] text-[color:var(--sandbox-ink)]',
}
