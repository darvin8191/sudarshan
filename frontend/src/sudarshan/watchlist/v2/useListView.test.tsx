import { QueryClientProvider } from '@tanstack/react-query'
import { act, renderHook, waitFor } from '@testing-library/react'
import { createElement, type ReactNode } from 'react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const bridge = vi.hoisted(() => ({
  current: null as null | ReturnType<typeof import('./mockBridge').createBridge>,
}))
vi.mock('@/api/client', async () => {
  const { createBridge } = await import('./mockBridge')
  bridge.current = createBridge()
  return { webClient: bridge.current.client }
})

import { createQueryClient } from '@/app/providers'
import type { Push, View } from './types'
import {
  applyPushes,
  compareVersions,
  keepNewer,
  useListView,
  useLists,
  V2_KEY,
  viewKey,
} from './useListView'

const b = () => bridge.current as NonNullable<typeof bridge.current>
const view = (version: string): View => ({ ref: 'list:1', version }) as View
const push = (v: string, why: Push['why'] = 'items', ref = 'list:1'): Push => ({ v, ref, why })

function setup() {
  const client = createQueryClient()
  const wrapper = ({ children }: { children: ReactNode }) =>
    createElement(QueryClientProvider, { client }, children)
  return { client, wrapper }
}

beforeEach(() => b().reset())

describe('the version rule', () => {
  it('orders versions of one boot and cannot order those of two', () => {
    expect(compareVersions('a:2', 'a:2')).toBe(0)
    expect(compareVersions('a:3', 'a:2')).toBe(1)
    expect(compareVersions('a:2', 'a:10')).toBe(-1)
    expect(compareVersions('b:1', 'a:9')).toBeNull()
    expect(compareVersions('garbage', 'a:1')).toBeNull()
  })

  it('keeps what is held when an older answer arrives, takes a newer or an unordered one', () => {
    expect(keepNewer(view('a:5'), view('a:4')).version).toBe('a:5')
    expect(keepNewer(view('a:5'), view('a:6')).version).toBe('a:6')
    expect(keepNewer(view('a:5'), view('b:1')).version).toBe('b:1')
    expect(keepNewer(undefined, view('a:1')).version).toBe('a:1')
  })

  it('does not let a slow read that began before a change replace the answer that saw it', async () => {
    const { client, wrapper } = setup()
    let release: (v: View) => void = () => {}
    b().on('state', () => ({ enabled: true }))
    b().on(
      'view',
      () =>
        new Promise<View>((resolve) => {
          release = resolve
        })
    )
    const { result } = renderHook(() => useListView('list:1'), { wrapper })
    await waitFor(() => expect(b().count('view')).toBe(1))
    // The newer answer reaches the cache while the older read is still on its way.
    act(() => {
      client.setQueryData(viewKey('list:1'), view('a:6'))
    })
    await act(async () => release(view('a:5')))
    await waitFor(() => expect(result.current.fetchStatus).toBe('idle'))
    expect(result.current.data?.version).toBe('a:6')
  })
})

describe('while v2 is off', () => {
  it('reads the state and nothing else', async () => {
    const { client, wrapper } = setup()
    b().on('state', () => ({ enabled: false }))
    client.setQueryData(['x'], 1)
    const { result } = renderHook(() => [useListView('list:1'), useLists()] as const, { wrapper })
    await act(async () => {
      await client.fetchQuery({
        queryKey: [...V2_KEY, 'state'],
        queryFn: async () => ({ enabled: false }),
      })
    })
    expect(result.current[0].fetchStatus).toBe('idle')
    expect(result.current[1].fetchStatus).toBe('idle')
    expect(b().count('view')).toBe(0)
    expect(b().count('lists')).toBe(0)
  })
})

describe('the change push', () => {
  const seed = (client: ReturnType<typeof setup>['client'], version: string) =>
    client.setQueryData(viewKey('list:1'), view(version))

  it('reads the named view again, once however many pushes name it', () => {
    const { client } = setup()
    seed(client, 'a:1')
    const spy = vi.spyOn(client, 'invalidateQueries')
    applyPushes(client, [push('a:2'), push('a:3', 'rows'), push('a:3', 'sections')])
    const keys = spy.mock.calls.map(([filter]) => JSON.stringify(filter?.queryKey))
    expect(keys.filter((key) => key === JSON.stringify(viewKey('list:1')))).toHaveLength(1)
  })

  it('reads nothing for a push whose version is the one held, or an older one', () => {
    const { client } = setup()
    seed(client, 'a:5')
    const spy = vi.spyOn(client, 'invalidateQueries')
    applyPushes(client, [push('a:5'), push('a:4')])
    expect(spy).not.toHaveBeenCalled()
  })

  it('reads again after a restart, whose versions cannot be ordered', () => {
    const { client } = setup()
    seed(client, 'a:5')
    const spy = vi.spyOn(client, 'invalidateQueries')
    applyPushes(client, [push('b:1')])
    expect(spy).toHaveBeenCalledTimes(2) // the view, and the counts
  })

  it('names the state and the lists for favourites, and every view for flags', () => {
    const { client } = setup()
    const spy = vi.spyOn(client, 'invalidateQueries')
    applyPushes(client, [push('a:1', 'favourites', '')])
    expect(spy).toHaveBeenCalledTimes(2)
    spy.mockClear()
    applyPushes(client, [push('a:1', 'flags', 'flag:2')])
    expect(spy.mock.calls.map(([f]) => (f?.queryKey as string[]).at(-1))).toContain('view')
  })
})
