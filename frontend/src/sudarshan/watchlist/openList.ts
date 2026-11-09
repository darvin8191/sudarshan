/** The list that was open last on this device: a convenience, so it lives in this browser only. */
export const OPEN_LIST_KEY = 'sudarshan-watchlist-open'

/** Make `id` the list the Watchlist page opens with (a link from the Screener's dashboard). */
export function rememberOpenList(id: number): void {
  try {
    localStorage.setItem(OPEN_LIST_KEY, String(id))
  } catch {
    // Storage refused (a private window): the link still opens the page.
  }
}
