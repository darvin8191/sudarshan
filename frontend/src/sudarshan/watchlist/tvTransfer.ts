import type { QueryClient } from '@tanstack/react-query'
import { WATCHLISTS_KEY } from './useWatchlists'
import { callOp } from './v2/api'
import { writeThenRead } from './v2/arrange'
import { LIMITS, type TvDone, type TvExport, type TvPreview } from './v2/types'
import { LISTS_KEY, viewKey } from './v2/useListView'

/**
 * F15 (M8.12): what the TradingView import and export sheets do, apart from drawing. The words that
 * matter come from the server as sent (`tv_preview`, `tv_import`, `tv_export`); the one sentence of
 * the page's own is the size limit, said before a file is read or anything is sent, in the server's
 * own wording so the two never disagree.
 */
export const TV_TOO_BIG = `That file is larger than ${LIMITS.tv_file_bytes / 1024} KiB. Export a shorter list and try again.`

/** A file's name without its extension: the new list's name, which the owner can edit. */
export function listNameOf(fileName: string): string {
  return fileName.replace(/\.[^./\\]*$/, '').trim()
}

/** What the text weighs on the wire (UTF-8), which is what the server's limit counts. */
export const bytesOf = (text: string): number => new TextEncoder().encode(text).length

/** The sentence refusing text that is too long, or null. Checked before a read and before a send. */
export function tooBig(size: number): string | null {
  return size > LIMITS.tv_file_bytes ? TV_TOO_BIG : null
}

/** Read a file in the browser. A file over the limit is refused with the sentence, never read. */
export function readFileText(file: File): Promise<string> {
  const said = tooBig(file.size)
  if (said) return Promise.reject(new Error(said))
  return new Promise((resolve, reject) => {
    const reader = new FileReader()
    reader.onload = () => resolve(typeof reader.result === 'string' ? reader.result : '')
    reader.onerror = () => reject(new Error('That file could not be read. Pick it again.'))
    reader.readAsText(file)
  })
}

/** Where an import goes: a new list by name, or the open own list on the version it was read at. */
export type Target = { name: string } | { list: number; version: string }

export function previewTv(
  text: string,
  target: { name?: string } | { list: number }
): Promise<TvPreview> {
  const said = tooBig(bytesOf(text))
  if (said) return Promise.reject(new Error(said))
  return callOp('tv_preview', { text, ...target })
}

/**
 * Import what the preview said, on its hash. The lists, the open list's view and the All lists
 * answer are read again whatever the answer, so a refusal shows the lists as the server has them.
 * Resolves to the answer, or the server's sentence and its code.
 */
export function importTv(client: QueryClient, text: string, hash: string, target: Target) {
  const keys =
    'list' in target
      ? [WATCHLISTS_KEY, LISTS_KEY, viewKey(`list:${target.list}`)]
      : [WATCHLISTS_KEY, LISTS_KEY]
  return writeThenRead<TvDone>(client, keys, () => callOp('tv_import', { text, hash, ...target }))
}

/** The export of the list on screen: nothing is written. Throws the server's sentence. */
export const exportTv = (ref: string, signal?: AbortSignal): Promise<TvExport> =>
  callOp('tv_export', { ref }, signal)

/** Give the owner the file: a download of `text` named `filename`. */
export function downloadText(text: string, filename: string): void {
  const url = URL.createObjectURL(new Blob([text], { type: 'text/plain;charset=utf-8' }))
  const link = document.createElement('a')
  link.href = url
  link.download = filename
  document.body.appendChild(link)
  link.click()
  link.remove()
  setTimeout(() => URL.revokeObjectURL(url), 0)
}
