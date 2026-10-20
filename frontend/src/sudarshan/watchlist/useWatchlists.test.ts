import { QueryClientProvider } from '@tanstack/react-query'
import { act, renderHook, waitFor } from '@testing-library/react'
import { createElement, type ReactNode } from 'react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

interface Item {
  id: number
  symbol: string
  exchange: string
  position: number
}
interface List {
  id: number
  name: string
  position: number
  items: Item[]
}

const server = vi.hoisted(() => {
  const state = { lists: [] as List[], hold: null as Promise<void> | null }
  const copy = <T>(value: T): T => JSON.parse(JSON.stringify(value))
  const find = (id: number) => state.lists.find((list) => list.id === id) as List
  /** Every call, in the order the server saw it start and finish. */
  const log: string[] = []
  const api = {
    list: vi.fn(async () => {
      log.push('list')
      return copy(state.lists)
    }),
    create: vi.fn(async (name: string) => {
      const list = { id: 100 + state.lists.length, name, position: state.lists.length, items: [] }
      state.lists.push(list)
      return copy(list)
    }),
    rename: vi.fn(async (id: number, name: string) => {
      find(id).name = name
    }),
    remove: vi.fn(async (id: number) => {
      state.lists = state.lists.filter((list) => list.id !== id)
    }),
    addItem: vi.fn(),
    removeItem: vi.fn(async (id: number, itemId: number) => {
      log.push(`remove ${itemId} start`)
      find(id).items = find(id).items.filter((item) => item.id !== itemId)
      log.push(`remove ${itemId} done`)
    }),
    reorderItems: vi.fn(async (id: number, order: number[]) => {
      log.push(`reorder ${order.join(',')} start`)
      if (state.hold) await state.hold
      const list = find(id)
      list.items = order
        .map((itemId) => list.items.find((item) => item.id === itemId))
        .filter((item): item is Item => item !== undefined)
        .map((item, position) => ({ ...item, position }))
      log.push(`reorder ${order.join(',')} done`)
    }),
  }
  return { state, api, log }
})

vi.mock('@/api/watchlist', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/api/watchlist')>()),
  watchlistApi: server.api,
}))

const toast = vi.hoisted(() => ({ error: vi.fn() }))
vi.mock('@/utils/toast', () => ({ showToast: toast }))

import { createQueryClient } from '@/app/providers'
import {
  DEFAULT_LIST_NAME,
  MAX_ITEMS,
  MAX_LISTS,
  moved,
  readLists,
  useWatchlists,
} from './useWatchlists'

const items = [1, 2, 3].map((id) => ({ id, symbol: `S${id}`, exchange: 'NSE', position: id }))

function seed() {
  server.state.lists = [
    {
      id: 1,
      name: 'Main',
      position: 0,
      items: [11, 12, 13].map((id, position) => ({
        id,
        symbol: `S${id}`,
        exchange: 'NSE',
        position,
      })),
    },
    { id: 2, name: 'Options', position: 1, items: [] },
  ]
}

/** The hook on the app's own query defaults (a minute's staleTime, refetch on focus). */
async function mount() {
  const client = createQueryClient()
  const wrapper = ({ children }: { children: ReactNode }) =>
    createElement(QueryClientProvider, { client }, children)
  const view = renderHook(() => useWatchlists(), { wrapper })
  await waitFor(() => expect(view.result.current.lists).toHaveLength(server.state.lists.length))
  server.log.length = 0
  server.api.list.mockClear()
  return view
}

const order = (lists: Array<{ id: number; items: Item[] }>, id = 1) =>
  lists.find((list) => list.id === id)?.items.map((item) => item.id)

beforeEach(() => {
  seed()
  server.state.hold = null
  server.log.length = 0
  for (const fn of Object.values(server.api)) fn.mockClear()
  toast.error.mockClear()
})

describe('moved', () => {
  it('moves an instrument one place up or down', () => {
    expect(moved(items, 2, -1).map((item) => item.id)).toEqual([2, 1, 3])
    expect(moved(items, 2, 1).map((item) => item.id)).toEqual([1, 3, 2])
  })

  it('leaves the list alone at either end, or for an id it does not hold', () => {
    expect(moved(items, 1, -1)).toBe(items)
    expect(moved(items, 3, 1)).toBe(items)
    expect(moved(items, 9, 1)).toBe(items)
  })
})

