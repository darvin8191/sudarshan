import type { RefKind } from './types'

/**
 * What the list menu may offer for the list on screen (M8.6, "the list menu acts only on the list
 * on screen"). Written once, keyed on the open list's ref kind, so a kind added later offers
 * nothing that edits a list until a line is added here: it cannot fall through to the own list
 * behind it.
 */
export interface ListMenu {
  /** 'list' renames the own list, 'flag' renames the flag on screen, null offers no rename. */
  rename: 'list' | 'flag' | null
  /** Reorder or remove. */
  edit: boolean
  /** Delete list. */
  remove: boolean
}

const OWN: ListMenu = { rename: 'list', edit: true, remove: true }
const FLAG: ListMenu = { rename: 'flag', edit: false, remove: false }
const NOTHING: ListMenu = { rename: null, edit: false, remove: false }

/** The ref kind of a ref ("flag:2" is 'flag'); an unknown prefix is returned as it is. */
export function kindOfRef(ref: string | null): RefKind | string {
  return ref === null ? 'list' : ref.slice(0, ref.indexOf(':') < 0 ? ref.length : ref.indexOf(':'))
}

export function menuFor(kind: RefKind | string): ListMenu {
  switch (kind) {
    case 'list':
      return OWN
    case 'flag':
      return FLAG
    default: // preset, ranked, and any kind not written here yet
      return NOTHING
  }
}

/** F15 (M8.12): what the list menu offers for TradingView, for the list on screen. */
export interface TvMenu {
  /** "Import into this list": only an own list. (A new list can be made from any screen.) */
  importHere: boolean
  /** Export for TradingView: every kind the `tv_export` op reads; a kind not written here has none. */
  export: boolean
}

export function tvFor(kind: RefKind | string): TvMenu {
  switch (kind) {
    case 'list':
      return { importHere: true, export: true }
    case 'preset':
    case 'ranked':
    case 'flag':
      return { importHere: false, export: true }
    default:
      return { importHere: false, export: false }
  }
}
