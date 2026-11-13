/**
 * SUDARSHAN (v0.8.1 review M3): the scalping terminal's expiry on an expiry
 * day. From 7 Dec 2026 an expiring index contract stops trading at 15:30
 * while F&O runs to 15:45 (until 6 Dec every contract stops at 15:40). The
 * server leaves today's expiry out of its list from that stop and says when
 * the nearest one offered stops today (`rolls_at`), so the page asks again
 * then and its expiry moves on to the next one.
 */

/** How long until the expiry list is asked for again: just after today's nearest stops, or never. */
export function rollRefetchMs(
  rollsAt: number | null | undefined,
  now = Date.now()
): number | false {
  return typeof rollsAt === 'number' && rollsAt > now ? rollsAt - now + 500 : false
}

/** The expiry to show: the one picked while it is still offered, else the nearest offered. */
export function pickExpiry(current: string, offered: readonly string[]): string {
  if (!offered.length) return current
  return current && offered.includes(current) ? current : offered[0]
}