describe('readLists', () => {
  it('makes the first list when there is none', async () => {
    server.state.lists = []
    expect(await readLists()).toEqual([
      { id: 100, name: DEFAULT_LIST_NAME, position: 0, items: [] },
    ])
    expect(server.api.create).toHaveBeenCalledWith('Watchlist')
  })

  it('reads again when another device made it first, instead of reporting a failure', async () => {
    const theirs = { id: 8, name: DEFAULT_LIST_NAME, position: 0, items: [] }
    server.api.list.mockResolvedValueOnce([]).mockResolvedValueOnce([theirs])
    server.api.create.mockRejectedValueOnce(new Error('A list named "Watchlist" already exists'))
    expect(await readLists()).toEqual([theirs])
  })
})

describe('the caps', () => {
  it("are the server's", () => {
    expect(MAX_LISTS).toBe(50)
    expect(MAX_ITEMS).toBe(250)
  })
})

describe('useWatchlists', () => {
  it('reads the lists afresh after a delete, whatever the cache thinks of its age', async () => {
    const { result } = await mount()
    let next: number | null = null
    await act(async () => {
      next = await result.current.remove(1)
    })
    expect(next).toBe(2)
    // Answered from a fresh read, not the minute-old cache.
    await waitFor(() => expect(result.current.lists.map((list) => list.id)).toEqual([2]))
  })

  it('runs every save on one chain, in order, and reads the lists once when it runs dry', async () => {
    const { result } = await mount()
    let release = () => {}
    server.state.hold = new Promise<void>((done) => {
      release = done
    })

    let saves: Promise<void>[] = []
    act(() => {
      saves = [result.current.move(1, 11, 1), result.current.removeItem(1, items3(13))]
    })
    // Both show at once.
    await waitFor(() => expect(order(result.current.lists)).toEqual([12, 11]))
    // The remove waits for the reorder; nothing reads the lists meanwhile.
    expect(server.log).toEqual(['reorder 12,11,13 start'])

    await act(async () => {
      release()
      await Promise.all(saves)
    })
    await waitFor(() => expect(server.api.list).toHaveBeenCalledTimes(1))
    expect(server.log).toEqual([
      'reorder 12,11,13 start',
      'reorder 12,11,13 done',
      'remove 13 start',
      'remove 13 done',
      'list',
    ])
    expect(order(result.current.lists)).toEqual([12, 11])
    expect(order(server.state.lists)).toEqual([12, 11])
  })

  it('builds a second tap on the first before the first is saved', async () => {
    const { result } = await mount()
    await act(async () => {
      await Promise.all([result.current.move(1, 11, 1), result.current.move(1, 11, 1)])
    })
    expect(server.api.reorderItems.mock.calls.map((call) => call[1])).toEqual([
      [12, 11, 13],
      [12, 13, 11],
    ])
    await waitFor(() => expect(order(result.current.lists)).toEqual([12, 13, 11]))
  })

  it('puts a removed instrument back, and says so, when the server refuses', async () => {
    const { result } = await mount()
    server.api.removeItem.mockRejectedValueOnce(
      Object.assign(new Error('refused'), { response: { data: { message: 'List not found' } } })
    )
    await act(async () => {
      await result.current.removeItem(1, items3(12))
    })
    expect(toast.error).toHaveBeenCalledWith('List not found')
    await waitFor(() => expect(order(result.current.lists)).toEqual([11, 12, 13]))
  })

  it('puts the order back, and says so, when a move is refused', async () => {
    const { result } = await mount()
    server.api.reorderItems.mockRejectedValueOnce(new Error('offline'))
    await act(async () => {
      await result.current.move(1, 11, 1)
    })
    expect(toast.error).toHaveBeenCalledWith('Could not save the new order')
    await waitFor(() => expect(order(result.current.lists)).toEqual([11, 12, 13]))
  })

  it('shows a new or renamed list at once, before the lists are read again', async () => {
    const { result } = await mount()
    server.api.list.mockImplementation(() => new Promise(() => {}))
    await act(async () => {
      await result.current.create('Banks')
    })
    await waitFor(() =>
      expect(result.current.lists.map((list) => list.name)).toEqual(['Main', 'Options', 'Banks'])
    )
    // The read after the create never answers; the rename does not wait for it.
    await act(async () => {
      await result.current.rename(2, 'Weekly')
    })
    await waitFor(() =>
      expect(result.current.lists.map((list) => list.name)).toEqual(['Main', 'Weekly', 'Banks'])
    )
  })
})

/** Item `id` of the seeded Main list. */
function items3(id: number): Item {
  return { id, symbol: `S${id}`, exchange: 'NSE', position: id - 11 }
}
