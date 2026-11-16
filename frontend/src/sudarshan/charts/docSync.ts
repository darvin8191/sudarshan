/**
 * One chart document kept on this phone and, with the server's switch on, on the server as the
 * copy of record (M4.18): an instrument's drawings, or the phone chart's indicator set.
 *
 * - **The phone's own copy is the one the screen uses.** It is read and written as before, at
 *   once, and the server is settled with in the background. Offline, signed out or switched off,
 *   nothing else happens.
 * - **A record per document** (`sudarshan-charts-sync:<kind>:<key>` in local storage) holds the
 *   server revision this phone last agreed with and a hash of the text agreed, so a change made
 *   here since is told from the text itself, whatever wrote it.
 * - **Settling with the server.** The server moved on and nothing changed here: its copy is taken.
 *   Something changed here and the server did not move: it is sent, naming the revision it
 *   replaces. Both changed: the server's copy, written later on another device, is shown; this
 *   phone's is kept aside, and the page says so in words with a way to put this phone's back.
 *   Nothing newer is ever dropped without a sentence.
 * - **One write in flight per document**; a change made meanwhile is sent when it returns.
 *
 * Nothing here imports the chart engine: the owner of the document reads, adopts and judges it.
 */
import { type ChartsApi, type DocKind, overLimit, type Problem, sharedChartsApi } from './api'

export type NoticeTone = 'conflict' | 'kept' | 'local'

/** A sentence for the page, about one document. */
export interface SyncNotice {
  id: string
  tone: NoticeTone
  text: string
  action?: { label: string; run: () => void }
}

/** The sentences the page shows, newest last, one per document. */
export class NoticeBoard {
  private items: SyncNotice[] = []
  private readonly listeners = new Set<() => void>()

  readonly subscribe = (listener: () => void): (() => void) => {
    this.listeners.add(listener)
    return () => {
      this.listeners.delete(listener)
    }
  }

  /** The newest sentence, or null. */
  readonly latest = (): SyncNotice | null => this.items[this.items.length - 1] ?? null

  post(notice: SyncNotice): void {
    this.items = [...this.items.filter((item) => item.id !== notice.id), notice]
    this.tell()
  }

  clear(id: string): void {
    if (!this.items.some((item) => item.id === id)) return
    this.items = this.items.filter((item) => item.id !== id)
    this.tell()
  }

  private tell(): void {
    for (const listener of [...this.listeners]) listener()
  }
}

/** The board the phone chart shows. */
export const chartSyncNotices = new NoticeBoard()

/** FNV-1a over the text's code units: enough to tell a changed document from the one agreed. */
export function textHash(text: string): string {
  let hash = 0x811c9dc5
  for (let i = 0; i < text.length; i++) {
    hash ^= text.charCodeAt(i)
    hash = Math.imul(hash, 0x01000193)
  }
  return (hash >>> 0).toString(16).padStart(8, '0')
}

const RECORD_PREFIX = 'sudarshan-charts-sync:'
/** Writes in a row for one document before it waits for the next change: never a hot loop. */
const MAX_ROUNDS = 4
/** The server's refusals the page is told about; the rest are passing, and the next change sends again. */
const SAID: ReadonlySet<Problem['code']> = new Set([
  'too_big',
  'limit',
  'session',
  'record',
  'invalid',
])

type Store = Pick<Storage, 'getItem' | 'setItem' | 'removeItem'>

function localStore(): Store | null {
  try {
    return globalThis.localStorage ?? null
  } catch {
    return null
  }
}

/** The text a JSON document is compared by; null when it is not JSON. */
function canonical(text: string | null): string | null {
  if (text === null) return null
  try {
    return JSON.stringify(JSON.parse(text))
  } catch {
    return null
  }
}

