import { afterEach, describe, expect, it, vi } from 'vitest'
import { downloadText, REVOKE_MS } from './tvTransfer'

/** Review L10: the file's address outlives the tap, so a browser that starts the download late still has it. */
describe('downloadText', () => {
  afterEach(() => {
    vi.useRealTimers()
    vi.restoreAllMocks()
  })

  it('keeps the file’s address for a minute after the tap, then lets it go', () => {
    vi.useFakeTimers()
    const create = vi.fn(() => 'blob:export')
    const revoke = vi.fn()
    Object.assign(URL, { createObjectURL: create, revokeObjectURL: revoke })
    const click = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {})
    downloadText('NSE:SBIN', 'Swing.txt')
    expect(click).toHaveBeenCalledTimes(1)
    expect(REVOKE_MS).toBe(60_000)
    vi.advanceTimersByTime(REVOKE_MS - 1)
    expect(revoke).not.toHaveBeenCalled()
    vi.advanceTimersByTime(1)
    expect(revoke).toHaveBeenCalledWith('blob:export')
  })
})
