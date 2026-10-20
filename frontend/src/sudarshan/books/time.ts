const IST_TIME = new Intl.DateTimeFormat('en-IN', {
  timeZone: 'Asia/Kolkata',
  hour: '2-digit',
  minute: '2-digit',
  second: '2-digit',
  hourCycle: 'h23',
})

/** "10:42:31": a moment as the clock in India shows it. */
export function istTime(at: number): string {
  return IST_TIME.format(at)
}