/** What the owner of a document lends the sync: how to read, judge, adopt and name it. */
export interface DocHandlers {
  /** The subject of the sentences: "The drawings on SBIN", "Your indicators". */
  what: string
  /** The document on this phone as text, or null when there is none. */
  read(): string | null
  /** Whether the text holds anything worth keeping on the server (an empty set does not). */
  meaningful(text: string): boolean
  /** Put a document on this phone, and on screen if it is shown; the text now stored, or null. */
  adopt(body: unknown): string | null
}

export interface DocSyncDeps {
  api?: ChartsApi
  storage?: Store | null
  board?: NoticeBoard
}

export class DocSync {
  private readonly api: ChartsApi
  private readonly storage: Store | null
  private readonly board: NoticeBoard
  private readonly id: string
  private handlers: DocHandlers | null = null
  private flight: Promise<void> | null = null
  private again = false
  /** This phone's copy, kept aside after a conflict until the owner puts it back. */
  private mine: string | null = null

  readonly kind: DocKind
  readonly key: string

  constructor(kind: DocKind, key: string, deps: DocSyncDeps = {}) {
    this.kind = kind
    this.key = key
    this.api = deps.api ?? sharedChartsApi
    this.storage = deps.storage === undefined ? localStore() : deps.storage
    this.board = deps.board ?? chartSyncNotices
    this.id = `${kind}:${key}`
  }

  /** Lend the handlers of whoever shows the document now; the last one lent is used. */
  bind(handlers: DocHandlers): this {
    this.handlers = handlers
    return this
  }

  /** A write in flight, or this phone's copy kept aside: not to be forgotten. */
  get busy(): boolean {
    return this.flight !== null || this.mine !== null
  }

  /** Read the server's copy and settle with this phone's. Never throws. */
  async pull(): Promise<void> {
    try {
      if (!this.handlers || !(await this.api.enabled())) return
      const rec = this.record()
      const got = await this.api.read(this.kind, this.key, rec?.r)
      if (!got.ok) return this.refused(got.problem)
      await this.settle(got.doc)
    } catch {
      /* the phone's copy stands */
    }
  }

  /** This phone's copy changed: send it, after the write in flight if there is one. */
  changed(): void {
    if (this.flight) {
      this.again = true
      return
    }
    void (async () => {
      try {
        if (!this.handlers || !(await this.api.enabled())) return
        await this.send(this.record()?.r ?? 0)
      } catch {
        /* the phone's copy stands */
      }
    })()
  }

  /** After a conflict: put this phone's copy back and send it over the server's. */
  async keepMine(): Promise<void> {
    const mine = this.mine
    this.mine = null
    this.board.clear(this.id)
    if (mine === null || !this.handlers) return
    try {
      if (this.handlers.adopt(JSON.parse(mine)) === null) return
      await this.send(this.record()?.r ?? 0, true)
    } catch {
      /* the phone's copy stands */
    }
  }

  private record(): { r: number; h: string } | null {
    try {
      const saved = JSON.parse(this.storage?.getItem(RECORD_PREFIX + this.id) ?? 'null') as {
        r?: unknown
        h?: unknown
      } | null
      if (saved && Number.isSafeInteger(saved.r) && typeof saved.h === 'string')
        return { r: saved.r as number, h: saved.h }
    } catch {
      /* no record: settled as a first sight */
    }
    return null
  }

  private agree(revision: number, text: string): void {
    try {
      this.storage?.setItem(
        RECORD_PREFIX + this.id,
        JSON.stringify({ r: revision, h: textHash(text) })
      )
    } catch {
      /* storage full or blocked: settled again on the next open */
    }
  }

  private async settle(server: { revision: number; body?: unknown } | null): Promise<void> {
    const handlers = this.handlers as DocHandlers
    const local = handlers.read()
    const worth = local !== null && handlers.meaningful(local)
    if (server === null) {
      if (worth) await this.send(0)
      return
    }
    const rec = this.record()
    if (rec && server.revision === rec.r) {
      if (local !== null && textHash(local) !== rec.h) await this.send(rec.r)
      return
    }
    if (server.body === undefined) return // asked with a revision the server says it holds
    // Changed here: anything since the copy agreed (a clear included); at first sight, a copy
    // worth keeping. Either way only a copy that differs from the server's is a conflict.
    const changedHere = rec ? local !== null && textHash(local) !== rec.h : worth
    if (changedHere && canonical(local) !== JSON.stringify(server.body))
      return this.conflict(server, local)
    this.take(server.revision, server.body)
  }

