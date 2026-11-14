// SUDARSHAN P7 F4 (M7.36): a bonus or a split waiting for the owner's one tap (design p7 6.3),
// as plain data: which reading is on the card, and the entries that put it into a tab. The
// sentences, the quantities and both readings are the service's; nothing here works out a lot.
import type { ActivityInput, CaExpected, CaKind, CaWay } from '../types'

export const UPSTOX_SCOPE = 'upstox'

/** The reading on the card: the proposed one, or the other way round. */
export function wayOf(expected: CaExpected, other: boolean): CaWay | null {
  if (other) return expected.other
  if (expected.ratio_num === null || expected.ratio_den === null) return null
  return {
    ratio_num: expected.ratio_num,
    ratio_den: expected.ratio_den,
    words: expected.words,
    added_qty_u: expected.added_qty_u,
    after_qty_u: expected.after_qty_u,
    fraction: expected.fraction,
  }
}

/**
 * The entries that put a reading into a tab. The service gives them for the proposed reading; the
 * other way round swaps a split's two numbers, or a single bonus entry's quantity. With a bonus
 * over several accounts the other reading cannot be shared out here: null, and the card says to
 * add it in the tab by hand.
 */
export function entriesFor(
  kind: CaKind,
  expected: CaExpected,
  other: boolean
): ActivityInput[] | null {
  const entries = expected.entries ?? []
  if (entries.length === 0) return null
  if (!other) return entries
  const way = expected.other
  if (way === null) return null
  if (kind === 'split') {
    return entries.map((e) => ({ ...e, ratio_num: way.ratio_num, ratio_den: way.ratio_den }))
  }
  if (entries.length === 1 && way.added_qty_u !== undefined) {
    return [{ ...entries[0], qty_u: way.added_qty_u }]
  }
  return null
}

/** A ratio typed by hand, when Upstox gave none: two whole numbers above zero, or null. */
export function typedRatio(
  num: string,
  den: string
): { ratio_num: number; ratio_den: number } | null {
  const ok = (text: string) => /^[1-9]\d{0,5}$/.test(text.trim())
  return ok(num) && ok(den) ? { ratio_num: Number(num), ratio_den: Number(den) } : null
}
