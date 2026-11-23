/**
 * Notes typed in a symbol's sheet that the server did not take once the sheet had closed
 * (M8.6, v0.16.0 phone review L2): kept for this visit, so the symbol's sheet opens with the
 * note in its field again, ready to save, instead of the words being lost. In memory only: a
 * reload ends the visit. One per symbol, the newest kept; the sheet lets it go once it shows it.
 */
const drafts = new Map<string, string>()

export function keepNoteDraft(symbol: string, note: string): void {
  drafts.set(symbol, note)
}

export function noteDraft(symbol: string): string | undefined {
  return drafts.get(symbol)
}

export function dropNoteDraft(symbol: string): void {
  drafts.delete(symbol)
}

export function clearNoteDraftsForTests(): void {
  drafts.clear()
}
