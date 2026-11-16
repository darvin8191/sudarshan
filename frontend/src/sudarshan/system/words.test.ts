/**
 * The System page's words (M20.8): every check the server's watch makes has a label and a line
 * for each level, in the owner's words, and odd facts never reach the screen.
 */
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { describe, expect, it } from 'vitest'
import type { CheckLevel, WatchCheck } from '@/api/system'
import { GROUPS, LEVEL_WORDS, lineOf, whenOf } from './words'

const check = (
  name: string,
  level: CheckLevel,
  facts: Record<string, string> = {}
): WatchCheck => ({
  name,
  level,
  facts,
})

/** The checks the server runs, read from its own vectors' check names. */
const VECTORS = JSON.parse(
  readFileSync(join(process.cwd(), '..', 'sudarshan', 'ops', 'tests', 'watch_vectors.json'), 'utf8')
) as { checks: { check: string; level: CheckLevel; facts?: Record<string, string> }[] }

describe('the System page words', () => {
  it('has a line for every check and level the server has a vector for', () => {
    expect(VECTORS.checks.length).toBeGreaterThan(60)
    const groups = new Set(GROUPS.map((group) => group.id))
    for (const vector of VECTORS.checks) {
      const line = lineOf(check(vector.check, vector.level, vector.facts ?? {}))
      expect(line, vector.check).not.toBeNull()
      expect(groups.has(line?.group ?? ('' as never)), vector.check).toBe(true)
      expect(line?.label.length, vector.check).toBeGreaterThan(2)
      expect(line?.detail, `${vector.check} ${vector.level}`).toMatch(/[.]$/)
      expect(line?.detail).not.toMatch(/undefined|null|NaN|service_|posture_|sudarshan-/)
    }
  })

  it('says each level as a word', () => {
    expect(LEVEL_WORDS).toEqual({
      fine: 'Fine',
      warning: 'Look at',
      failing: 'Not working',
      unread: 'Could not be read',
      off: 'Off',
    })
  })

  it('tells what to do where there is something to do', () => {
    expect(lineOf(check('service_feed', 'failing', { minutes: '6' }))?.detail).toBe(
      'Not answering for 6 minutes. The server restarts it by itself.'
    )
    expect(lineOf(check('login', 'failing', { when: 'before_open' }))?.detail).toBe(
      'No login for today. Connect Upstox in the app.'
    )
    expect(lineOf(check('instrument_list', 'failing'))?.detail).toContain('press Download')
    expect(lineOf(check('disk', 'warning', { free_gb: '12.0', cause: 'space' }))?.detail).toBe(
      '12.0 GB free. Getting full.'
    )
    expect(lineOf(check('certificate', 'fine', { hours: '120' }))?.detail).toBe('5 days left.')
    expect(lineOf(check('certificate', 'failing', { hours: '31' }))?.detail).toBe(
      '31 hours left and not renewed.'
    )
    // verify N6: no certificate is being judged, and the page says so, with the setting to add,
    // before the two days are up (unread) and after (warning).
    for (const level of ['unread', 'warning'] as const) {
      const unknown = lineOf(check('certificate', level, { cause: 'no_name' }))?.detail
      expect(unknown).toMatch(/^Not being checked: the watch does not know the app's address\./)
      expect(unknown).toContain('SUDARSHAN_WATCH_CERT_HOST')
      expect(lineOf(check('certificate', level, { cause: 'other_site' }))?.detail).toMatch(
        /^Not being checked: the web server answers the app's address with another site's certificate\./
      )
    }
    expect(lineOf(check('certificate', 'unread'))?.detail).toBe(
      'Could not be read at the last look.'
    )
    expect(lineOf(check('posture_locks', 'warning', { cause: 'locks' }))?.detail).toContain(
      'OPENALGO_MCP_READ_ONLY=1'
    )
    expect(lineOf(check('posture_modes', 'warning', { what: 'vault' }))?.detail).toMatch(
      /^The folder of sealed Upstox sign-in details can be read by other accounts/
    )
    expect(lineOf(check('feed', 'failing'))?.detail).toBe(
      'Not connected. Prices are not Real-time.'
    )
  })

  it('shows the backup, the year archive and the restore test', () => {
    const backup = check('backup', 'fine', {
      age_h: '17',
      size_mb: '412',
      offsite: 'ok',
      fy: '2026-27',
      fy_mb: '398',
    })
    expect(lineOf(backup)?.detail).toBe(
      "Newest is 17 hours old (412 MB). Offsite copy: sent. The 2026-27 year's archive is kept (398 MB)."
    )
    // Review L4: a year's archive whose offsite copy has not gone yet is said until it has.
    expect(
      lineOf(
        check('backup', 'warning', {
          age_h: '17',
          offsite: 'ok',
          fy: '2026-27',
          fy_owed: '2026-27',
        })
      )?.detail
    ).toBe(
      "Newest is 17 hours old. Offsite copy: sent. The 2026-27 year's archive is kept. The 2026-27 year's archive has no offsite copy yet. It is sent again at the next backup."
    )
    expect(lineOf(check('backup', 'fine', { age_h: '17', fy_owed: '<script>' }))?.detail).toBe(
      'Newest is 17 hours old.'
    )
    expect(lineOf(check('backup', 'off'))?.detail).toBe('No backup has been made yet.')
    expect(lineOf(check('drill', 'fine', { days: '14' }))?.detail).toBe('Passed 14 days ago.')
    expect(lineOf(check('drill', 'fine', { days: '0' }))?.detail).toBe('Passed today.')
    expect(lineOf(check('drill', 'warning', { cause: 'never' }))?.detail).toMatch(/^Never done yet/)
    const failed = check('drill', 'warning', { cause: 'failed', days: '1' })
    const words = 'The vault in the archive does not open with the keys in the same archive.'
    expect(
      lineOf(failed, { at: '2026-11-01T11:00:05+05:30', outcome: 'failed', words })?.detail
    ).toBe(`FAILED 1 day ago. ${words} Have the server looked at.`)
    expect(lineOf(failed)?.detail).toBe(
      'FAILED 1 day ago. The backups may not restore. Have the server looked at.'
    )
  })

  it('never shows a fact that is not a plain figure', () => {
    const odd = '<img src=x onerror=alert(1)>'
    for (const [name, facts] of [
      ['disk', { free_gb: odd }],
      ['memory', { available_mb: odd }],
      ['certificate', { hours: odd }],
      ['backup', { age_h: odd, size_mb: odd, offsite: odd, fy: odd, fy_mb: odd, fy_owed: odd }],
      ['assistant', { clients: odd, families: odd }],
      ['telegram_links', { links: odd }],
      ['historify', { gb: odd }],
      ['posture_modes', { what: odd }],
      ['posture_updates', { all: odd, security: odd }],
    ] as const) {
      for (const level of ['fine', 'warning', 'failing'] as const) {
        expect(lineOf(check(name, level, facts))?.detail).not.toContain('<')
      }
    }
    expect(lineOf(check('a_new_check', 'fine'))).toBeNull()
    expect(lineOf(check('service_unknown', 'fine'))).toBeNull()
  })

  it('says what the daily check of the Analytics Token found, and what to do', () => {
    // GA5.1 (M13.12): a state and a time from the portfolio's own check; never the token.
    const at = String(Date.UTC(2026, 10, 16, 3, 6) / 1000) // 08:36 in India, Mon 16 Nov 2026
    const line = (level: CheckLevel, facts: Record<string, string> = {}) =>
      lineOf(check('analytics_token', level, facts))
    expect(line('fine', { when: at })).toEqual({
      group: 'market',
      label: 'Upstox read-only token (Analytics Token)',
      detail:
        "Accepted by Upstox at the last check, 16 Nov, 08:36. It is checked at the portfolio service's first start of a day.",
    })
    expect(line('failing', { when: at })?.detail).toBe(
      'Not accepted by Upstox at the check of 16 Nov, 08:36. Open the Upstox developer page (My Apps) and see whether the token is still there and active. Do not create a new token before you have looked: a new one ends the old one for everything else that uses it.'
    )
    const stale = line('warning', { cause: 'missed', tried: at })?.detail
    expect(stale).toContain('Could not be checked at the last start, 16 Nov, 08:36')
    expect(stale).toContain('No check has been answered yet.')
    expect(line('warning', { cause: 'missed', tried: at, when: at })?.detail).toContain(
      'Last accepted 16 Nov, 08:36.'
    )
    expect(line('unread', { cause: 'changed', when: at })?.detail).toBe(
      'The token on the server was changed after the last check (16 Nov, 08:36), so the new one has not been checked yet. It is checked when the portfolio service next starts on a new day.'
    )
    expect(line('unread', { cause: 'never' })?.detail).toBe(
      'Not checked yet. It is checked when the portfolio service next starts on a new day.'
    )
    expect(line('unread')?.detail).toBe('Could not be read at the last look.')
    expect(line('off')?.detail).toBe('Not checked: the portfolio is switched off.')
    // an odd fact never reaches the screen, and no line carries a code
    expect(line('failing', { when: '<b>401</b>' })?.detail).toMatch(
      /^Not accepted by Upstox at the last check\. /
    )
    for (const level of ['fine', 'failing', 'warning', 'unread', 'off'] as CheckLevel[])
      expect(line(level, { when: at, tried: at })?.detail).not.toMatch(/\b[1-5]\d\d\b|TRINETRA/)
  })

  it('says which way the TRINETRA reader is switched, and never calls off a fault', () => {
    // v0.15.0 review S2: the reader runs while its view is off, and says so.
    const line = (level: CheckLevel, facts: Record<string, string> = {}) =>
      lineOf(check('service_signals', level, facts))
    expect(line('fine', { view: 'off' })).toEqual({
      group: 'services',
      label: 'TRINETRA reader',
      detail: 'Answering. The TRINETRA view is switched off.',
    })
    expect(line('fine', { view: 'on' })?.detail).toBe('Answering.')
    expect(line('fine', { view: 'not_set_up' })?.detail).toContain('switched on but not set up')
    expect(line('off', { view: 'off' })?.detail).toBe('The TRINETRA view is switched off.')
    expect(line('off')?.detail).toBe('Not set up on this server.')
    expect(line('failing', { service: 'signals', minutes: '4' })?.detail).toBe(
      'Not answering for 4 minutes. The server restarts it by itself.'
    )
    expect(line('fine', { view: '<b>' })?.detail).toBe('Answering.')
  })

  it('says a time in India, with the day when it is not today', () => {
    const now = Date.UTC(2026, 10, 16, 5, 30) // 11:00 in India
    expect(whenOf(Date.UTC(2026, 10, 16, 5, 12) / 1000, now)).toBe('10:42')
    expect(whenOf(Date.UTC(2026, 10, 14, 5, 12) / 1000, now)).toBe('14 Nov, 10:42')
    expect(whenOf(null, now)).toBe('')
    expect(whenOf(0, now)).toBe('')
  })
})
