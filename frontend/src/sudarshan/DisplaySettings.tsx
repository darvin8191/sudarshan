import { ArrowDown, ArrowUp } from 'lucide-react'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Label } from '@/components/ui/label'
import { Switch } from '@/components/ui/switch'
import {
  setMarketSoundMuted,
  setOrderSoundMuted,
  useMarketSoundMuted,
  useOrderSoundMuted,
} from './books/chime'
import { setPortfolioCard, usePortfolioCard } from './dashboard/portfolioCard'
import { setCvdMode, useCvdMode } from './display'

/**
 * Profile's Display tab, in place of upstream's theme picker. The theme and the
 * accent are stated here rather than offered (light only, one brand accent: a
 * control that cannot change anything is not shown as one). Colour-blind mode
 * and the order and market alert sounds are the settings, all kept on this
 * device only; the preview under colour-blind mode is drawn in the live tokens,
 * so it changes the moment the switch does.
 */
export function DisplaySettings() {
  const cvd = useCvdMode()
  const soundMuted = useOrderSoundMuted()
  const marketMuted = useMarketSoundMuted()
  const portfolioCard = usePortfolioCard()

  return (
    <Card>
      <CardHeader>
        <CardTitle>Display</CardTitle>
        <CardDescription>
          One light theme, tuned to stay readable on a phone in sunlight.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-6">
        <dl className="grid gap-x-6 gap-y-3 text-sm sm:grid-cols-[7rem_1fr]">
          <dt className="font-medium">Theme</dt>
          <dd className="text-muted-foreground">Light. There is no dark theme.</dd>
          <dt className="font-medium">Accent</dt>
          <dd className="flex items-center gap-2 text-muted-foreground">
            <span
              aria-hidden="true"
              className="size-4 shrink-0 rounded-full border bg-[color:var(--brand)]"
            />
            Peacock blue, from the app icon. Analyze mode turns it violet and shows a banner.
          </dd>
        </dl>

        <div className="flex items-start justify-between gap-4 rounded-lg border p-4">
          <div className="space-y-1">
            <Label htmlFor="sd-cvd-mode">Colour-blind mode</Label>
            <p className="text-sm text-muted-foreground">
              Up and down use blue and orange instead of green and red. Signs and arrows stay.
            </p>
            <p className="flex flex-wrap items-center gap-x-4 gap-y-1 pt-1 text-sm">
              <span className="text-muted-foreground">Preview</span>
              <span className="inline-flex items-center gap-1 font-semibold tabular-nums text-[color:var(--up)]">
                <ArrowUp aria-hidden="true" className="size-4" />
                +2,450.75
              </span>
              <span className="inline-flex items-center gap-1 font-semibold tabular-nums text-[color:var(--down)]">
                <ArrowDown aria-hidden="true" className="size-4" />
                -1,180.40
              </span>
            </p>
          </div>
          <Switch id="sd-cvd-mode" checked={cvd} onCheckedChange={setCvdMode} />
        </div>

        <SoundRow
          id="sd-order-sound"
          title="Order alert sound"
          words="A short soft sound when an order fills, partly fills, is rejected or is cancelled at the broker. The alert itself still shows when this is off. This device only."
          on={!soundMuted}
          onChange={(on) => setOrderSoundMuted(!on)}
        />

        {/* SUDARSHAN M10.7: market alerts sound on their own two notes. */}
        <SoundRow
          id="sd-market-sound"
          rowId="market-alert-sound"
          title="Market alert sound"
          words="A short sound, higher than the order sounds, when a market alert you set fires. The alert itself still shows when this is off. This device only."
          on={!marketMuted}
          onChange={(on) => setMarketSoundMuted(!on)}
        />

        {/* SUDARSHAN M3.11: the dashboard's Portfolio card, off until the owner has checked it
            (decision P5); it then replaces the Holdings card a release later. */}
        <SoundRow
          id="sd-portfolio-card"
          rowId="portfolio-card"
          title="Portfolio card on the dashboard"
          words="A card with the Upstox account and your pinned portfolio tabs, each with its value, today and overall gain, moving with the ticks. Shown beside the Holdings card for now. This device only."
          on={portfolioCard}
          onChange={setPortfolioCard}
        />
      </CardContent>
    </Card>
  )
}

/**
 * A sound's switch, its whole row the target (a switch alone is under 48 dp): a tap anywhere on
 * the row turns it on or off. Named by its title, described by its words.
 */
function SoundRow({
  id,
  rowId,
  title,
  words,
  on,
  onChange,
}: {
  id: string
  rowId?: string
  title: string
  words: string
  on: boolean
  onChange: (on: boolean) => void
}) {
  return (
    <label
      id={rowId}
      htmlFor={id}
      className="flex min-h-12 cursor-pointer items-start justify-between gap-4 rounded-lg border p-4"
    >
      <span className="space-y-1">
        <span id={`${id}-title`} className="block text-sm leading-none font-medium">
          {title}
        </span>
        <span id={`${id}-words`} className="block text-sm text-muted-foreground">
          {words}
        </span>
      </span>
      <Switch
        id={id}
        aria-labelledby={`${id}-title`}
        aria-describedby={`${id}-words`}
        checked={on}
        onCheckedChange={onChange}
      />
    </label>
  )
}
