/**
 * One chart document settled between this phone and the server's copy of record (M4.18): taken
 * when only the server moved, sent when only this phone changed, and a conflict when both did,
 * with the newer copy shown, this phone's kept aside and the words to put it back. Switched off,
 * nothing is asked. A fake server stands in; nothing reaches a network.
 */
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { describe, expect, it } from 'vitest'
import type { ChartsApi, DocKind, Problem, ServerDoc, WriteResult } from './api'
import { DocSync, documentSync, NoticeBoard, textHash } from './docSync'

const KEY = 'NSE:SBIN'

class FakeServer implements ChartsApi {
  on = true
  next = 1_794_000_000_000
  readonly docs = new Map<string, { revision: number; body: unknown }>()
  readonly reads: Array<{ key: string; have?: number }> = []
  readonly writes: Array<{ key: string; base: number; body: unknown }> = []
  /** Holds every write until released, to see what happens while one is in flight. */
  gate: Promise<void> | null = null
  problem: Problem | null = null

  async enabled() {
    return this.on
  }

  async read(kind: DocKind, key: string, have?: number) {
    this.reads.push({ key, have })
    const doc = this.docs.get(key)
    if (!doc) return { ok: true as const, doc: null }
    const out: ServerDoc = { kind, key, revision: doc.revision, updated_ms: 1 }
    if (have !== doc.revision) out.body = structuredClone(doc.body)
    return { ok: true as const, doc: out }
  }

  async write(kind: DocKind, key: string, base: number, body: unknown): Promise<WriteResult> {
    this.writes.push({ key, base, body: structuredClone(body) })
    await this.gate
    if (this.problem) return { ok: false, problem: this.problem }
    const doc = this.docs.get(key)
    if ((doc?.revision ?? 0) !== base)
      return {
        ok: false,
        stale: doc ? { kind, key, revision: doc.revision, updated_ms: 1, body: doc.body } : null,
      }
    const revision = ++this.next
    this.docs.set(key, { revision, body: structuredClone(body) })
    return { ok: true, doc: { kind, key, revision, updated_ms: 1 } }
  }

  /** Another device saves this document. */
  saves(body: unknown, key = KEY) {
    this.docs.set(key, { revision: ++this.next, body })
  }
}

function memoryStorage() {
  const items = new Map<string, string>()
  return {
    items,
    getItem: (k: string) => items.get(k) ?? null,
    setItem: (k: string, v: string) => void items.set(k, v),
    removeItem: (k: string) => void items.delete(k),
  }
}

const doc = (...ids: string[]) => ({ version: 2, drawings: ids.map((id) => ({ id })) })
const text = (...ids: string[]) => JSON.stringify(doc(...ids))

/** A phone holding one document, with a sync over a fake server. */
function phone(server = new FakeServer(), storage = memoryStorage(), board = new NoticeBoard()) {
  const state = { local: null as string | null, adopted: [] as unknown[] }
  const make = () =>
    new DocSync('drawings', KEY, { api: server, storage, board }).bind({
      what: 'The drawings on SBIN',
      read: () => state.local,
      meaningful: (t) => (JSON.parse(t).drawings?.length ?? 0) > 0,
      adopt: (body) => {
        state.adopted.push(body)
        state.local = JSON.stringify(body)
        return state.local
      },
    })
  return { server, storage, board, state, sync: make(), make }
}

const settled = () => new Promise((done) => setTimeout(done, 0))

describe('switched off', () => {
  it('asks the server nothing and leaves this phone’s copy alone', async () => {
    const { server, state, sync } = phone()
    server.on = false
    state.local = text('a')
    await sync.pull()
    sync.changed()
    await settled()
    expect(server.reads).toEqual([])
    expect(server.writes).toEqual([])
    expect(state.local).toBe(text('a'))
  })
})

