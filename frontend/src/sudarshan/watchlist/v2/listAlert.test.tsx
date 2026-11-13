import { QueryClientProvider } from '@tanstack/react-query'
import { act, fireEvent, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { createQueryClient } from '@/app/providers'
import { alertOverlayOf, listAlertAddress } from '@/sudarshan/alerts/useAlertOverlay'
import { ListTabs } from '../ListTabs'
import { OPEN_GUARD } from '../openGuard'
import { SectionSheets } from '../SectionMenus'
import { ALERTS_OFF, type AlertOffer, offerFor, RANKED_OFF, sectionRef, V2_OFF } from './listAlert'
import type { RefKind, Section, View } from './types'

/** F17: "Alert on this list" and "Alert on this section" build the Alerts page's address. */

const ON = { v2: true, alerts: true }
const list = (ref: string, name: string, count: number, kind: RefKind = 'list', fed = false) => ({
  ref,
  name,
  count,
  kind,
  fed,
})

beforeEach(() => {
  OPEN_GUARD.ms = 0
})
afterEach(() => vi.useRealTimers())

describe('the offer', () => {
  it.each([
    ['an own list', list('list:7', 'Swing', 24)],
    ['a section', list(sectionRef('list:7', 2), 'Banks', 6)],
    ['a built-in list', list('preset:fno', 'F&O stocks', 180, 'preset')],
    ['a flag list', list('flag:2', 'Review', 3, 'flag')],
  ])('opens the address of %s through listAlertAddress', (_name, one) => {
    const go = vi.fn()
    const offer = offerFor(one, ON, go)
    expect(offer.off).toBeNull()
    expect(offer.href).toBe(
      listAlertAddress({ ref: one.ref, name: one.name, count: one.count, fed: false })
    )
    // The Alerts page reads the same ref, name and count back.
    const url = new URL(offer.href as string, 'http://x')
    expect(alertOverlayOf(url.searchParams)).toEqual({
      kind: 'list',
      ref: one.ref,
      name: one.name,
      count: one.count,
      fed: false,
    })
  })

  it('adds fed=1 only when a screen feeds the list', () => {
    const fed = offerFor(list('list:7', 'Swing', 24, 'list', true), ON, vi.fn())
    expect(fed.href).toContain('&fed=1')
    expect(offerFor(list('list:7', 'Swing', 24), ON, vi.fn()).href).not.toContain('fed')
  })

  it('refuses a ranked list in the service’s own sentence', () => {
    const offer = offerFor(list('ranked:oi', 'OI gainers', 30, 'ranked'), ON, vi.fn())
    expect(offer).toMatchObject({ href: null, off: RANKED_OFF })
    expect(RANKED_OFF).toBe(
      'A ranked list changes with every read. Set the alert on a list or a built-in list instead.'
    )
  })

  it('says why it is off when the watchlist features or market alerts are off', () => {
    const one = list('list:7', 'Swing', 24)
    expect(offerFor(one, { v2: false, alerts: true }, vi.fn())).toMatchObject({
      href: null,
      off: V2_OFF,
    })
    expect(offerFor(one, { v2: true, alerts: false }, vi.fn())).toMatchObject({
      href: null,
      off: ALERTS_OFF,
    })
    // The first reason wins, so one sentence is shown.
    expect(offerFor(one, { v2: false, alerts: false }, vi.fn()).off).toBe(V2_OFF)
  })
})

describe('the list menu', () => {
  const lists = [{ id: 1, name: 'Mine', position: 0, items: [] }]
  const open = async (alert: AlertOffer) => {
    const user = userEvent.setup()
    render(
      <ListTabs
        lists={lists}
        activeId={1}
        editing={false}
        onSelect={() => {}}
        onCreate={() => {}}
        onRename={() => {}}
        onEdit={() => {}}
        onDelete={() => {}}
        strip={<div />}
        alert={alert}
      />
    )
    await user.click(screen.getByRole('button', { name: 'List options' }))
    return user
  }

  it('opens the address of the list on show', async () => {
    const go = vi.fn()
    const user = await open(offerFor(list('list:1', 'Mine', 5), ON, go))
    await user.click(await screen.findByRole('menuitem', { name: 'Alert on this list' }))
    expect(go).toHaveBeenCalledWith(
      listAlertAddress({ ref: 'list:1', name: 'Mine', count: 5, fed: false })
    )
  })

  it.each([
    [RANKED_OFF, list('ranked:oi', 'OI gainers', 30, 'ranked'), ON],
    [V2_OFF, list('list:1', 'Mine', 5), { v2: false, alerts: true }],
    [ALERTS_OFF, list('list:1', 'Mine', 5), { v2: true, alerts: false }],
  ])('shows the item disabled with its reason: %s', async (words, one, on) => {
    const go = vi.fn()
    const user = await open(offerFor(one, on, go))
    const item = await screen.findByRole('menuitem', { name: 'Alert on this list' })
    expect(item).toHaveAttribute('aria-disabled', 'true')
    expect(item).toHaveAccessibleDescription(words)
    await user.click(item)
    expect(go).not.toHaveBeenCalled()
  })
})

describe('the section menu', () => {
  const section = (id: number, name: string, count: number): Section => ({
    id,
    name,
    mark: 0,
    position: id,
    count,
  })
  const view = { sections: [section(10, 'Banks', 2)], rows: [] } as unknown as View

  function sheet(alertFor: (ref: string, name: string, count: number) => AlertOffer) {
    return render(
      <QueryClientProvider client={createQueryClient()}>
        <MemoryRouter>
          <SectionSheets
            overlay={{ kind: 'section', sectionId: 10 }}
            view={view}
            listId={1}
            alertFor={alertFor}
            onSwap={() => {}}
            onClose={() => {}}
          />
        </MemoryRouter>
      </QueryClientProvider>
    )
  }
  const tap = (element: Element) => {
    fireEvent.pointerDown(element)
    fireEvent.mouseDown(element)
    fireEvent.pointerUp(element)
    fireEvent.mouseUp(element)
    fireEvent.click(element)
  }

  it('opens the section’s address with its list’s ref, its name and its rows', async () => {
    const go = vi.fn()
    const asked = vi.fn((ref: string, name: string, count: number) =>
      offerFor(list(ref, name, count), ON, go)
    )
    sheet(asked)
    await userEvent.setup().click(screen.getByRole('button', { name: 'Alert on this section' }))
    expect(asked).toHaveBeenCalledWith('list:1/section:10', 'Banks', 2)
    expect(go).toHaveBeenCalledWith(
      listAlertAddress({ ref: 'list:1/section:10', name: 'Banks', count: 2, fed: false })
    )
  })

  it('shows the item disabled with its reason, never hidden', () => {
    sheet((ref, name, count) =>
      offerFor(list(ref, name, count), { v2: true, alerts: false }, vi.fn())
    )
    const item = screen.getByRole('button', { name: 'Alert on this section' })
    expect(item).toBeDisabled()
    expect(item).toHaveAccessibleDescription(ALERTS_OFF)
  })

  it('ignores a tap for 500 ms after the sheet opens', () => {
    OPEN_GUARD.ms = 500
    vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout', 'Date'] })
    const go = vi.fn()
    sheet((ref, name, count) => offerFor(list(ref, name, count), ON, go))
    const item = screen.getByRole('button', { name: 'Alert on this section' })
    act(() => vi.advanceTimersByTime(499))
    tap(item)
    expect(go).not.toHaveBeenCalled()
    act(() => vi.advanceTimersByTime(2))
    tap(item)
    expect(go).toHaveBeenCalledOnce()
  })
})
