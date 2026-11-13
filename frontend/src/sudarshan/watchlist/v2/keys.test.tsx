import { QueryClientProvider } from '@tanstack/react-query'
import { renderHook } from '@testing-library/react'
import { createElement, type ReactNode } from 'react'
import { describe, expect, it, vi } from 'vitest'

vi.mock('@/api/watchlist', () => ({
  watchlistApi: { list: vi.fn(async () => [{ id: 1, name: 'Main', position: 0, items: [] }]) },
  watchlistError: (error: unknown) => String(error),
}))

import { createQueryClient } from '@/app/providers'
import { onListsPush } from '@/sudarshan/screener/push'
import { useWatchlists, WATCHLISTS_KEY } from '../useWatchlists'
import { arrangeKeys, writeThenRead } from './arrange'
import { LISTS_KEY, STATE_KEY, viewKey } from './useListView'

/** A client holding the old page's lists and every kind of v2 read. */
function held() {
  const client = createQueryClient()
  client.setQueryData(WATCHLISTS_KEY, [{ id: 1, name: 'Main', position: 0, items: [] }])
  client.setQueryData(STATE_KEY, { enabled: true })
  client.setQueryData(LISTS_KEY, { version: 'a:1' })
  client.setQueryData(viewKey('list:1'), { ref: 'list:1', version: '3' })
  client.setQueryData(viewKey('list:2'), { ref: 'list:2', version: '3' })
  return client
}

const stale = (client: ReturnType<typeof held>, key: readonly unknown[]) =>
  client.getQueryState(key)?.isInvalidated === true

describe('a write re-reads only what it changed (WATCHLISTS_KEY is a prefix of every v2 key)', () => {
  it('writeThenRead leaves the state, the All lists answer and the other views alone', async () => {
    const client = held()
    await writeThenRead(client, arrangeKeys('list:1'), async () => 'done')
    expect(stale(client, viewKey('list:1'))).toBe(true)
    expect(stale(client, WATCHLISTS_KEY)).toBe(true)
    expect(stale(client, STATE_KEY)).toBe(false)
    expect(stale(client, LISTS_KEY)).toBe(false)
    expect(stale(client, viewKey('list:2'))).toBe(false)
  })

  it('useWatchlists().retry marks only the lists stale', async () => {
    const client = held()
    const wrapper = ({ children }: { children: ReactNode }) =>
      createElement(QueryClientProvider, { client }, children)
    const { result } = renderHook(() => useWatchlists(), { wrapper })
    await result.current.retry()
    expect(stale(client, STATE_KEY)).toBe(false)
    expect(stale(client, LISTS_KEY)).toBe(false)
    expect(stale(client, viewKey('list:1'))).toBe(false)
  })

  it('a screener push marks the lists stale and not the v2 reads', () => {
    const client = held()
    onListsPush(client)
    expect(stale(client, WATCHLISTS_KEY)).toBe(true)
    expect(stale(client, viewKey('list:1'))).toBe(false)
    expect(stale(client, LISTS_KEY)).toBe(false)
  })
})
