/**
 * Render the app icons from their vector source, public/images/sudarshan-icon.svg,
 * keeping the file names and paths that index.html and the pages already use:
 *   public/favicon.ico                         16, 32 and 48 px frames (PNG-compressed)
 *   public/apple-touch-icon.png                180 px, square tile (iOS rounds the corners)
 *   public/images/android-chrome-192x192.png   192 px
 *   public/logo.png                            192 px
 * The SVG carries its variants as classes on the root element: "full-bleed" squares
 * the tile and "small" drops the ring and enlarges the glyph so 16 px stays legible.
 * Rasterises with the Chromium that @playwright/test already installs for e2e.
 * Run from frontend: node scripts/render-sudarshan-icons.mjs
 */
import { readFileSync, writeFileSync } from 'node:fs'
import { chromium } from '@playwright/test'

const at = (path) => new URL(`../${path}`, import.meta.url)
const svg = readFileSync(at('public/images/sudarshan-icon.svg'), 'utf8')

const PNGS = [
  { file: 'public/apple-touch-icon.png', size: 180, variant: 'full-bleed' },
  { file: 'public/images/android-chrome-192x192.png', size: 192 },
  { file: 'public/logo.png', size: 192 },
]
const FAVICON = [
  { size: 16, variant: 'small' },
  { size: 32, variant: 'small' },
  { size: 48 },
]

const browser = await chromium.launch()
const page = await browser.newPage({ deviceScaleFactor: 1 })

async function render(size, variant) {
  await page.setViewportSize({ width: size, height: size })
  await page.setContent(
    `<!doctype html><style>html,body{margin:0}svg{display:block;width:${size}px;height:${size}px}</style>${svg}`,
  )
  if (variant) await page.locator('svg').evaluate((el, name) => el.classList.add(name), variant)
  return page.screenshot({ omitBackground: true, clip: { x: 0, y: 0, width: size, height: size } })
}

// ICO container holding PNG frames: 6-byte header, one 16-byte entry per frame, then the PNGs.
function ico(frames) {
  const head = Buffer.alloc(6 + 16 * frames.length)
  head.writeUInt16LE(1, 2) // type: icon
  head.writeUInt16LE(frames.length, 4)
  let offset = head.length
  frames.forEach(({ size, png }, i) => {
    const entry = 6 + 16 * i
    head.writeUInt8(size % 256, entry) // width, 0 means 256
    head.writeUInt8(size % 256, entry + 1) // height
    head.writeUInt16LE(1, entry + 4) // colour planes
    head.writeUInt16LE(32, entry + 6) // bits per pixel
    head.writeUInt32LE(png.length, entry + 8)
    head.writeUInt32LE(offset, entry + 12)
    offset += png.length
  })
  return Buffer.concat([head, ...frames.map((frame) => frame.png)])
}

try {
  for (const { file, size, variant } of PNGS) {
    writeFileSync(at(file), await render(size, variant))
    console.log(`${file} ${size}px`)
  }
  const frames = []
  for (const { size, variant } of FAVICON) frames.push({ size, png: await render(size, variant) })
  writeFileSync(at('public/favicon.ico'), ico(frames))
  console.log(`public/favicon.ico ${FAVICON.map((frame) => frame.size).join('/')}px`)
} finally {
  await browser.close()
}
