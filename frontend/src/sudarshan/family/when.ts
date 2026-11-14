// SUDARSHAN P6 D3: dates as a phone in India shows them: "2 Nov", "today", "18:42". The clock is
// an argument so a test can hold it still.
const DAY = new Intl.DateTimeFormat('en-IN', {
  timeZone: 'Asia/Kolkata',
  day: 'numeric',
  month: 'short',
})
const DAY_KEY = new Intl.DateTimeFormat('en-CA', { timeZone: 'Asia/Kolkata' })
const CLOCK = new Intl.DateTimeFormat('en-IN', {
  timeZone: 'Asia/Kolkata',
  hour: '2-digit',
  minute: '2-digit',
  hourCycle: 'h23',
})

const DAY_MS = 86_400_000

/** "today", "yesterday" or "2 Nov": the day a moment fell on, in India. */
export function dayWords(at: number, now: number): string {
  const key = DAY_KEY.format(at)
  if (key === DAY_KEY.format(now)) return 'today'
  if (key === DAY_KEY.format(now - DAY_MS)) return 'yesterday'
  return DAY.format(at)
}

/** "18:42". */
export function clock(at: number): string {
  return CLOCK.format(at)
}

/** "Signed in 2 Nov · last used today". */
export function deviceWhen(createdAt: number, lastSeenAt: number, now: number): string {
  return `Signed in ${dayWords(createdAt, now)} · last used ${dayWords(lastSeenAt, now)}`
}