  private take(revision: number, body: unknown): boolean {
    const kept = this.handlers?.adopt(body) ?? null
    if (kept !== null) this.agree(revision, kept)
    return kept !== null
  }

  /** Both changed: the server's copy is shown, this phone's kept aside, and the page told. */
  private conflict(server: { revision: number; body?: unknown }, local: string | null): void {
    if (!this.take(server.revision, server.body)) return
    this.mine = local
    this.board.post({
      id: this.id,
      tone: 'conflict',
      text: `${this.handlers?.what} were changed on another device. That copy is shown; the change made on this phone was not saved.`,
      action: { label: 'Use this phone’s copy', run: () => void this.keepMine() },
    })
  }

  private send(base: number, putBack = false): Promise<void> {
    if (this.flight) {
      this.again = true
      return this.flight
    }
    this.flight = this.rounds(base, putBack).finally(() => {
      this.flight = null
    })
    return this.flight
  }

  private async rounds(base: number, putBack: boolean): Promise<void> {
    let from = base
    for (let round = 0; round < MAX_ROUNDS; round++) {
      this.again = false
      const handlers = this.handlers as DocHandlers
      const text = handlers.read()
      if (text === null) return
      const rec = this.record()
      if (rec && rec.r === from && rec.h === textHash(text)) return // the copy agreed: nothing new
      if (overLimit(text))
        return this.say(
          'local',
          `${handlers.what} are larger than the server keeps, so they are kept on this phone only.`
        )
      const answer = await this.api.write(this.kind, this.key, from, JSON.parse(text))
      if (answer.ok) {
        this.agree(answer.doc.revision, text)
        from = answer.doc.revision
        if (putBack)
          this.say(
            'kept',
            `${handlers.what} from this phone are saved. Your other devices show them when they open them next.`
          )
        putBack = false
        if (!this.again && this.handlers?.read() === text) return
        continue
      }
      if ('stale' in answer) {
        if (answer.stale === null) {
          from = 0 // the server keeps nothing under this key any more: send it as new
          continue
        }
        return this.settleStale(answer.stale, text)
      }
      return this.refused(answer.problem)
    }
  }

  /** A write refused as stale: the newer copy is settled with what was sent. */
  private settleStale(server: { revision: number; body?: unknown }, sent: string): void {
    if (canonical(sent) === JSON.stringify(server.body)) {
      this.agree(server.revision, sent)
      return
    }
    this.conflict(server, this.handlers?.read() ?? sent)
  }

  private refused(problem: Problem): void {
    if (SAID.has(problem.code)) this.say('local', problem.message)
  }

  private say(tone: NoticeTone, text: string): void {
    this.board.post({ id: this.id, tone, text })
  }
}

/** How many documents' syncs the page keeps; one with a write in flight or a copy aside stays. */
const MAX_KEPT = 64
const syncs = new Map<string, DocSync>()

/**
 * The one sync of a document for the whole page, made on first use: a chart rebuilt, or the page
 * left and opened again, finds the write it had in flight instead of racing it with a second.
 */
export function documentSync(kind: DocKind, key: string): DocSync {
  const id = `${kind}:${key}`
  const found = syncs.get(id)
  if (found) {
    syncs.delete(id)
    syncs.set(id, found) // the newest is last
    return found
  }
  const made = new DocSync(kind, key)
  syncs.set(id, made)
  for (const [oldest, sync] of syncs) {
    if (syncs.size <= MAX_KEPT) break
    if (!sync.busy) syncs.delete(oldest)
  }
  return made
}

/** Forget every sync (tests only: a page keeps its syncs for its life). */
export function forgetDocumentSyncs(): void {
  syncs.clear()
}
