/**
 * A refused order in Analyze mode (SUDARSHAN): the toast shows the server's own sentence after
 * the symbol, and never the word "Error", which reads as a fault the trader caused when it is
 * a limit the platform kept (the daily loss lock, the price band, the kill switch).
 */
import { renderHook } from '@testing-library/react'
import type { ReactNode } from 'react'
import { MemoryRouter } from 'react-router'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const world = vi.hoisted(() => {
  const handlers = new Map<string, Array<(...args: unknown[]) => void>>()
  const socket = {
    on(event: string, handler: (...args: unknown[]) => void) {
      handlers.set(event, [...(handlers.get(event) ?? []), handler])
      return socket
    },
    off() {
      return socket
    },
  }
  return { handlers, socket, toast: { error: vi.fn(), success: vi.fn(), info: vi.fn() } }
})

vi.mock('socket.io-client', () => ({ io: () => world.socket }))
vi.mock('sonner', () => ({ toast: world.toast }))
vi.mock('@/sudarshan/books/sharedSocket', () => ({
  acquireSharedSocket: (make: () => unknown) => make(),
  releaseSharedSocket: () => {},
}))
vi.mock('@/sudarshan/books/orderAlerts', () => ({
  installOrderAlerts: () => () => {},
  noteAppCancelEvent: () => {},
}))
vi.mock('@/sudarshan/alerts/marketAlerts', () => ({ installMarketAlerts: () => {} }))

import { useAuthStore } from '@/stores/authStore'
import { useSocket } from './useSocket'

const wrapper = ({ children }: { children: ReactNode }) => <MemoryRouter>{children}</MemoryRouter>

function analyzerUpdate(symbol: string | undefined, message: string) {
  const fire = world.handlers.get('analyzer_update')?.[0]
  expect(fire).toBeTypeOf('function')
  fire?.({
    request: { api_type: 'placeorder', symbol, action: 'BUY' },
    response: { status: 'error', message },
  })
}

beforeEach(() => {
  world.handlers.clear()
  vi.clearAllMocks()
  vi.stubGlobal(
    'Audio',
    class {
      play = () => Promise.resolve()
    }
  )
  useAuthStore.setState({ isAuthenticated: true })
})
afterEach(() => vi.unstubAllGlobals())

describe('a refused order in Analyze mode', () => {
  it("shows only the server's sentence: no symbol and no Error in front", () => {
    // Backlog 2d (Analyzer session): it read "NIFTY17NOV2825200CE - Error: ...", then
    // "NIFTY17NOV2825200CE: ...".
    renderHook(() => useSocket(), { wrapper })
    analyzerUpdate('NIFTY17NOV2825200CE', 'Daily loss limit reached: new orders are locked.')
    expect(world.toast.error).toHaveBeenCalledWith(
      'Daily loss limit reached: new orders are locked.'
    )
  })

  it('names the symbol only when the server gave no sentence', () => {
    renderHook(() => useSocket(), { wrapper })
    analyzerUpdate('SBIN', '')
    expect(world.toast.error).toHaveBeenCalledWith('SBIN')
  })

  it('shows the sentence alone when the order names no symbol, still in the error style', () => {
    renderHook(() => useSocket(), { wrapper })
    analyzerUpdate(undefined, 'The kill switch is on.')
    expect(world.toast.error).toHaveBeenCalledWith('The kill switch is on.')
  })
})

describe('a smart order that sent nothing, in Live mode', () => {
  it("shows the server's sentence alone, as Analyze mode shows the same answer", () => {
    // It read "SBIN: infoPositions Already Matched. No Action needed.": the symbol and the
    // status word glued in front of the sentence.
    renderHook(() => useSocket(), { wrapper })
    const fire = world.handlers.get('order_notification')?.[0]
    expect(fire).toBeTypeOf('function')
    fire?.({
      symbol: 'SBIN',
      status: 'info',
      message: 'Positions Already Matched. No Action needed.',
    })
    expect(world.toast.info).toHaveBeenCalledWith('Positions Already Matched. No Action needed.')
  })
})
