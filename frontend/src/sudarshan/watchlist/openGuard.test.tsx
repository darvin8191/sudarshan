/**
 * A sheet or a confirmation that has just opened takes no tap for half a
 * second (M6.9). The second tap of a double tap lands 20 to 250 ms after the
 * first, on whatever has just opened under the finger: on a tall phone the
 * order sheet's Cancel and the confirmation's "Cancel order" sit on the same
 * spot, and a card's second tap lands on the sheet's Chart, Add or Cancel.
 */
import { act, fireEvent, render, screen } from '@testing-library/react'
import { useState } from 'react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('@/stores/authStore', () => ({
  useAuthStore: () => ({ apiKey: 'test-api-key' }),
}))
vi.mock('@/hooks/useLiveQuote', () => ({
  useLiveQuote: () => ({
    data: { ltp: 152.5, close: 131.45, depth: undefined },
    isLive: false,
    isConnected: false,
    isLoading: false,
    isPaused: false,
    isFallbackMode: false,
    dataSource: 'none',
    refresh: async () => {},
  }),
}))

import { PlaceOrderDialog } from '@/components/trading/PlaceOrderDialog'
import { Confirm } from '@/sudarshan/books/cards/parts'
import { BottomSheet } from './BottomSheet'
import { OPEN_GUARD } from './openGuard'

beforeEach(() => {
  OPEN_GUARD.ms = 500
  vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout', 'Date'] })
})

afterEach(() => {
  vi.useRealTimers()
})

const wait = (ms: number) => act(() => vi.advanceTimersByTime(ms))

/**
 * A finger's tap, every event of it, dispatched at once. Not user-event: its
 * wrapper waits on a timer that a clock held still never fires, and these
 * tests hold the clock still to tell 499 ms from 500.
 */
function tap(element: Element) {
  fireEvent.pointerDown(element)
  fireEvent.mouseDown(element)
  fireEvent.pointerUp(element)
  fireEvent.mouseUp(element)
  fireEvent.click(element)
}

/** A card that opens a sheet, as the books and the watchlist have it. */
function SheetHarness({
  onCancel,
  onOpenChange,
}: {
  onCancel: () => void
  onOpenChange?: (open: boolean) => void
}) {
  const [open, setOpen] = useState(false)
  return (
    <>
      <button type="button" onClick={() => setOpen(true)}>
        NIFTY 25900 CE
      </button>
      <BottomSheet
        open={open}
        onOpenChange={(next) => {
          onOpenChange?.(next)
          setOpen(next)
        }}
        title="NIFTY 25900 CE"
      >
        <button type="button" onClick={onCancel}>
          Cancel this order
        </button>
        <input aria-label="Quantity" />
      </BottomSheet>
    </>
  )
}

/** The sheet's Cancel, which opens the confirmation in the same spot. */
function ConfirmHarness({ onConfirm, onClose }: { onConfirm: () => void; onClose: () => void }) {
  const [open, setOpen] = useState(false)
  return (
    <>
      <button type="button" onClick={() => setOpen(true)}>
        Cancel
      </button>
      <Confirm
        open={open}
        title="Cancel this order?"
        description="SELL 65 NIFTY 25900 CE at 168.00"
        cancelLabel="Keep order"
        confirmLabel="Cancel order"
        onConfirm={onConfirm}
        onClose={() => {
          onClose()
          setOpen(false)
        }}
      />
    </>
  )
}

