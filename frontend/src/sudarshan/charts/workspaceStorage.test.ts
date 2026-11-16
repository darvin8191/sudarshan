/**
 * The terminal's saved layouts and templates on the server (M4.18, T7b), through the workspace
 * tier's real repository over its in-memory storage (the IndexedDB adapter's contract) and a fake
 * server: off it is the browser's storage and nothing else; on, the server's copy is the copy of
 * record, a change here is sent on, and when both sides changed the server's copy is kept with
 * what this browser made added, in words. Nothing reaches a network.
 */
import { act, renderHook, waitFor } from '@testing-library/react'
import {
  createMemoryWorkspaceStorage,
  type IndexedDbWorkspaceStorage,
  type WorkspaceCatalog,
  type WorkspacePayload,
  WorkspaceRepository,
} from 'openalgo-charts/workspace'
import { describe, expect, it } from 'vitest'
import { useChartWorkspaceCatalog } from '@/hooks/useChartWorkspaceCatalog'
import type { ChartsApi, DocKind, Problem, ServerDoc, WriteResult } from './api'
import { mergeCatalogs, syncedWorkspaceStorage } from './workspaceStorage'

const NS = 'oa-trading:alice'

class FakeServer implements ChartsApi {
  on = true
  next = 1_794_000_000_000
  doc: { revision: number; body: unknown } | null = null
  readonly writes: Array<{ base: number; body: WorkspaceCatalog }> = []
  readonly reads: Array<number | undefined> = []
  offline = false
  problem: Problem | null = null

  async enabled() {
    return this.on
  }

  async read(kind: DocKind, key: string, have?: number) {
    this.reads.push(have)
    if (this.offline)
      return { ok: false as const, problem: { code: 'offline' as const, message: 'x' } }
    if (!this.doc) return { ok: true as const, doc: null }
    const out: ServerDoc = { kind, key, revision: this.doc.revision, updated_ms: 1 }
    if (have !== this.doc.revision) out.body = structuredClone(this.doc.body)
    return { ok: true as const, doc: out }
  }

  async write(kind: DocKind, key: string, base: number, body: unknown): Promise<WriteResult> {
    this.writes.push({ base, body: structuredClone(body) as WorkspaceCatalog })
    if (this.offline) return { ok: false, problem: { code: 'offline', message: 'x' } }
    if (this.problem) return { ok: false, problem: this.problem }
    if ((this.doc?.revision ?? 0) !== base)
      return {
        ok: false,
        stale: this.doc ? { kind, key, revision: this.doc.revision, updated_ms: 1 } : null,
      }
    this.doc = { revision: ++this.next, body: structuredClone(body) }
    return { ok: true, doc: { kind, key, revision: this.doc.revision, updated_ms: 1 } }
  }

  names(): string[] {
    const body = this.doc?.body as WorkspaceCatalog | undefined
    return [...(body?.workspaces ?? []), ...(body?.templates ?? [])].map((d) => d.name).sort()
  }
}

function records() {
  const items = new Map<string, string>()
  return {
    getItem: (k: string) => items.get(k) ?? null,
    setItem: (k: string, v: string) => void items.set(k, v),
  }
}

function idb(): IndexedDbWorkspaceStorage {
  return { ...createMemoryWorkspaceStorage(), close: async () => {} }
}

/** One browser: its own storage and records, a clock it controls, and a repository on top. */
function browser(server: FakeServer, name = 'b') {
  let time = 1_794_000_000_000
  let ids = 0
  const local = idb()
  const storage = syncedWorkspaceStorage(local, {
    api: server,
    storage: records(),
    now: () => time,
  })
  const repo = new WorkspaceRepository(storage, NS, {
    now: () => time,
    id: () => `${name}-${++ids}`,
  })
  return {
    local,
    storage,
    repo,
    tick: (ms = 60_000) => {
      time += ms
    },
  }
}

const PAYLOAD: WorkspacePayload = {
  layout: {
    rows: 1,
    columns: 1,
    slots: [{ paneId: 'p0', row: 0, column: 0, rowSpan: 1, columnSpan: 1 }],
  },
  panes: [
    {
      id: 'p0',
      symbol: 'SBIN',
      exchange: 'NSE',
      interval: '5m',
      chartType: 'candlestick',
      chart: { version: 1 },
      settings: {},
      volume: true,
      magnet: 'off',
      stay: false,
      comparisons: [],
      comparisonMode: 'price',
    },
  ],
  activePaneId: 'p0',
  sync: { crosshair: true, viewport: false, symbol: false, interval: false },
}

