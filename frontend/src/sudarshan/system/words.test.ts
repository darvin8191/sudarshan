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

  it('says a time in India, with the day when it is not today', () => {
    const now = Date.UTC(2026, 10, 16, 5, 30) // 11:00 in India
    expect(whenOf(Date.UTC(2026, 10, 16, 5, 12) / 1000, now)).toBe('10:42')
    expect(whenOf(Date.UTC(2026, 10, 14, 5, 12) / 1000, now)).toBe('14 Nov, 10:42')
    expect(whenOf(null, now)).toBe('')
    expect(whenOf(0, now)).toBe('')
  })
})
