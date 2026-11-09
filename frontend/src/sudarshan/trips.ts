/**
 * At most one automatic trip a minute between the login page and the app, whatever the server
 * answers (SIO1, round 3).
 *
 * After a logout on another device the server and the page disagreed about a cookie: the login
 * page sent the phone to the dashboard, whose guard sent it straight back, about 300 reads in two
 * seconds. The server now answers both sides alike, and this is the client's own stop: a trip that
 * would repeat within a minute is not taken, and the page says why instead (the login form, or
 * "Sign in again" on the order updates line). A trip can reload the page, so the time of the last
 * one is kept in sessionStorage (this tab only), with this page's memory as the fallback when
 * storage cannot be used.
 */

/** The least time between two automatic trips of one kind. */
export const TRIP_GAP_MS = 60_000

/** The kinds of automatic trip: to the login page, and from it to the dashboard. */
export type Trip = 'login' | 'dashboard'

const KEY = 'sudarshan.trip.'
const remembered = new Map<Trip, number>()

function stored(trip: Trip): number {
  try {
    return Number(window.sessionStorage.getItem(KEY + trip)) || 0
  } catch {
    return 0
  }
}

/** Whether this tab may take `trip` by itself now; when it may, the trip is noted. */
export function claimTrip(trip: Trip, now: number = Date.now()): boolean {
  const last = Math.max(remembered.get(trip) ?? 0, stored(trip))
  if (last > 0 && now >= last && now - last < TRIP_GAP_MS) return false
  remembered.set(trip, now)
  try {
    window.sessionStorage.setItem(KEY + trip, String(now))
  } catch {
    // This page's memory only.
  }
  return true
}

/** Tests only: forget every trip. */
export function resetTripsForTests(): void {
  remembered.clear()
  for (const trip of ['login', 'dashboard'] as const) {
    try {
      window.sessionStorage.removeItem(KEY + trip)
    } catch {
      // Nothing stored.
    }
  }
}