const namesOf = (catalog: WorkspaceCatalog) =>
  [...catalog.workspaces, ...catalog.templates].map((d) => d.name).sort()

describe('switched off', () => {
  it('is this browser’s storage and nothing else', async () => {
    const server = new FakeServer()
    server.on = false
    const { repo, storage } = browser(server)
    await repo.createTemplate('Momentum', [])
    await repo.createWorkspace('Intraday', PAYLOAD)
    expect(namesOf(await repo.load())).toEqual(['Intraday', 'Momentum'])
    expect(server.reads).toEqual([])
    expect(server.writes).toEqual([])
    expect(storage.notice()).toBeNull()
  })
})

describe('switched on', () => {
  it('sends a browser’s catalogue the server has not seen, then each change after it', async () => {
    const server = new FakeServer()
    const off = browser(server)
    server.on = false
    await off.repo.createTemplate('Made before the switch', [])
    server.on = true
    expect(namesOf(await off.repo.load())).toEqual(['Made before the switch'])
    expect(server.writes[0].base).toBe(0)
    await off.repo.createWorkspace('Intraday', PAYLOAD)
    expect(server.names()).toEqual(['Intraday', 'Made before the switch'])
    expect(server.writes.at(-1)?.base).toBe(server.next - 1) // the revision it replaced
  })

  it('a second browser shows what the first saved, and the tier’s revisions stay its own', async () => {
    const server = new FakeServer()
    const laptop = browser(server, 'laptop')
    await laptop.repo.createWorkspace('Intraday', PAYLOAD)
    const tablet = browser(server, 'tablet')
    const seen = await tablet.repo.load()
    expect(namesOf(seen)).toEqual(['Intraday'])
    expect(seen.revision).toBe(1) // this browser's first catalogue, whatever the server's numbers
    await tablet.repo.createTemplate('Momentum', []) // the tier's own write still lands
    laptop.tick()
    expect(namesOf(await laptop.repo.load())).toEqual(['Intraday', 'Momentum'])
    expect(laptop.storage.notice()).toBeNull()
  })

  it('reads with the revision it holds, so an unchanged catalogue is not sent again', async () => {
    const server = new FakeServer()
    const { repo } = browser(server)
    await repo.createTemplate('Momentum', [])
    const writes = server.writes.length
    await repo.load()
    await repo.load()
    expect(server.reads.at(-1)).toBe(server.doc?.revision)
    expect(server.writes).toHaveLength(writes)
  })

  it('offline, a change lands here and is sent when the server answers again', async () => {
    const server = new FakeServer()
    const { repo } = browser(server)
    await repo.createTemplate('First', [])
    server.offline = true
    await repo.createTemplate('Made offline', [])
    expect(namesOf(await repo.load())).toEqual(['First', 'Made offline'])
    server.offline = false
    await repo.load()
    expect(server.names()).toEqual(['First', 'Made offline'])
  })
})

