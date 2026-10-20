import { FlaskConical } from 'lucide-react'
import { useThemeStore } from '@/stores/themeStore'

/**
 * Analyze mode's banner (M1.7). Upstream told the mode apart by turning the
 * whole page dark purple; here the page stays light and this strip says it
 * instead: the violet tint, a 1px violet top border, the words and an icon, so
 * the mode is never carried by colour alone.
 *
 * Rendered by both shells (Layout above the navbar, FullWidthLayout above the
 * full-screen pages, /trading included), and nothing when the mode is Live.
 */
export function AnalyzeBanner() {
  const appMode = useThemeStore((s) => s.appMode)
  if (appMode !== 'analyzer') return null

  return (
    // <output> is a status region, so switching into Analyze mode is announced.
    <output className="sd-analyze-banner" data-testid="analyze-banner">
      <FlaskConical aria-hidden="true" className="size-4 shrink-0" />
      <span>
        ANALYZE <span aria-hidden="true">/</span> <span lang="gu">સેન્ડબોક્સ</span>
      </span>
      <span className="sd-analyze-banner__note">Testing with sandbox capital</span>
    </output>
  )
}
