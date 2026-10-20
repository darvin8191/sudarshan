import { cn } from '@/lib/utils'
import { BRAND } from './brand'

interface BrandLockupProps {
  /**
   * `bar`: the app bar's lockup. On phones it stacks the Devanagari over the
   * caps, which is what fits beside the account controls down to 320px; from
   * the sm breakpoint it runs on one line, the two scripts behind a hairline.
   * `stacked`: the full lockup for the login screen, the Devanagari above
   * tracked Latin caps.
   */
  variant?: 'bar' | 'stacked'
  className?: string
}

/**
 * The bilingual wordmark (M1.1): सुदर्शन in Tiro Devanagari Sanskrit (never
 * letter-spaced, never synthesised bold) and SUDARSHAN in tracked caps. Real
 * text rather than an image, so it is crisp at every size and read aloud as the
 * name; `lang="sa"` keeps the Sanskrit glyph forms.
 */
export function BrandLockup({ variant = 'bar', className }: BrandLockupProps) {
  return (
    <span className={cn('sd-lockup', `sd-lockup--${variant}`, className)}>
      <span lang="sa" className="sd-wordmark">
        {BRAND.devanagari}
      </span>
      {variant === 'bar' && <span aria-hidden="true" className="sd-lockup__rule" />}
      <span className="sd-lockup__latin">{BRAND.latin}</span>
    </span>
  )
}
