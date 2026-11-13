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
import { WATCHLISTS_KEY } from '../useWatchlists'
import type { Push, View } from './types'
import {
  applyPushes,
  keepNewer,
  LISTS_KEY,
  rememberListsVersion,
  useLists,
  useListView,
  useV2State,
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

describe('the version rules', () => {
  // A list's own version is a plain number as text, as `refs/list.py` answers it.
  it('keeps the view held when an older answer arrives, takes a newer or an unorderable one', () => {
    expect(keepNewer(view('5'), view('4')).version).toBe('5')
    expect(keepNewer(view('5'), view('6')).version).toBe('6')
    expect(keepNewer(view('9'), view('10')).version).toBe('10') // numbers, not text
    expect(keepNewer(view('1789000000123'), view('1789000000100')).version).toBe('1789000000123')
    expect(keepNewer(view('1789000000123'), view('0')).version).toBe('0') // a list made anew
    expect(keepNewer(view('5'), view('x')).version).toBe('x')
    expect(keepNewer(undefined, view('1')).version).toBe('1')
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
      client.setQueryData(viewKey('list:1'), view('6'))
    })
    await act(async () => release(view('5')))
    await waitFor(() => expect(result.current.fetchStatus).toBe('idle'))
    expect(result.current.data?.version).toBe('6')
  })

  // The lists version is "<boot>:<n>" and is only ever compared for equality.
  it('takes every All lists answer as it comes, whatever its version says', async () => {
    const { client, wrapper } = setup()
    b().on('state', () => ({ enabled: true }))
    b().on('lists', () => ({ version: 'a:2', own: [] }))
    client.setQueryData(LISTS_KEY, { version: 'a:9', own: [] }, { updatedAt: 1 })
    const { result } = renderHook(() => useLists(), { wrapper })
    await waitFor(() => expect(b().count('lists')).toBe(1))
    await waitFor(() => expect(result.current.data?.version).toBe('a:2'))
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
  const keysOf = (spy: { mock: { calls: Array<[{ queryKey?: unknown; exact?: boolean }?]> } }) =>
    spy.mock.calls.map(
      ([filter]) => `${JSON.stringify(filter?.queryKey)}${filter?.exact ? '!' : ''}`
    )
  const watch = (client: ReturnType<typeof setup>['client']) =>
    vi.spyOn(client, 'invalidateQueries')
  const view1 = `${JSON.stringify(viewKey('list:1'))}!`
  const lists = `${JSON.stringify(WATCHLISTS_KEY)}!`

  it('reads the named view again, once however many pushes name it', () => {
    const { client } = setup()
    const spy = watch(client)
    applyPushes(client, [push('a:2'), push('a:3', 'rows'), push('a:3', 'sections')])
    expect(keysOf(spy).filter((key) => key === view1)).toHaveLength(1)
  })

  it('reads OpenAlgo lists, the rows and tabs source, and the view for a push about an own list', () => {
    const { client } = setup()
    const spy = watch(client)
    applyPushes(client, [push('a:2')])
    expect(keysOf(spy)).toEqual(expect.arrayContaining([view1, lists]))
    spy.mockClear()
    applyPushes(client, [push('a:3', 'lists', 'lists')])
    expect(keysOf(spy)).toContain(lists)
  })

  it('reads only the overlay for a section, row or preference change', () => {
    const { client } = setup()
    const spy = watch(client)
    applyPushes(client, [push('a:2', 'sections'), push('a:3', 'prefs'), push('a:4', 'rows')])
    expect(keysOf(spy)).toEqual([view1])
  })

  it('reads every list a push names (a move), and no other', () => {
    const { client } = setup()
    const spy = watch(client)
    applyPushes(client, [{ ...push('a:2', 'items', 'lists'), refs: ['list:1', 'list:2'] }])
    const keys = keysOf(spy)
    expect(keys).toEqual(
      expect.arrayContaining([view1, `${JSON.stringify(viewKey('list:2'))}!`, lists])
    )
    expect(keys).not.toContain(`${JSON.stringify(viewKey('list:3'))}!`)
    // "lists" with no names reads every open view.
    expect(keys).not.toContain(JSON.stringify([...V2_KEY, 'view']))
  })

  it('falls back to ref when refs is absent, and reads every open view for "lists"', () => {
    const { client } = setup()
    const spy = watch(client)
    applyPushes(client, [push('a:2', 'items', 'lists')])
    expect(keysOf(spy)).toContain(JSON.stringify([...V2_KEY, 'view']))
    spy.mockClear()
    applyPushes(client, [{ ...push('a:3'), refs: [] }])
    expect(keysOf(spy)).toContain(view1)
  })

  it('reads nothing for a push whose lists version this page already saw', async () => {
    const { client, wrapper } = setup()
    b().on('state', () => ({ enabled: true, versions: { lists: 'a:3', favourites: 0, flags: 0 } }))
    const { result } = renderHook(() => useV2State(), { wrapper })
    await waitFor(() => expect(result.current.data?.enabled).toBe(true))
    const spy = watch(client)
    applyPushes(client, [push('a:3')])
    expect(spy).not.toHaveBeenCalled()
    applyPushes(client, [push('a:4')])
    expect(spy).toHaveBeenCalled()
    spy.mockClear()
    applyPushes(client, [push('a:4')]) // the push just handled is known too
    expect(spy).not.toHaveBeenCalled()
  })

  it('never skips a flags or favourites push, whose lists version did not move', () => {
    const { client } = setup()
    rememberListsVersion(client, 'a:3')
    const spy = watch(client)
    applyPushes(client, [push('a:3', 'flags', 'flag:2'), push('a:3', 'favourites', '')])
    expect(spy).toHaveBeenCalled()
  })

  it('does not skip while a read is in flight, which may not have seen the change', () => {
    const { client } = setup()
    rememberListsVersion(client, 'a:3')
    void client.fetchQuery({ queryKey: viewKey('list:1'), queryFn: () => new Promise(() => {}) })
    const spy = watch(client)
    applyPushes(client, [push('a:3')])
    expect(spy).toHaveBeenCalled()
  })

  it('names the state and the lists for favourites, and every view for flags', () => {
    const { client } = setup()
    const spy = watch(client)
    applyPushes(client, [push('a:1', 'favourites', '')])
    expect(spy).toHaveBeenCalledTimes(2)
    spy.mockClear()
    applyPushes(client, [push('a:2', 'flags', 'flag:2')])
    expect(spy.mock.calls.map(([f]) => (f?.queryKey as string[]).at(-1))).toContain('view')
  })
})