describe('a sheet that has just opened', () => {
  it('ignores a tap on its contents for half a second, then takes it', async () => {
    const onCancel = vi.fn()
    render(<SheetHarness onCancel={onCancel} />)

    tap(screen.getByRole('button', { name: 'NIFTY 25900 CE' }))
    const cancel = screen.getByRole('button', { name: 'Cancel this order' })
    tap(cancel)
    await wait(499)
    tap(cancel)
    expect(onCancel).not.toHaveBeenCalled()

    await wait(1)
    tap(cancel)
    expect(onCancel).toHaveBeenCalledTimes(1)
  })

  it('swallows a press, Enter and Space inside the window, but not typing', async () => {
    render(<SheetHarness onCancel={vi.fn()} />)
    fireEvent.click(screen.getByRole('button', { name: 'NIFTY 25900 CE' }))
    const cancel = screen.getByRole('button', { name: 'Cancel this order' })
    const field = screen.getByRole('textbox', { name: 'Quantity' })

    // fireEvent returns false when the event's default was prevented.
    expect(fireEvent.pointerDown(cancel)).toBe(false)
    expect(fireEvent.keyDown(cancel, { key: 'Enter' })).toBe(false)
    expect(fireEvent.keyDown(cancel, { key: ' ' })).toBe(false)
    expect(fireEvent.keyDown(field, { key: 'Enter' })).toBe(false)
    expect(fireEvent.keyDown(field, { key: ' ' })).toBe(true)
    expect(fireEvent.keyDown(field, { key: '6' })).toBe(true)

    await wait(500)
    expect(fireEvent.pointerDown(cancel)).toBe(true)
    expect(fireEvent.keyDown(cancel, { key: 'Enter' })).toBe(true)
    expect(fireEvent.keyDown(cancel, { key: ' ' })).toBe(true)
  })

  it('still closes at once on Escape: closing is always safe', async () => {
    const onOpenChange = vi.fn()
    render(<SheetHarness onCancel={vi.fn()} onOpenChange={onOpenChange} />)
    fireEvent.click(screen.getByRole('button', { name: 'NIFTY 25900 CE' }))
    fireEvent.keyDown(screen.getByRole('dialog'), { key: 'Escape' })
    expect(onOpenChange).toHaveBeenCalledWith(false)
    expect(screen.queryByRole('dialog')).toBeNull()
  })

  it('starts a fresh window each time it opens', async () => {
    const onCancel = vi.fn()
    render(<SheetHarness onCancel={onCancel} />)
    tap(screen.getByRole('button', { name: 'NIFTY 25900 CE' }))
    await wait(600)
    fireEvent.keyDown(screen.getByRole('dialog'), { key: 'Escape' })

    tap(screen.getByRole('button', { name: 'NIFTY 25900 CE' }))
    tap(screen.getByRole('button', { name: 'Cancel this order' }))
    expect(onCancel).not.toHaveBeenCalled()
  })
})

describe('a confirmation that has just opened', () => {
  it('shows its action as disabled for half a second, and the action does nothing', async () => {
    const onConfirm = vi.fn()
    render(<ConfirmHarness onConfirm={onConfirm} onClose={vi.fn()} />)

    tap(screen.getByRole('button', { name: 'Cancel' }))
    const action = screen.getByRole('button', { name: 'Cancel order' })
    expect(action).toBeDisabled()
    tap(action)
    fireEvent.click(action)
    expect(onConfirm).not.toHaveBeenCalled()

    await wait(500)
    expect(action).toBeEnabled()
    tap(action)
    expect(onConfirm).toHaveBeenCalledTimes(1)
  })

  it('lets no tap or key through to its buttons inside the window, its other button included', async () => {
    const onConfirm = vi.fn()
    const onClose = vi.fn()
    render(<ConfirmHarness onConfirm={onConfirm} onClose={onClose} />)

    tap(screen.getByRole('button', { name: 'Cancel' }))
    const keep = screen.getByRole('button', { name: 'Keep order' })
    const action = screen.getByRole('button', { name: 'Cancel order' })
    tap(keep)
    expect(fireEvent.keyDown(action, { key: 'Enter' })).toBe(false)
    expect(fireEvent.keyDown(keep, { key: ' ' })).toBe(false)
    expect(onConfirm).not.toHaveBeenCalled()
    expect(onClose).not.toHaveBeenCalled()
    expect(screen.getByRole('dialog')).toBeInTheDocument()
  })

  it('ships with the window at half a second', async () => {
    vi.resetModules()
    const fresh = await import('./openGuard')
    expect(fresh.OPEN_GUARD.ms).toBe(500)
  })
})

describe('an order ticket that has just opened', () => {
  it('keeps its side: a second tap on SELL does nothing for half a second', async () => {
    // A double tap on a long position's Add, at 412x915, lands its second tap on SELL.
    function Ticket() {
      const [open, setOpen] = useState(false)
      return (
        <>
          <button type="button" onClick={() => setOpen(true)}>
            Add
          </button>
          <PlaceOrderDialog
            open={open}
            onOpenChange={setOpen}
            symbol="NIFTY27OCT2625900CE"
            exchange="NFO"
            action="BUY"
            lotSize={65}
            tickSize={0.05}
            product="MIS"
            strategy="Positions"
          />
        </>
      )
    }
    render(<Ticket />)
    tap(screen.getByRole('button', { name: 'Add' }))
    const title = () => screen.getByRole('dialog').querySelector('h2')?.textContent
    expect(title()).toContain('BUY')
    tap(screen.getByRole('button', { name: 'SELL' }))
    expect(title()).toContain('BUY')
    expect(screen.getByRole('button', { name: /^Place BUY Order/ })).toBeDisabled()

    await wait(500)
    tap(screen.getByRole('button', { name: 'SELL' }))
    expect(title()).toContain('SELL')
  })
})
