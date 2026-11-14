import {
  CircleCheckIcon,
  InfoIcon,
  Loader2Icon,
  OctagonXIcon,
  TriangleAlertIcon,
} from 'lucide-react'
import { useTheme } from 'next-themes'
import { Toaster as Sonner, type ToasterProps } from 'sonner'

/**
 * SUDARSHAN (M1.18): the coloured toasts on the D tokens. The library's own light greens and
 * ambers put a success or a warning sentence under 4.5:1 on its fill; these pairs are the D
 * look's chip pairs, each checked in `sudarshan/theme.test.ts`. The icon beside the sentence
 * says the kind, so the colour is never the only sign. The close button's size is in
 * `sudarshan/theme.css` ("Shell toast").
 */
export const SD_TOAST_COLOURS: Record<string, string> = {
  '--success-bg': 'var(--live-tint)',
  '--success-text': 'var(--live-ink)',
  '--success-border': 'var(--live-border)',
  '--error-bg': 'var(--down-tint)',
  '--error-text': 'var(--loss-strong)',
  '--error-border': 'color-mix(in oklab, var(--loss-strong) 35%, var(--down-tint))',
  '--warning-bg': 'var(--gold-tint)',
  '--warning-text': 'var(--gold-ink)',
  '--warning-border': 'color-mix(in oklab, var(--gold) 60%, var(--gold-tint))',
  '--info-bg': 'var(--brand-tint)',
  '--info-text': 'var(--brand)',
  '--info-border': 'color-mix(in oklab, var(--brand) 35%, var(--brand-tint))',
}

const Toaster = ({ ...props }: ToasterProps) => {
  const { theme = 'system' } = useTheme()

  return (
    <Sonner
      theme={theme as ToasterProps['theme']}
      className="toaster group"
      offset="64px"
      icons={{
        success: <CircleCheckIcon className="size-4" />,
        info: <InfoIcon className="size-4" />,
        warning: <TriangleAlertIcon className="size-4" />,
        error: <OctagonXIcon className="size-4" />,
        loading: <Loader2Icon className="size-4 animate-spin" />,
      }}
      style={
        {
          '--normal-bg': 'var(--popover)',
          '--normal-text': 'var(--popover-foreground)',
          '--normal-border': 'var(--border)',
          '--border-radius': 'var(--radius)',
          ...SD_TOAST_COLOURS,
        } as React.CSSProperties
      }
      {...props}
    />
  )
}

export { Toaster }
