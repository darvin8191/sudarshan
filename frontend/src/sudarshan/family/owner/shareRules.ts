// SUDARSHAN P6 D7b (M8.15, M7.37): which share rows a switch stands for. A share row names a kind
// (this page makes watchlist rows only; review H1 took the book rows away), a ref (the list's id), and a target: one member, or
// every member (no member and no role).
//
// CONTRACT GAP, assumed here: the contract does not say how "every member" is written. This page
// writes it as a row with `member_id: null` and `role: null`, and reads such a row as covering all
// members. A row with a role is not made by this page, and is shown as it stands (never changed).
import type { Share, ShareKind } from '../types'

export type Target = number | 'all'

/** The row for exactly this kind, ref and target. */
export function rowFor(
  shares: readonly Share[],
  kind: ShareKind,
  ref: string,
  target: Target
): Share | undefined {
  return shares.find(
    (s) =>
      s.kind === kind &&
      s.ref === ref &&
      s.role === null &&
      (target === 'all' ? s.member_id === null : s.member_id === target)
  )
}

/** Whether a member sees it only because it is shared with every member. */
export function coveredByAll(shares: readonly Share[], kind: ShareKind, ref: string): boolean {
  return rowFor(shares, kind, ref, 'all') !== undefined
}
