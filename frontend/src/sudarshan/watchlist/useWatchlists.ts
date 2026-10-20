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

/** The most lists the server keeps for one user (`MAX_LISTS_PER_USER`). */
export const MAX_LISTS = 50

/** The most instruments the server keeps in one list (`MAX_ITEMS_PER_LIST`). */
export const MAX_ITEMS = 250

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

/**
 * The lists, and every change to them.
 *
 * Changes show at once where the answer is already known (an instrument
 * removed or moved, a list made, renamed or given an instrument once the
 * server has said yes), and every save goes to the server one after another
 * on a single chain, so two quick taps cannot land out of order. The lists are
 * read again once, when the chain runs dry: never while a save is still out,
 * whose answer such a read could not yet hold. A refused save is reported, and
 * that read puts the screen back as the server has it.
 */
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

  /** Change the lists on screen at once, past any read in flight. */
  const patch = useCallback(
    async (change: (lists: Watchlist[]) => Watchlist[]) => {
      await client.cancelQueries({ queryKey: WATCHLISTS_KEY })
      client.setQueryData<Watchlist[]>(WATCHLISTS_KEY, (lists) => lists && change(lists))
    },
    [client]
  )

  const patchList = useCallback(
    (listId: number, change: (list: Watchlist) => Watchlist) =>
      patch((lists) => lists.map((list) => (list.id === listId ? change(list) : list))),
    [patch]
  )

  const chain = useRef<Promise<void>>(Promise.resolve())
  const waiting = useRef(0)

  /** Run a save after every save before it; read the lists again once the last one is done. */
  const enqueue = useCallback(
    <T>(save: () => Promise<T>): Promise<T> => {
      waiting.current += 1
      const run = chain.current.then(save)
      chain.current = run
        .then(
          () => undefined,
          () => undefined
        )
        .then(() => {
          waiting.current -= 1
          // Not waited for: a read that hangs must not hold the next save. A
          // save that follows cancels it before it draws (`patch`).
          if (waiting.current === 0) void refresh()
        })
      return run
    },
    [refresh]
  )

  /** Make a list. Throws with the server's reason (a name already used, the cap). */
  const create = useCallback(
    (name: string): Promise<Watchlist> =>
      enqueue(async () => {
        const created = await watchlistApi.create(name.trim())
        await patch((lists) =>
          lists.some((list) => list.id === created.id) ? lists : [...lists, created]
        )
        return created
      }),
    [enqueue, patch]
  )

  /** Rename a list. Throws with the server's reason. */
  const rename = useCallback(
    (listId: number, name: string): Promise<void> =>
      enqueue(async () => {
        await watchlistApi.rename(listId, name.trim())
        await patchList(listId, (list) => ({ ...list, name: name.trim() }))
      }),
    [enqueue, patchList]
  )

  /**
   * Delete a list, and say which list to show next. Deleting the last one
   * makes a fresh empty list in its place, so the page is never left with
   * nothing to act on. The lists are read afresh before answering, whatever
   * the cache thinks of its own age. Throws with the server's reason.
   */
  const remove = useCallback(
    (listId: number): Promise<number | null> =>
      enqueue(async () => {
        await watchlistApi.remove(listId)
        const lists = await client.fetchQuery({
          queryKey: WATCHLISTS_KEY,
          queryFn: readLists,
          staleTime: 0,
        })
        return lists[0]?.id ?? null
      }),
    [client, enqueue]
  )

  /** Add an instrument at the end of a list. Throws with the server's reason (the list is full). */
  const addItem = useCallback(
    (listId: number, symbol: string, exchange: string): Promise<void> =>
      enqueue(async () => {
        const added = await watchlistApi.addItem(listId, symbol, exchange)
        if (added) {
          await patchList(listId, (list) =>
            list.items.some((item) => item.id === added.id)
              ? list
              : { ...list, items: [...list.items, added] }
          )
        }
      }),
    [enqueue, patchList]
  )

  /** Take an instrument out of a list: gone from the screen at once, put back if the server refuses. */
  const removeItem = useCallback(
    async (listId: number, item: WatchlistItem): Promise<void> => {
      await patchList(listId, (list) => ({
        ...list,
        items: list.items.filter((each) => each.id !== item.id),
      }))
      try {
        await enqueue(() => watchlistApi.removeItem(listId, item.id))
      } catch (error) {
        showToast.error(watchlistError(error, `Could not remove ${item.symbol}`))
      }
    },
    [enqueue, patchList]
  )

  /**
   * Move an instrument one place up or down, and save the whole order as the
   * screen now shows it. Each save carries the order of its own tap, and they
   * reach the server in the order they were made.
   */
  const move = useCallback(
    async (listId: number, itemId: number, by: -1 | 1): Promise<void> => {
      // Worked out from the lists as they stand when the change is drawn, so a
      // second tap builds on the first even before the first is saved.
      const made: { order: WatchlistItem[] | null } = { order: null }
      await patchList(listId, (list) => {
        const order = moved(list.items, itemId, by)
        if (order === list.items) return list
        made.order = order
        return { ...list, items: order }
      })
      const order = made.order
      if (!order) return
      try {
        await enqueue(() =>
          watchlistApi.reorderItems(
            listId,
            order.map((item) => item.id)
          )
        )
      } catch (error) {
        showToast.error(watchlistError(error, 'Could not save the new order'))
      }
    },
    [enqueue, patchList]
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
