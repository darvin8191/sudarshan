/**
 * M8.19: Historify keeps its own list of symbols to download, and upstream calls it the
 * "watchlist". SUDARSHAN's watchlists are something else (the lists on the bottom bar), so on
 * Historify's page this list is the Download list. Upstream's server files are not changed: the
 * sentences they send back (a toast after an add or a removal, a job's kind) pass through
 * `downloadListWords` before they are shown. API paths, the tab's value and `download_source` keep
 * upstream's word.
 */

/** The page's name for the list, as a tab label and a screen reader say it. */
export const DOWNLOAD_LIST = 'Download list'

const CAPITAL = /\bWatchlist\b/g
const LOWER = /\bwatchlist\b/g

/** A server sentence with Historify's "watchlist" said as the Download list; whole words only. */
export function downloadListWords(text: string): string {
  return text.replace(CAPITAL, DOWNLOAD_LIST).replace(LOWER, 'download list')
}
