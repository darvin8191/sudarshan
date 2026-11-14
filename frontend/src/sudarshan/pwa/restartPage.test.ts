/**
 * The restart page (M1.17, T8): one static file, shown by the web server and by the family app's
 * service worker while the app restarts. It must stand alone, because the app that would answer
 * any request of its own is the thing that is down.
 */
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { describe, expect, it } from 'vitest'
import { BRAND } from '@/sudarshan/brand'
import { ICON, WORDMARK } from '@/sudarshan/marks'

const PAGE = readFileSync(join(process.cwd(), 'public', 'images', 'restarting.html'), 'utf8')
const BODY = PAGE.replace(/<!--[\s\S]*?-->/g, '')
const text = BODY.replace(/<style[\s\S]*?<\/style>/g, '')
  .replace(/<[^>]+>/g, ' ')
  .replace(/\s+/g, ' ')

describe('the restart page', () => {
  it('says the app is restarting and when it is back, in plain words', () => {
    expect(text).toContain('SUDARSHAN is restarting. It is back within a minute.')
    expect(text).toContain('tries again by itself')
    expect(text).toContain('Nothing is shown here as current')
    // Never a status code, the web server's name or a developer's word (CLAUDE.md, messages).
    expect(text).not.toMatch(/\b(50[0-9]|nginx|gateway|upstream|gunicorn|error)\b/i)
    expect(text).not.toMatch(/Real-time|\bLive\b/)
  })

  it('carries the name in both scripts, drawn from the app’s own outlines', () => {
    expect(BODY).toContain(`aria-label="${BRAND.devanagari}"`)
    expect(text).toContain(BRAND.latin)
    expect(BODY).toContain(`d="${WORDMARK.d}"`)
    expect(BODY).toContain(`viewBox="${WORDMARK.viewBox}"`)
    expect(BODY).toContain(`d="${ICON.glyph.d}"`)
    expect(BODY).toContain(`transform="${ICON.glyph.transform}"`)
    expect(BODY).toContain(`fill="${ICON.tile}"`)
    expect(BODY).toContain(`stroke="${ICON.ring.color}"`)
    expect(PAGE).toMatch(/<title>[^<]*सुदर्शन[^<]*SUDARSHAN[^<]*<\/title>/)
  })

  it('stands alone: no script, no font, no image and no request of its own', () => {
    expect(BODY).not.toMatch(/<script|<link|<img|<iframe|@import|url\(|@font-face/i)
    expect(BODY).not.toMatch(/\b(src|srcset|action)\s*=/i)
    // The only address in it is the one it is shown under: Try again loads that again.
    expect([...BODY.matchAll(/href="([^"]*)"/g)].map((found) => found[1])).toEqual([''])
    expect(BODY).not.toMatch(/https?:\/\//)
  })

  it('tries the same address again by itself, and offers a 48 dp Try again', () => {
    expect(BODY).toMatch(/<meta http-equiv="refresh" content="15" \/>/)
    expect(BODY).toMatch(/<a class="again" href="">Try again<\/a>/)
    const again = /a\.again \{([^}]*)\}/.exec(BODY)?.[1] ?? ''
    expect(again).toMatch(/min-height:\s*48px/)
    expect(again).toMatch(/min-width:\s*48px/)
  })

  it('is light only, on the D look’s page and card', () => {
    expect(BODY).toContain('<meta name="color-scheme" content="only light" />')
    expect(BODY).toContain('<meta name="theme-color" content="#FFFFFF" />')
    expect(BODY).toMatch(/--bg-canvas:\s*#f4f6f8/)
    expect(BODY).not.toMatch(/prefers-color-scheme|\.dark\b/)
  })
})
