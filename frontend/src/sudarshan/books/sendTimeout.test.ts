/**
 * An exit or a cancel waits 45 s for its answer and no longer (M6.9): past it,
 * the request is given up and reads as "not confirmed", never as failed. The
 * web client sets no timeout of its own, so without this an exit in flight said
 * "Sending exit" for as long as the request hung (nginx ends it at 300 s).
 */
import { AxiosError } from 'axios'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { webClient } from '@/api/client'
import { SEND_TIMEOUT_MS, tradingApi } from '@/api/trading'
import { thrownOutcome } from './sendOutcome'

afterEach(() => {
  vi.restoreAllMocks()
})

describe('an exit or a cancel', () => {
  it.each([
    [
      'Exit',
      () => tradingApi.closePosition('NIFTY27OCT2625900CE', 'NFO', 'MIS'),
      '/close_position',
    ],
    ['Exit all', () => tradingApi.closeAllPositions(), '/close_all_positions'],
    ['Cancel', () => tradingApi.cancelOrder('1001'), '/cancel_order'],
    ['Cancel all', () => tradingApi.cancelAllOrders(), '/cancel_all_orders'],
  ])('%s waits 45 s for its answer', async (_, send, path) => {
    const post = vi.spyOn(webClient, 'post').mockResolvedValue({ data: { status: 'success' } })
    await send()
    expect(post).toHaveBeenCalledWith(path, expect.anything(), { timeout: 45_000 })
    expect(SEND_TIMEOUT_MS).toBe(45_000)
  })

  it('given up at 45 s, is not confirmed: it may have gone', () => {
    // What axios throws when its timeout runs out: no answer at all.
    const timedOut = new AxiosError('timeout of 45000ms exceeded', AxiosError.ECONNABORTED)
    expect(thrownOutcome(timedOut)).toBe('unconfirmed')
  })
})