describe('settling with the server', () => {
  it('sends a phone’s drawings the server has not seen, and then agrees with them', async () => {
    const { server, state, sync } = phone()
    state.local = text('a')
    await sync.pull()
    expect(server.writes).toEqual([{ key: KEY, base: 0, body: doc('a') }])
    sync.changed() // nothing new since
    await settled()
    expect(server.writes).toHaveLength(1)
  })

  it('sends nothing for a phone that has nothing to keep', async () => {
    const { server, state, sync } = phone()
    state.local = text()
    await sync.pull()
    expect(server.writes).toEqual([])
  })

  it('takes the server’s copy when only the server moved', async () => {
    const { server, state, sync, storage } = phone()
    state.local = text('a')
    await sync.pull()
    server.saves(doc('a', 'tablet'))
    await sync.pull()
    expect(state.local).toBe(text('a', 'tablet'))
    expect(server.writes).toHaveLength(1)
    const record = JSON.parse(storage.items.get(`sudarshan-charts-sync:drawings:${KEY}`) ?? '')
    expect(record).toEqual({ r: server.docs.get(KEY)?.revision, h: textHash(text('a', 'tablet')) })
  })

  it('asks with the revision it holds, and takes nothing when the server holds the same', async () => {
    const { server, state, sync } = phone()
    state.local = text('a')
    await sync.pull()
    await sync.pull()
    expect(server.reads[1].have).toBe(server.docs.get(KEY)?.revision)
    expect(server.writes).toHaveLength(1)
  })

  it('sends this phone’s change when only this phone changed', async () => {
    const { server, state, sync } = phone()
    state.local = text('a')
    await sync.pull()
    const agreed = server.docs.get(KEY)?.revision
    state.local = text('a', 'b') // changed while offline, say
    await sync.pull()
    expect(server.writes[1]).toEqual({ key: KEY, base: agreed, body: doc('a', 'b') })
  })

  it('takes a copy that is the same as this phone’s at first sight, with nothing to say', async () => {
    const server = new FakeServer()
    server.saves(doc('a'))
    const { state, sync, board } = phone(server)
    state.local = text('a')
    await sync.pull()
    expect(board.latest()).toBeNull()
    expect(server.writes).toEqual([])
  })
})

describe('a conflict', () => {
  it('shows the newer copy, says so in words, and puts this phone’s back when asked', async () => {
    const { server, state, sync, board } = phone()
    state.local = text('a')
    await sync.pull()
    server.saves(doc('a', 'tablet')) // another device saved
    state.local = text('a', 'phone') // and this phone changed too
    await sync.pull()
    expect(state.local).toBe(text('a', 'tablet'))
    const notice = board.latest()
    expect(notice?.tone).toBe('conflict')
    expect(notice?.text).toBe(
      'The drawings on SBIN were changed on another device. That copy is shown; the change made on this phone was not saved.'
    )
    expect(notice?.action?.label).toBe('Use this phone’s copy')

    notice?.action?.run()
    await settled()
    expect(state.local).toBe(text('a', 'phone'))
    expect(server.docs.get(KEY)?.body).toEqual(doc('a', 'phone'))
    expect(board.latest()?.tone).toBe('kept')
    expect(board.latest()?.text).toContain('from this phone are saved')
  })

  it('at first sight, a phone’s own drawings are never dropped without a word', async () => {
    const server = new FakeServer()
    server.saves(doc('tablet'))
    const { state, sync, board } = phone(server)
    state.local = text('phone')
    await sync.pull()
    expect(state.local).toBe(text('tablet'))
    expect(board.latest()?.tone).toBe('conflict')
  })

  it('counts a phone’s clear as its change', async () => {
    const { server, state, sync, board } = phone()
    state.local = text('a')
    await sync.pull()
    server.saves(doc('a', 'tablet'))
    state.local = text() // every drawing deleted here
    await sync.pull()
    expect(board.latest()?.tone).toBe('conflict')
  })

  it('a write refused as stale shows the newer copy and keeps this phone’s aside', async () => {
    const { server, state, sync, board } = phone()
    state.local = text('a')
    await sync.pull()
    server.saves(doc('a', 'tablet'))
    state.local = text('a', 'phone')
    sync.changed()
    await settled()
    await settled()
    expect(server.writes.at(-1)?.body).toEqual(doc('a', 'phone'))
    expect(state.local).toBe(text('a', 'tablet'))
    expect(board.latest()?.action?.label).toBe('Use this phone’s copy')
  })
})

