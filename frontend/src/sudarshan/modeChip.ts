import type { AppMode } from '@/stores/themeStore'

/**
 * The Live / Analyze badge as the D look's LIVE chip: a tint, a dark text of the
 * same hue (6.1:1 and 8.9:1 on it), an edge and a dot, in bold tracked caps.
 * The word carries the mode, so the colour is never the only sign. Utilities
 * rather than a class, so they merge over the Badge variant's own colours
 * instead of losing to them.
 */
export const MODE_CHIP: Record<AppMode, string> = {
  live: 'border-[color:var(--live-border)] bg-[color:var(--live-tint)] text-[color:var(--live-ink)]',
  analyzer:
    'border-[color:var(--sandbox-border)] bg-[color:var(--sandbox-tint)] text-[color:var(--sandbox-ink)]',
}

/** The chip's dot. Live keeps its green in colour-blind mode: it is a state, not a direction. */
export const MODE_DOT: Record<AppMode, string> = {
  live: 'bg-[color:var(--live-ink)]',
  analyzer: 'bg-[color:var(--sandbox)]',
}

/** Shape and type shared by both modes. */
export const MODE_CHIP_BASE = 'rounded-full font-bold uppercase tracking-[0.06em] gap-1.5'
