import { ArrowDown, ArrowUp } from 'lucide-react'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Label } from '@/components/ui/label'
import { Switch } from '@/components/ui/switch'
import { setCvdMode, useCvdMode } from './display'

/**
 * Profile's Display tab, in place of upstream's theme picker. The theme and the
 * accent are stated here rather than offered (light only, one brand accent: a
 * control that cannot change anything is not shown as one). Colour-blind mode is
 * the one setting, and the preview under it is drawn in the live tokens, so it
 * changes the moment the switch does.
 */
export function DisplaySettings() {
  const cvd = useCvdMode()

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
      </CardContent>
    </Card>
  )
}
