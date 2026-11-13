/**
 * Render the family app's notification badge, public/images/badge-96.png: the Mor icon's glyph
 * as one flat white shape on a transparent square. Android uses only the alpha channel of a
 * badge, so it is drawn solid and a little heavier than the icon's own glyph to stay legible
 * at the 24 px the status bar gives it. Kept out of the build: run it by hand when the icon
 * changes, from frontend: node scripts/render-family-badge.mjs
 * Rasterises with rsvg-convert (librsvg), which a Linux box has.
 */
import { execFileSync } from 'node:child_process'
import { readFileSync, writeFileSync } from 'node:fs'

const at = (path) => new URL(`../${path}`, import.meta.url)
const icon = readFileSync(at('public/images/sudarshan-icon.svg'), 'utf8')
const glyph = /<path fill="#FFFFFF" transform="([^"]+)" d="([^"]+)"\/>/.exec(icon)
if (!glyph) throw new Error('The icon no longer has its glyph path in the expected form.')

// The glyph sits at about x 157-355, y 128-370 of the icon's 512 square: a square of 282 centred
// on it leaves a margin on every side.
const svg = `<svg xmlns="http://www.w3.org/2000/svg" width="96" height="96" viewBox="115 108 282 282">
  <path fill="#FFFFFF" stroke="#FFFFFF" stroke-width="40" stroke-linejoin="round" transform="${glyph[1]}" d="${glyph[2]}"/>
</svg>`
const png = execFileSync('rsvg-convert', ['--width', '96', '--height', '96', '--format', 'png'], {
  input: svg,
})
writeFileSync(at('public/images/badge-96.png'), png)
