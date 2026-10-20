import { describe, expect, it, vi } from 'vitest'

const api = vi.hoisted(() => ({ list: vi.fn(), create: vi.fn() }))
vi.mock('@/api/watchlist', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/api/watchlist')>()),
  watchlistApi: api,
}))

import { DEFAULT_LIST_NAME, moved, readLists } from './useWatchlists'

const items = [1, 2, 3].map((id) => ({ id, symbol: `S${id}`, exchange: 'NSE', position: id }))

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
    const created = { id: 7, name: DEFAULT_LIST_NAME, position: 0, items: [] }
    api.list.mockResolvedValueOnce([])
    api.create.mockResolvedValueOnce(created)
    expect(await readLists()).toEqual([created])
    expect(api.create).toHaveBeenCalledWith('Watchlist')
  })

  it('reads again when another device made it first, instead of reporting a failure', async () => {
    const theirs = { id: 8, name: DEFAULT_LIST_NAME, position: 0, items: [] }
    api.list.mockResolvedValueOnce([]).mockResolvedValueOnce([theirs])
    api.create.mockRejectedValueOnce(new Error('A list named "Watchlist" already exists'))
    expect(await readLists()).toEqual([theirs])
  })
})
