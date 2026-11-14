/**
 * SUDARSHAN (SCR-P item 9): what a new pane of a grid opens with.
 *
 * A grid's panes each keep their own instrument, so a pane that has shown one before comes back
 * with it. A pane that has never shown anything used to open on the terminal's default, or on
 * nothing when the default could not be read, and a workspace's new panes copied the first pane
 * whichever pane was in use. Both now take the pane the trader was on: the focused one.
 */

export interface SymbolRef {
  symbol: string
  exchange: string
}

/**
 * A pane's instrument from the key the page keeps for it (`EXCHANGE:SYMBOL`). An exchange has no
 * colon in its name and a symbol may, so the first colon is the divide. A computed chart
 * (`NIFTY/RELIANCE`) has no exchange, and its key starts with the colon.
 */
export function parseSymbolKey(key: string | null | undefined): SymbolRef | null {
  if (!key) return null
  const at = key.indexOf(':')
  if (at < 0 || at === key.length - 1) return null
  return { exchange: key.slice(0, at), symbol: key.slice(at + 1) }
}

/**
 * The pane a new pane takes after: the focused one, else the first. `panes` are the panes as they
 * stand before the layout changes.
 */
export function seedPane<T extends { id: string }>(panes: readonly T[], focused: string): T {
  return panes.find((pane) => pane.id === focused) ?? panes[0]
}

/**
 * The panes of the next layout: each keeps what it has, and each new one is the focused pane
 * again under its own id.
 */
export function panesForLayout<T extends { id: string }>(
  panes: readonly T[],
  focused: string,
  count: number
): T[] {
  const seed = seedPane(panes, focused)
  return Array.from({ length: count }, (_, index) => ({
    ...(panes[index] ?? seed),
    id: `p${index}`,
  }))
}
