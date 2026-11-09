import { act, renderHook } from '@testing-library/react'
import { createElement, type ReactNode } from 'react'
import { MemoryRouter, useLocation } from 'react-router'
import { describe, expect, it } from 'vitest'
import { overlayOf, useOverlay } from './useOverlay'

describe('overlayOf', () => {
  it('knows the All lists sheet only while v2 is on', () => {
    const params = new URLSearchParams('sheet=lists')
    expect(overlayOf(params, null)).toBeNull()
    expect(overlayOf(params, null, true)).toEqual({ kind: 'lists' })
  })
})

function at(path: string, v2: boolean) {
  const wrapper = ({ children }: { children: ReactNode }) =>
    createElement(MemoryRouter, { initialEntries: ['/watchlist', path], initialIndex: 1 }, children)
  return renderHook(() => ({ ...useOverlay(v2), location: useLocation() }), { wrapper })
}

describe('useOverlay with v2', () => {
  it('opens the sheet in the address, and Back (the close) leaves the list behind it', () => {
    const wrapper = ({ children }: { children: ReactNode }) =>
      createElement(MemoryRouter, { initialEntries: ['/watchlist'] }, children)
    const { result } = renderHook(() => ({ ...useOverlay(true), location: useLocation() }), {
      wrapper,
    })
    act(() => result.current.open({ kind: 'lists' }))
    expect(result.current.overlay).toEqual({ kind: 'lists' })
    expect(result.current.location.search).toBe('?sheet=lists')
    act(() => result.current.close())
    expect(result.current.overlay).toBeNull()
    expect(result.current.location.pathname).toBe('/watchlist')
    expect(result.current.location.search).toBe('')
  })

  it('opens another sheet over it as one entry, so one Back closes the one that shows', () => {
    const wrapper = ({ children }: { children: ReactNode }) =>
      createElement(MemoryRouter, { initialEntries: ['/watchlist'] }, children)
    const { result } = renderHook(() => ({ ...useOverlay(true), location: useLocation() }), {
      wrapper,
    })
    act(() => result.current.open({ kind: 'lists' }))
    act(() => result.current.open({ kind: 'new' }))
    expect(result.current.overlay).toEqual({ kind: 'new' })
    act(() => result.current.close())
    expect(result.current.overlay).toBeNull()
  })

  it('reads a bookmarked All lists address as nothing while v2 is off, as before', () => {
    const { result } = at('/watchlist?sheet=lists', false)
    expect(result.current.overlay).toBeNull()
  })
})
