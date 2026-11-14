// SUDARSHAN P6 D4: reading a part of the books from the viewer API. The first snapshot comes on
// arrival; after that a re-read happens when the service says it has read the part again (the
// `book` message on the viewer socket, cross-design Y11), when the page comes back into view, and
// as a re-sync every 45 s while a market is open. Never on a faster timer: ticks keep prices
// current, and the figures themselves change with an order, which the service announces.
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { useEffect } from 'react'
import { RESYNC_MS } from '@/sudarshan/dashboard/live'
import { familyApi } from './api'
import { getFamilyManager, onFamilyBook, setFamilyScreen, useFamilyFeed } from './familyManager'
import type { BookPart, Screen } from './types'

export const bookKey = (part: BookPart) => ['family', 'book', part] as const

export function useFamilyBook(part: BookPart) {
  const client = useQueryClient()
  const feed = useFamilyFeed()
  const query = useQuery({
    queryKey: bookKey(part),
    queryFn: ({ signal }) => familyApi.book(part, signal),
    retry: false,
    refetchOnMount: 'always',
    staleTime: 0,
    refetchInterval: feed.state === 'closed' ? false : RESYNC_MS,
  })
  useEffect(
    () =>
      onFamilyBook((changed) => {
        if (changed === part) void client.invalidateQueries({ queryKey: bookKey(part) })
      }),
    [client, part]
  )
  return query
}

/**
 * Tell the service which screen is open: what it claims from the feed follows the screens. An
 * open screen also holds the socket up, because the service tells a screen on that socket that
 * its books were read again (Y11): a screen with no price to follow (Trades, Orders with nothing
 * working) would otherwise never open one, and would only learn of a fill at the 45 s re-read.
 */
export function useFamilyScreen(name: Screen): void {
  useEffect(() => {
    void getFamilyManager().connect()
    setFamilyScreen(name)
    return () => setFamilyScreen(null)
  }, [name])
}
