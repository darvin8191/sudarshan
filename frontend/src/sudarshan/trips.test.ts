/**
 * At most one automatic trip a minute between the login page and the app (SIO1 round 3): the
 * stop that ends the login loop whatever the server answers.
 */
import { afterEach, describe, expect, it, vi } from 'vitest'
import { claimTrip, resetTripsForTests, TRIP_GAP_MS } from './trips'

afterEach(() => {
  vi.restoreAllMocks()
  resetTripsForTests()
})

describe('automatic trips', () => {
  it('allows one trip of each kind a minute', () => {
    expect(claimTrip('login', 1_000)).toBe(true)
    expect(claimTrip('login', 1_000 + TRIP_GAP_MS - 1)).toBe(false)
    expect(claimTrip('dashboard', 2_000)).toBe(true) // the other kind keeps its own minute
    expect(claimTrip('login', 1_000 + TRIP_GAP_MS)).toBe(true)
  })

  it('remembers a trip across a reload of the page', async () => {
    expect(claimTrip('login', 1_000)).toBe(true)
    vi.resetModules()
    const reloaded = await import('./trips') // a fresh page: only this tab's storage is left
    expect(reloaded.claimTrip('login', 2_000)).toBe(false)
    expect(reloaded.claimTrip('login', 1_000 + TRIP_GAP_MS)).toBe(true)
  })

  it('keeps the minute in the page memory when storage cannot be used', () => {
    vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => {
      throw new Error('storage is off')
    })
    vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => {
      throw new Error('storage is off')
    })
    expect(claimTrip('dashboard', 1_000)).toBe(true)
    expect(claimTrip('dashboard', 30_000)).toBe(false)
  })

  it('is not held back for good by a clock that was set back', () => {
    expect(claimTrip('login', 100_000)).toBe(true)
    expect(claimTrip('login', 50_000)).toBe(true)
  })
})