describe('one write at a time', () => {
  it('sends a change made during a write after it returns, from the revision it made', async () => {
    const { server, state, sync } = phone()
    state.local = text('a')
    let release!: () => void
    server.gate = new Promise<void>((done) => {
      release = done
    })
    const first = sync.pull()
    await settled()
    state.local = text('a', 'b')
    sync.changed()
    state.local = text('a', 'b', 'c')
    sync.changed()
    expect(server.writes).toHaveLength(1)
    server.gate = null
    release()
    await first
    await settled()
    expect(server.writes).toHaveLength(2)
    expect(server.writes[1].body).toEqual(doc('a', 'b', 'c'))
    expect(server.docs.get(KEY)?.body).toEqual(doc('a', 'b', 'c'))
  })
})

describe('what the phone is told', () => {
  it.each([
    ['too_big', true],
    ['limit', true],
    ['session', true],
    ['record', true],
    ['busy', false],
    ['offline', false],
    ['failed', false],
    ['off', false],
  ] as const)('a %s refusal is told: %s', async (code, told) => {
    const { server, state, sync, board } = phone()
    server.problem = { code, message: `The server's words for ${code}.` }
    state.local = text('a')
    await sync.pull()
    expect(board.latest()?.text ?? null).toBe(told ? `The server's words for ${code}.` : null)
  })

  it('a document past the server’s size is kept on this phone and not sent', async () => {
    const { server, state, sync, board } = phone()
    state.local = JSON.stringify({ version: 2, drawings: [{ id: 'x'.repeat(5 * 1024 * 1024) }] })
    await sync.pull()
    expect(server.writes).toEqual([])
    expect(board.latest()?.text).toBe(
      'The drawings on SBIN are larger than the server keeps, so they are kept on this phone only.'
    )
  })

  it('never throws when the server cannot be asked', async () => {
    const { server, state, sync } = phone()
    server.read = async () => {
      throw new Error('gone')
    }
    state.local = text('a')
    await expect(sync.pull()).resolves.toBeUndefined()
  })
})

describe('a record kept across visits', () => {
  it('lets the next visit tell an unchanged copy from a changed one', async () => {
    const { server, state, make } = phone()
    state.local = text('a')
    await make().pull()
    server.saves(doc('a', 'tablet'))
    await make().pull() // a fresh visit: nothing changed here, so the server's copy is taken
    expect(state.local).toBe(text('a', 'tablet'))
  })
})

describe('the page’s one sync per document', () => {
  it('is the same sync wherever it is asked for, so a write in flight is never raced', () => {
    expect(documentSync('drawings', 'NSE:ONE')).toBe(documentSync('drawings', 'NSE:ONE'))
    expect(documentSync('drawings', 'NSE:ONE')).not.toBe(documentSync('phone_set', 'NSE:ONE'))
  })

  it('keeps at most 64, the oldest idle one forgotten first', () => {
    const first = documentSync('drawings', 'NSE:FIRST')
    for (let i = 0; i < 70; i++) documentSync('drawings', `NSE:K${i}`)
    expect(documentSync('drawings', 'NSE:FIRST')).not.toBe(first)
  })
})

it('imports nothing from the chart engine, so the phone chart never loads a tier for it', () => {
  for (const name of ['api.ts', 'docSync.ts', 'SyncNotice.tsx']) {
    const text = readFileSync(resolve(__dirname, name), 'utf8')
    expect(text, name).not.toMatch(/from 'openalgo-charts/)
  }
})
