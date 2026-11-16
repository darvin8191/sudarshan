/**
 * The terminal's saved layouts and indicator templates on the server (M4.18, T7b): a storage
 * for the workspace tier's repository, wrapped around this browser's own IndexedDB storage.
 *
 * - **The repository sees only this browser's catalogue**, whose revision moves by exactly one a
 *   change as the tier requires. The server's copy is settled with inside `read`, before the
 *   repository sees anything, and a write is sent on after it has landed here. So the tier's own
 *   revision checks, conflicts between two tabs included, work exactly as before.
 * - **Switched off, offline or refused**, this is the IndexedDB storage and nothing more.
 * - **The terminal never waits for the switch** (review L2). While this visit has not heard the
 *   server's answer, a load gives this browser's catalogue at once and a save lands here at once;
 *   the switch is asked in the background. Only a browser that saw the switch on at its last
 *   visit waits for the answer, because there the server's copy is the one to open. When a late
 *   answer says on, `whenServerKeeps` listeners hear it and the hook loads again to settle.
 * - **A record per catalogue** (`sudarshan-charts-sync:workspace:<key>`) holds the server's
 *   revision, this browser's revision and the time at their last agreement.
 *   - The server moved and nothing changed here: its catalogue is written here as the next one.
 *   - Changed here and the server did not move: sent, naming the revision it replaces.
 *   - Both changed (saved here offline while another device saved), or both have layouts at
 *     first sight: the server's catalogue is kept, with every layout and template made or changed
 *     here since the last agreement added to it, and the terminal says so in words. One changed
 *     on both is the other device's, and the sentence names it.
 * - **What it says** waits in `notice()` until the catalogue hook reads it after a load.
 */
import {
  type IndexedDbWorkspaceStorage,
  type IndicatorTemplateDocument,
  parseWorkspaceCatalog,
  type WorkspaceCatalog,
  type WorkspaceDocument,
} from 'openalgo-charts/workspace'
import { type ChartsApi, overLimit, type Problem, sharedChartsApi } from './api'

const RECORD_PREFIX = 'sudarshan-charts-sync:workspace:'
/** The workspace tier keeps at most this many layouts, and as many templates. */
const MAX_LIST = 100
/** The server's refusals the terminal is told about; the rest pass, and the next save sends again. */
const SAID: ReadonlySet<Problem['code']> = new Set([
  'too_big',
  'limit',
  'session',
  'record',
  'invalid',
])

export interface SyncedWorkspaceStorage extends IndexedDbWorkspaceStorage {
  /** What to tell the owner about the last settle, once; null when there is nothing to say. */
  notice(): string | null
  /** Whether the server keeps the layouts, as far as this visit knows: where they are saved. */
  serverKeeps(): boolean
  /** Hear a switch found on after a load already gave this browser's copy; returns the undo. */
  whenServerKeeps(listener: () => void): () => void
}

type Store = Pick<Storage, 'getItem' | 'setItem'>
/** Whether this browser saw the switch on when it last heard the server's answer. */
const SEEN_ON_KEY = 'sudarshan-charts-sync:seen-on'
type Saved = WorkspaceDocument | IndicatorTemplateDocument
interface Agreement {
  s: number
  l: number
  at: number
}

function localStore(): Store | null {
  try {
    return globalThis.localStorage ?? null
  } catch {
    return null
  }
}

function parsed(raw: unknown): WorkspaceCatalog | null {
  try {
    return raw === null || raw === undefined ? null : parseWorkspaceCatalog(raw)
  } catch {
    return null
  }
}

function names(items: readonly string[]): string {
  const quoted = items.slice(0, 3).map((name) => `“${name}”`)
  const more = items.length > 3 ? ` and ${items.length - 3} more` : ''
  return quoted.length > 1
    ? `${quoted.slice(0, -1).join(', ')} and ${quoted[quoted.length - 1]}${more}`
    : `${quoted[0] ?? ''}${more}`
}

/**
 * The server's catalogue with what this browser made or changed since `since` added: a document
 * changed on both sides stays the server's (named in `lost`), one changed only here replaces the
 * server's, one made here is added while the list has room. Deletions made on the other device
 * stand. The rest of the catalogue (the open layout, the recent list, autosave) is the server's.
 */