describe('when both sides changed', () => {
  it('keeps the server’s copy, adds what this browser made, and says so', async () => {
    const server = new FakeServer()
    const laptop = browser(server, 'laptop')
    await laptop.repo.createTemplate('Shared', [])
    const tablet = browser(server, 'tablet')
    await tablet.repo.load()
    server.offline = true
    laptop.tick()
    await laptop.repo.createWorkspace('Made on the laptop offline', PAYLOAD)
    server.offline = false
    tablet.tick()
    await tablet.repo.createTemplate('Made on the tablet', [])
    laptop.tick()
    const merged = await laptop.repo.load()
    expect(namesOf(merged)).toEqual(['Made on the laptop offline', 'Made on the tablet', 'Shared'])
    expect(server.names()).toEqual(['Made on the laptop offline', 'Made on the tablet', 'Shared'])
    expect(laptop.storage.notice()).toBe(
      'Layouts saved on another device were brought in, and “Made on the laptop offline” from this browser were kept beside them.'
    )
    expect(laptop.storage.notice()).toBeNull() // said once
  })

  it('a layout changed on both keeps the other device’s version and names it', async () => {
    const server = new FakeServer()
    const laptop = browser(server, 'laptop')
    const made = await laptop.repo.createWorkspace('Intraday', PAYLOAD)
    const tablet = browser(server, 'tablet')
    await tablet.repo.load()
    server.offline = true
    laptop.tick()
    await laptop.repo.saveWorkspace(made.id, { ...PAYLOAD, activePaneId: 'p0' })
    await laptop.repo.rename('workspace', made.id, 'Intraday laptop')
    server.offline = false
    tablet.tick()
    await tablet.repo.rename('workspace', made.id, 'Intraday tablet')
    laptop.tick()
    const kept = await laptop.repo.load()
    expect(namesOf(kept)).toEqual(['Intraday tablet'])
    expect(laptop.storage.notice()).toBe(
      'Layouts were changed on another device too. “Intraday laptop” kept the other device’s version, and the change made in this browser was not saved.'
    )
  })

  it('at first sight a second browser’s own layouts are kept beside the server’s', async () => {
    const server = new FakeServer()
    const laptop = browser(server, 'laptop')
    await laptop.repo.createTemplate('From the laptop', [])
    server.on = false
    const tablet = browser(server, 'tablet')
    await tablet.repo.createTemplate('Tablet’s own', [])
    server.on = true
    expect(namesOf(await tablet.repo.load())).toEqual(['From the laptop', 'Tablet’s own'])
    expect(server.names()).toEqual(['From the laptop', 'Tablet’s own'])
  })

  it('a layout deleted on the other device stays deleted when nothing changed it here', () => {
    const base = {
      version: 1 as const,
      revision: 3,
      recentWorkspaceIds: [],
      activeWorkspaceId: null,
      autosave: false,
    }
    const template = (id: string, name: string, at: number) => ({
      version: 1 as const,
      kind: 'indicator-template' as const,
      id,
      name,
      createdAt: at,
      updatedAt: at,
      indicators: [],
    }) // prettier-ignore
    const theirs = { ...base, workspaces: [], templates: [template('t1', 'Kept', 10)] }
    const mine = {
      ...base,
      workspaces: [],
      templates: [
        template('t1', 'Kept', 10),
        template('t2', 'Deleted there', 10),
        template('t3', 'New here', 50),
      ],
    }
    const { merged, kept, lost } = mergeCatalogs(theirs, mine, 20)
    expect(merged.templates.map((t) => t.name)).toEqual(['Kept', 'New here'])
    expect(kept).toEqual(['New here'])
    expect(lost).toEqual([])
  })
})

describe('what is kept as it is', () => {
  it('a server copy this app cannot read leaves this browser’s, with a sentence', async () => {
    const server = new FakeServer()
    const { repo, storage } = browser(server)
    await repo.createTemplate('Mine', [])
    server.doc = {
      revision: server.next + 5,
      body: { version: 1, revision: 1, workspaces: [{}], templates: [] },
    }
    expect(namesOf(await repo.load())).toEqual(['Mine'])
    expect(storage.notice()).toContain('could not be read by this version of the app')
  })

  it('a broken catalogue in this browser is never replaced, and nothing is sent', async () => {
    const server = new FakeServer()
    const broken = { version: 1, revision: 1, broken: true }
    const local = { ...createMemoryWorkspaceStorage({ [NS]: broken }), close: async () => {} }
    const storage = syncedWorkspaceStorage(local, { api: server, storage: records() })
    expect(await storage.read(NS)).toEqual({ version: 1, revision: 1, broken: true })
    expect(server.writes).toEqual([])
  })

  it('a refusal the owner can act on is said; the catalogue stays in this browser', async () => {
    const server = new FakeServer()
    server.problem = {
      code: 'too_big',
      message: 'That is larger than the server keeps, so it is kept on this device only.',
    }
    const { repo, storage } = browser(server)
    await repo.createTemplate('Mine', [])
    expect(storage.notice()).toBe(
      'That is larger than the server keeps, so it is kept on this device only.'
    )
    expect(namesOf(await repo.load())).toEqual(['Mine'])
  })
})

describe('the catalogue hook', () => {
  it('shows which copy was kept where it shows a failure, and clears it on the next load', async () => {
    const server = new FakeServer()
    const laptop = browser(server, 'laptop')
    await laptop.repo.createTemplate('Shared', [])
    server.offline = true
    const local = idb()
    const storage = syncedWorkspaceStorage(local, { api: server, storage: records() })
    const factory = () => storage
    const { result } = renderHook(() => useChartWorkspaceCatalog('alice', factory))
    await waitFor(() => expect(result.current.loading).toBe(false))
    await act(async () => {
      await result.current.run((repo) => repo.createTemplate('Made here offline', []))
    })
    server.offline = false
    await act(async () => {
      await result.current.reload()
    })
    expect(result.current.catalog?.templates.map((t) => t.name).sort()).toEqual([
      'Made here offline',
      'Shared',
    ])
    expect(result.current.error).toContain('were kept beside them')
    await act(async () => {
      await result.current.reload()
    })
    expect(result.current.error).toBeNull()
  })
})
