import { useQuery, useQueryClient } from '@tanstack/react-query'
import { useCallback, useRef } from 'react'
import { type Watchlist, type WatchlistItem, watchlistApi, watchlistError } from '@/api/watchlist'
import { showToast } from '@/utils/toast'

/**
 * The watchlists themselves (M8.1): the server-side lists the /trading
 * terminal's watchlist panel reads and writes (`blueprints/watchlist.py`,
 * `api/watchlist.ts`), so a list is the same on the phone and in the terminal.
 * Up to 50 lists of up to 250 instruments, names unique and at most 64
 * characters; the server keeps the order of the instruments in a list.
 */
export const WATCHLISTS_KEY = ['sudarshan', 'watchlists'] as const

/** Made when there is no list at all, as the terminal's panel does. */
export const DEFAULT_LIST_NAME = 'Watchlist'

/** The longest name the server accepts. */
export const MAX_NAME_LENGTH = 64

/** Every list, making the first one when there is none, so the page always has one to act on. */
export async function readLists(): Promise<Watchlist[]> {
  const lists = await watchlistApi.list()
  if (lists.length > 0) return lists
  try {
    return [await watchlistApi.create(DEFAULT_LIST_NAME)]
  } catch {
    // The terminal or another device made it first: the name is unique, so
    // that is a refusal, not a failure. Read again rather than report it.
    const again = await watchlistApi.list()
    if (again.length === 0) throw new Error('Could not create a watchlist')
    return again
  }
}

/** The list with one instrument moved one place up or down; the same list at either end. */
export function moved(items: WatchlistItem[], itemId: number, by: -1 | 1): WatchlistItem[] {
  const from = items.findIndex((item) => item.id === itemId)
  const to = from + by
  if (from < 0 || to < 0 || to >= items.length) return items
  const next = [...items]
  const [item] = next.splice(from, 1)
  next.splice(to, 0, item)
  return next
}

export function useWatchlists() {
  const client = useQueryClient()
  const query = useQuery({
    queryKey: WATCHLISTS_KEY,
    queryFn: readLists,
    staleTime: 15_000,
    retry: false,
  })

  const refresh = useCallback(
    () => client.invalidateQueries({ queryKey: WATCHLISTS_KEY }),
    [client]
  )

  /** Change the lists on screen at once; the server's answer follows. */
  const patch = useCallback(
    async (listId: number, change: (items: WatchlistItem[]) => WatchlistItem[]) => {
      await client.cancelQueries({ queryKey: WATCHLISTS_KEY })
      client.setQueryData<Watchlist[]>(WATCHLISTS_KEY, (lists) =>
        lists?.map((list) => (list.id === listId ? { ...list, items: change(list.items) } : list))
      )
    },
    [client]
  )

  /** Make a list. Throws with the server's reason (a name already used, the cap). */
  const create = useCallback(
    async (name: string): Promise<Watchlist> => {
      const created = await watchlistApi.create(name.trim())
      await refresh()
      return created
    },
    [refresh]
  )

  /** Rename a list. Throws with the server's reason. */
  const rename = useCallback(
    async (listId: number, name: string): Promise<void> => {
      await watchlistApi.rename(listId, name.trim())
      await refresh()
    },
    [refresh]
  )

  /**
   * Delete a list, and say which list to show next. Deleting the last one
   * makes a fresh empty list in its place, so the page is never left with
   * nothing to act on. Throws with the server's reason.
   */
  const remove = useCallback(
    async (listId: number): Promise<number | null> => {
      await watchlistApi.remove(listId)
      const lists = await client.fetchQuery({ queryKey: WATCHLISTS_KEY, queryFn: readLists })
      return lists[0]?.id ?? null
    },
    [client]
  )

  /** Add an instrument at the end of a list. Throws with the server's reason (the list is full). */
  const addItem = useCallback(
    async (listId: number, symbol: string, exchange: string): Promise<void> => {
      await watchlistApi.addItem(listId, symbol, exchange)
      await refresh()
    },
    [refresh]
  )

  /** Take an instrument out of a list: gone from the screen at once, put back if the server refuses. */
  const removeItem = useCallback(
    async (listId: number, item: WatchlistItem): Promise<void> => {
      await patch(listId, (items) => items.filter((each) => each.id !== item.id))
      try {
        await watchlistApi.removeItem(listId, item.id)
      } catch (error) {
        showToast.error(watchlistError(error, `Could not remove ${item.symbol}`))
      } finally {
        await refresh()
      }
    },
    [patch, refresh]
  )

  /**
   * Move an instrument one place up or down, and save the whole order. Saves
   * go one after another, so two quick taps cannot reach the server out of
   * order and leave the first order in place.
   */
  const saving = useRef<Promise<void>>(Promise.resolve())
  const move = useCallback(
    async (listId: number, itemId: number, by: -1 | 1): Promise<void> => {
      const list = client
        .getQueryData<Watchlist[]>(WATCHLISTS_KEY)
        ?.find((each) => each.id === listId)
      if (!list) return
      const order = moved(list.items, itemId, by)
      if (order === list.items) return
      await patch(listId, () => order)
      const save = saving.current.then(async () => {
        try {
          await watchlistApi.reorderItems(
            listId,
            order.map((item) => item.id)
          )
        } catch (error) {
          showToast.error(watchlistError(error, 'Could not save the new order'))
          await refresh()
        }
      })
      saving.current = save
      await save
    },
    [client, patch, refresh]
  )

  return {
    lists: query.data ?? [],
    loading: query.isPending,
    error: query.isError
      ? watchlistError(query.error, 'Your watchlists could not be loaded.')
      : null,
    retry: refresh,
    create,
    rename,
    remove,
    addItem,
    removeItem,
    move,
  }
}
