import { cn } from '@/lib/utils'
import { BRAND } from './brand'
import { ICON, WORDMARK } from './marks'

interface BrandMarkProps {
  className?: string
}

/**
 * The app icon, drawn inline from the same vector source as the home-screen
 * icon. Decorative wherever it stands beside the name, which carries the words.
 */
export function BrandIcon({ className }: BrandMarkProps) {
  return (
    <svg
      viewBox={ICON.viewBox}
      aria-hidden="true"
      focusable="false"
      className={cn('sd-icon', className)}
    >
      <rect width="512" height="512" rx={ICON.tileRadius} fill={ICON.tile} />
      <circle
        cx={ICON.ring.cx}
        cy={ICON.ring.cy}
        r={ICON.ring.r}
        fill="none"
        stroke={ICON.ring.color}
        strokeWidth={ICON.ring.width}
      />
      <path fill={ICON.glyph.color} transform={ICON.glyph.transform} d={ICON.glyph.d} />
    </svg>
  )
}

/**
 * सुदर्शन as outlines of Tiro Devanagari Sanskrit, the letterforms of the
 * icon's सु. Drawn in the text colour; the stroke the stylesheet adds is a
 * hair of extra weight at small sizes, where Tiro's thin strokes would fade.
 * Named for assistive technology in Sanskrit, so it is read as the name.
 */
export function Wordmark({ className }: BrandMarkProps) {
  return (
    <svg
      viewBox={WORDMARK.viewBox}
      role="img"
      aria-label={BRAND.devanagari}
      lang="sa"
      focusable="false"
      className={cn('sd-wordmark-mark', className)}
    >
      <path d={WORDMARK.d} />
    </svg>
  )
}

interface BrandLockupProps {
  /**
   * `bar`: the app bar's lockup, the icon beside सुदर्शन over SUDARSHAN. On
   * the narrowest phones the icon steps aside so the account controls fit;
   * both scripts stay at every width (owner rule 2).
   * `stacked`: the icon above the name, for the login, broker and About screens.
   */
  variant?: 'bar' | 'stacked'
  /** Stacked only. `lg` is the login screen's: an 80px icon over a 62px wordmark. */
  size?: 'md' | 'lg'
  /** Stacked only: centred under a card title, or flush left as on the login screen. */
  align?: 'center' | 'start'
  className?: string
}

/**
 * The bilingual lockup (M1.1): the Mor icon, सुदर्शन drawn from outlines, and
 * SUDARSHAN in tracked Khand caps in the brand colour.
 */
export function BrandLockup({
  variant = 'bar',
  size = 'md',
  align = 'center',
  className,
}: BrandLockupProps) {
  return (
    <span
      className={cn(
        'sd-lockup',
        `sd-lockup--${variant}`,
        variant === 'stacked' && [`sd-lockup--${size}`, `sd-lockup--${align}`],
        className
      )}
    >
      <BrandIcon className="sd-lockup__icon" />
      <span className="sd-lockup__name">
        <Wordmark className="sd-lockup__wordmark" />
        <span className="sd-lockup__latin">{BRAND.latin}</span>
      </span>
    </span>
  )
}
