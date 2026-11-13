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

const IST_HHMM = new Intl.DateTimeFormat('en-IN', {
  timeZone: 'Asia/Kolkata',
  hour: '2-digit',
  minute: '2-digit',
  hourCycle: 'h23',
})

/** When the page last learned the nearest expiry stops: that expiry and its stop (epoch ms). */
export interface ExpiryStop {
  expiry: string
  at: number
}

/**
 * SUDARSHAN (v0.8.1 review N5): what the page says when the expiry it showed is no longer
 * offered and it moves on to the next one, so a key pressed a moment later is not a surprise.
 * Nothing when nothing was picked yet (the first load) or nothing moved.
 */
export function rollNotice(current: string, next: string, stop?: ExpiryStop | null): string | null {
  if (!current || !next || current === next) return null
  const stopped =
    stop && stop.expiry === current
      ? `stopped trading at ${IST_HHMM.format(stop.at)}`
      : 'is no longer traded'
  return `The ${current} expiry ${stopped}. Now showing ${next}.`
}
