import type { LogoWatermarkOptions } from 'openalgo-charts'

/**
 * The product's name, in both scripts, for every main place the shell shows it
 * (owner rule 2). The Devanagari is the wordmark; the Latin caps go with it.
 */
export const BRAND = {
  devanagari: 'सुदर्शन',
  latin: 'SUDARSHAN',
  /** One line: the browser tab, meta tags. */
  full: 'सुदर्शन · SUDARSHAN',
} as const

/**
 * The project this fork is built on. Its name stays wherever credit is due:
 * the AGPL-3.0 attribution in the footer and on the About page.
 */
export const UPSTREAM = {
  name: 'OpenAlgo',
  repo: 'https://github.com/marketcalls/openalgo',
  licence: 'AGPL-3.0',
  licenceUrl: 'https://www.gnu.org/licenses/agpl-3.0.html',
} as const

/**
 * The chart's corner mark (M1.5), in place of the engine's default. The mark is
 * the wordmark as vector paths (public/images/sudarshan-wordmark.svg), because
 * an image drawn on a canvas cannot use the page's web fonts; it is served from
 * this origin, so the canvas stays exportable. The engine tints it to the axis
 * text colour and reveals the label on hover. No link: the mark names the
 * product, it does not send the trader anywhere.
 */
export const CHART_BRANDING: LogoWatermarkOptions = {
  src: '/images/sudarshan-wordmark.svg',
  label: BRAND.latin,
  href: undefined,
  height: 22,
}