export function mergeCatalogs(
  theirs: WorkspaceCatalog,
  mine: WorkspaceCatalog,
  since: number
): { merged: WorkspaceCatalog; kept: string[]; lost: string[] } {
  const kept: string[] = []
  const lost: string[] = []
  const merge = <T extends Saved>(server: readonly T[], here: readonly T[]): T[] => {
    const out = [...server]
    for (const doc of here) {
      if (doc.updatedAt <= since) continue
      const at = out.findIndex((item) => item.id === doc.id)
      if (at >= 0 && out[at].updatedAt > since) {
        if (JSON.stringify(out[at]) !== JSON.stringify(doc)) lost.push(doc.name)
      } else if (at >= 0) {
        out[at] = doc
        kept.push(doc.name)
      } else if (out.length < MAX_LIST) {
        out.push(doc)
        kept.push(doc.name)
      } else lost.push(doc.name)
    }
    return out
  }
  const merged = {
    ...theirs,
    workspaces: merge(theirs.workspaces, mine.workspaces),
    templates: merge(theirs.templates, mine.templates),
  }
  return { merged, kept, lost }
}

export function syncedWorkspaceStorage(
  local: IndexedDbWorkspaceStorage,
  options: { api?: ChartsApi; storage?: Store | null; now?: () => number } = {}
): SyncedWorkspaceStorage {
  const api = options.api ?? sharedChartsApi
  const storage = options.storage === undefined ? localStore() : options.storage
  const now = options.now ?? Date.now
  let pending: string | null = null
  const late = new Set<() => void>()

  const seenOn = () => {
    try {
      return storage?.getItem(SEEN_ON_KEY) === '1'
    } catch {
      return false
    }
  }
  const remember = (on: boolean) => {
    try {
      if (seenOn() !== on) storage?.setItem(SEEN_ON_KEY, on ? '1' : '0')
    } catch {
      /* storage full or blocked: asked the slow way never, the quick way always */
    }
  }
  /**
   * The switch without waiting, where that is safe: the visit's own answer; else null (ask and
   * wait) for a client that cannot say, or for a browser that last saw it on.
   */
  function quick(): boolean | null {
    if (!api.state) return null
    const known = api.state()
    if (known !== null) {
      remember(known)
      return known
    }
    if (seenOn()) return null
    // Not known, and off when last heard: this browser's copy now, the answer afterwards.
    void api.enabled().then(
      (on) => {
        if (api.state?.() !== null) remember(on)
        if (on) for (const listener of [...late]) listener()
      },
      () => {}
    )
    return false
  }
  async function on(): Promise<boolean> {
    const known = quick()
    if (known !== null) return known
    const answer = await api.enabled()
    if (!api.state || api.state() !== null) remember(answer)
    return answer
  }

  const keyOf = (namespace: string) => encodeURIComponent(namespace)
  function record(key: string): Agreement | null {
    try {
      const saved = JSON.parse(storage?.getItem(RECORD_PREFIX + key) ?? 'null') as Agreement | null
      if (saved && [saved.s, saved.l, saved.at].every(Number.isSafeInteger)) return saved
    } catch {
      /* no record: settled as a first sight */
    }
    return null
  }
  function agree(key: string, s: number, l: number): void {
    try {
      storage?.setItem(RECORD_PREFIX + key, JSON.stringify({ s, l, at: now() }))
    } catch {
      /* storage full or blocked: settled again on the next load */
    }
  }
  const refused = (problem: Problem) => {
    if (SAID.has(problem.code)) pending = problem.message // each says this copy is kept here
  }

  /** Send this browser's catalogue over the server revision `base`; true once it is agreed. */
  async function send(key: string, base: number, catalog: WorkspaceCatalog): Promise<boolean> {
    if (overLimit(JSON.stringify(catalog))) {
      pending =
        'Saved in this browser only: the saved layouts are larger than the server keeps (5 MB).'
      return false
    }
    const answer = await api.write('workspace', key, base, catalog)
    if (answer.ok) agree(key, answer.doc.revision, catalog.revision)
    else if (!('stale' in answer)) refused(answer.problem)
    return answer.ok // a stale one is settled by the next read, which the hook asks for next
  }

  /** Put `next` here as the revision after `mine`'s; this browser's catalogue as it is then. */
  async function store(namespace: string, mine: WorkspaceCatalog | null, next: WorkspaceCatalog) {
    const base = mine?.revision ?? 0
    const written = { ...next, revision: base + 1 }
    await local.write(namespace, written, base)
    return written
  }

  async function settle(namespace: string, raw: unknown): Promise<unknown> {
    const mine = parsed(raw)
    if (raw !== null && raw !== undefined && mine === null) return raw // never replaces a broken one
    const key = keyOf(namespace)
    const rec = record(key)
    const got = await api.read('workspace', key, rec?.s)
    if (!got.ok) {
      refused(got.problem)
      return raw
    }
    const server = got.doc
    if (server === null) {
      if (mine) await send(key, 0, mine)
      return raw
    }
    if (rec && server.revision === rec.s) {
      if (mine && mine.revision !== rec.l) await send(key, rec.s, mine)
      return raw
    }
    const theirs = parsed(server.body)
    if (!theirs) {
      pending =
        'The layouts saved on the server could not be read by this version of the app, so this browser’s own are shown. Update the app.'
      return raw
    }
    const has = (c: WorkspaceCatalog) => c.workspaces.length + c.templates.length > 0
    const changedHere = rec ? (mine?.revision ?? 0) !== rec.l : !!mine && has(mine)
    if (!mine || !changedHere) {
      const written = await store(namespace, mine, theirs)
      agree(key, server.revision, written.revision)
      return written
    }
    const { merged, kept, lost } = mergeCatalogs(theirs, mine, rec?.at ?? 0)
    const written = await store(namespace, mine, parsed(merged) ?? theirs)
    if (kept.length) await send(key, server.revision, written)
    else agree(key, server.revision, written.revision)
    pending = lost.length
      ? `Layouts were changed on another device too. ${names(lost)} kept the other device’s version, and the change made in this browser was not saved.`
      : kept.length
        ? `Layouts saved on another device were brought in, and ${names(kept)} from this browser were kept beside them.`
        : null
    return written
  }

  return {
    async read(namespace) {
      const raw = await local.read(namespace)
      try {
        if (!(await on())) return raw
        return await settle(namespace, raw)
      } catch {
        return local.read(namespace) // the server never fails a load: this browser's copy
      }
    },
    async write(namespace, catalog, expectedRevision, writeOptions) {
      await local.write(namespace, catalog, expectedRevision, writeOptions)
      try {
        if (!(await on())) return
        await send(keyOf(namespace), record(keyOf(namespace))?.s ?? 0, catalog)
      } catch {
        /* landed here; sent again with the next save or load */
      }
    },
    close: () => local.close(),
    notice() {
      const said = pending
      pending = null
      return said
    },
    serverKeeps: () => (api.state ? api.state() === true : seenOn()),
    whenServerKeeps(listener) {
      late.add(listener)
      return () => {
        late.delete(listener)
      }
    },
  }
}

/** The sentence a storage has for the owner after a load, if it has one (any other storage: none). */
export function storageNotice(storage: unknown): string | null {
  const notice = (storage as Partial<SyncedWorkspaceStorage> | null)?.notice
  return typeof notice === 'function' ? notice.call(storage) : null
}

/** Whether a storage keeps the layouts on the server now (any other storage: this browser). */
export function storageOnServer(storage: unknown): boolean {
  const keeps = (storage as Partial<SyncedWorkspaceStorage> | null)?.serverKeeps
  return typeof keeps === 'function' ? keeps.call(storage) === true : false
}

/** Hear a storage find its switch on after a load; the undo (any other storage: nothing). */
export function whenStorageOnServer(storage: unknown, listener: () => void): () => void {
  const hear = (storage as Partial<SyncedWorkspaceStorage> | null)?.whenServerKeeps
  return typeof hear === 'function' ? hear.call(storage, listener) : () => {}
}
