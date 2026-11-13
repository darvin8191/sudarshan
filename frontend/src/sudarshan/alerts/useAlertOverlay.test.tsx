/**
 * The address contract of the Alerts page's overlays (M8.14): the list alert form and a group's
 * own form, which the watchlist's menus open, parse and build as documented, and keep the Back
 * rules of the other kinds (opening pushes an entry, closing goes back; one the page did not open
 * is replaced).
 */
import { act, renderHook } from '@testing-library/react'
import { createElement, type ReactNode } from 'react'
import { MemoryRouter, useLocation } from 'react-router'
import { describe, expect, it } from 'vitest'
import { alertOverlayOf, listAlertAddress, useAlertOverlay } from './useAlertOverlay'

const params = (query: string) => new URLSearchParams(query)

describe('the list alert address', () => {
  it('parses a new list alert with its heading and the fed hint', () => {
    expect(alertOverlayOf(params('sheet=list&ref=list%3Aswing&name=Swing&count=24&fed=1'))).toEqual(
      {
        kind: 'list',
        ref: 'list:swing',
        name: 'Swing',
        count: 24,
        fed: true,
      }
    )
    expect(alertOverlayOf(params('sheet=list&ref=flag%3Ared'))).toEqual({
      kind: 'list',
      ref: 'flag:red',
      name: '',
      count: null,
      fed: false,
    })
  })

  it('needs a ref, and reads a bad count as none', () => {
    expect(alertOverlayOf(params('sheet=list&name=Swing'))).toBeNull()
    expect(alertOverlayOf(params('sheet=list&ref=a&count=-3'))).toMatchObject({ count: null })
    expect(alertOverlayOf(params('sheet=list&ref=a&count=x'))).toMatchObject({ count: null })
  })

  it('parses a group edit and refuses a bad id', () => {
    expect(alertOverlayOf(params('sheet=group&group=7'))).toEqual({ kind: 'group', id: 7 })
    expect(alertOverlayOf(params('sheet=group&group=0'))).toBeNull()
    expect(alertOverlayOf(params('sheet=group'))).toBeNull()
  })

  it('builds the address the watchlist opens, and parses it back', () => {
    const address = listAlertAddress({
      ref: 'list:swing & co',
      name: 'Swing & co',
      count: 24,
      fed: true,
    })
    expect(address.startsWith('/market-alerts?sheet=list&')).toBe(true)
    expect(alertOverlayOf(new URL(address, 'http://x').searchParams)).toEqual({
      kind: 'list',
      ref: 'list:swing & co',
      name: 'Swing & co',
      count: 24,
      fed: true,
    })
    expect(listAlertAddress({ ref: 'a', name: 'A', count: 0 })).not.toContain('fed')
  })

  it('leaves the count out when it is not known, and reads that back as none (lane F, F10)', () => {
    const address = listAlertAddress({ ref: 'preset:fno_stocks', name: 'F&O stocks' })
    expect(address).not.toContain('count')
    expect(alertOverlayOf(new URL(address, 'http://x').searchParams)).toMatchObject({
      kind: 'list',
      count: null,
    })
    // A known count of 0 is still said: an own list can be empty.
    expect(listAlertAddress({ ref: 'a', name: 'A', count: 0 })).toContain('count=0')
  })
})

function wrapper(entries: Array<string | { pathname: string; search: string; state?: unknown }>) {
  return ({ children }: { children: ReactNode }) =>
    createElement(
      MemoryRouter,
      { initialEntries: entries, initialIndex: entries.length - 1 },
      children
    )
}

function both() {
  const overlay = useAlertOverlay()
  return { ...overlay, location: useLocation() }
}

describe('opening and closing the new kinds', () => {
  it('opens a list form over the cards with an entry of its own, and Back leaves it', () => {
    const { result } = renderHook(both, { wrapper: wrapper(['/market-alerts']) })
    act(() =>
      result.current.open({ kind: 'list', ref: 'list:swing', name: 'Swing', count: 24, fed: false })
    )
    expect(result.current.overlay).toEqual({
      kind: 'list',
      ref: 'list:swing',
      name: 'Swing',
      count: 24,
      fed: false,
    })
    expect(result.current.location.search).toContain('sheet=list')
    expect((result.current.location.state as { sdOverlay: boolean }).sdOverlay).toBe(true)
    act(() => result.current.close())
    expect(result.current.overlay).toBeNull()
    expect(result.current.location.search).toBe('')
  })

  it('swaps a group form for the one open and keeps its mark; no list query is left behind', () => {
    const { result } = renderHook(both, { wrapper: wrapper(['/market-alerts']) })
    act(() => result.current.open({ kind: 'list', ref: 'a', name: 'A', count: 1, fed: true }))
    act(() => result.current.open({ kind: 'group', id: 7 }))
    expect([...new URLSearchParams(result.current.location.search)].sort()).toEqual([
      ['group', '7'],
      ['sheet', 'group'],
    ])
    expect((result.current.location.state as { sdOverlay: boolean }).sdOverlay).toBe(true)
  })

  it('closes a form that arrived by a link by replacing the entry', () => {
    const { result } = renderHook(both, {
      wrapper: wrapper(['/market-alerts?sheet=list&ref=list%3Aswing&name=Swing&count=24']),
    })
    expect(result.current.overlay).toMatchObject({ kind: 'list', ref: 'list:swing' })
    act(() => result.current.close())
    expect(result.current.overlay).toBeNull()
    expect(result.current.location.pathname).toBe('/market-alerts')
  })
})
