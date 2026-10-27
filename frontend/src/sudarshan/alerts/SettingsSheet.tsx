import { useQuery, useQueryClient } from '@tanstack/react-query'
import { AlertTriangle, Send, Volume2 } from 'lucide-react'
import { useRef, useState } from 'react'
import { Link } from 'react-router'
import {
  type AlertEvent,
  type AlertsError,
  type Channel,
  type EventsPage,
  type MarketAlertsState,
  marketAlertsApi,
  type Prefs,
  problemOf,
} from '@/api/marketAlerts'
import { Switch } from '@/components/ui/switch'
import { cn } from '@/lib/utils'
import { OUTLINED } from '@/sudarshan/books/cards/parts'
import { BottomSheet } from '@/sudarshan/watchlist/BottomSheet'
import { CHANNELS, DeliveryChip } from './HistoryList'
import { withRecentDeliveries } from './marketAlerts'
import { PREFS_KEY, STATE_KEY, TEST_KEY } from './stateCache'

/** Taps closer than this to the last test are the same tap. */
const TAP_GAP_MS = 1_000

const WHAT: Record<Channel, string> = {
  telegram: 'Each alert in your Telegram chat, from OpenAlgo’s Telegram bot.',
  app: 'A toast on every screen where the app is open.',
}

/**
 * Where market alerts go (M10.14): Telegram on or off and the app on or off, kept on the server
 * as the default for every alert that does not say otherwise, on every device; a test through
 * each channel that is on, with how each delivered it (one still sending settles by push); and
 * the way to this device's own sound switch, in Profile > Display.
 */
export function SettingsSheet({ onClose }: { onClose: () => void }) {
  const client = useQueryClient()
  const state = client.getQueryData<MarketAlertsState>(STATE_KEY)
  const prefs = useQuery<Prefs, AlertsError>({
    queryKey: PREFS_KEY,
    queryFn: marketAlertsApi.prefs,
    placeholderData: state?.prefs,
    retry: false,
  })
  const tested = useQuery<EventsPage | null>({
    queryKey: TEST_KEY,
    queryFn: () => null,
    enabled: false,
    staleTime: Number.POSITIVE_INFINITY,
  })
  const [saving, setSaving] = useState(false)
  const [testing, setTesting] = useState(false)
  const [problem, setProblem] = useState<string | null>(null)
  const once = useRef(false)
  const tappedAt = useRef(Number.NEGATIVE_INFINITY)

  // A read that failed shows the settings as /state last had them, not as both off; a change
  // waits for a read that answered.
  const current = prefs.data
  const shown = current ?? state?.prefs
  const save = async (channel: Channel, on: boolean) => {
    if (!current || saving) return
    setSaving(true)
    setProblem(null)
    try {
      const saved = await marketAlertsApi.setPrefs({ ...current, [channel]: on })
      client.setQueryData(PREFS_KEY, saved)
      client.setQueryData<MarketAlertsState>(STATE_KEY, (held) =>
        held ? { ...held, prefs: saved } : held
      )
    } catch (error) {
      setProblem(problemOf(error).message)
      void prefs.refetch()
    } finally {
      setSaving(false)
    }
  }

  const test = async () => {
    // One test a tap: none while one is on its way, nor for the second tap of a double tap.
    if (once.current || Date.now() - tappedAt.current < TAP_GAP_MS) return
    once.current = true
    tappedAt.current = Date.now()
    setTesting(true)
    setProblem(null)
    try {
      const result = await marketAlertsApi.test()
      const event = { id: result.event_id, deliveries: result.deliveries } as AlertEvent
      client.setQueryData<EventsPage>(
        TEST_KEY,
        withRecentDeliveries({ events: [event], more: false })
      )
    } catch (error) {
      setProblem(problemOf(error).message)
    } finally {
      once.current = false
      setTesting(false)
    }
  }

  const result = tested.data?.events[0]
  return (
    <BottomSheet
      open
      onOpenChange={(open) => {
        if (!open) onClose()
      }}
      title="Alert settings"
      description="For every alert, on every device"
    >
      <div className="flex flex-col gap-4">
        <fieldset className="flex flex-col gap-2">
          <legend className="pb-1 text-xs font-semibold text-[color:var(--text-secondary)]">
            Send alerts to
          </legend>
          {CHANNELS.map(([channel, name]) => (
            <label
              key={channel}
              className="flex min-h-12 items-center justify-between gap-3 rounded-[0.875rem] border px-3 py-2"
            >
              <span className="flex flex-col">
                <span className="text-sm font-semibold">{name}</span>
                <span className="text-xs font-medium text-muted-foreground">{WHAT[channel]}</span>
              </span>
              <Switch
                aria-label={name}
                checked={shown?.[channel] ?? false}
                disabled={!current || saving}
                onCheckedChange={(on) => void save(channel, on)}
              />
            </label>
          ))}
          <p className="text-xs font-medium text-muted-foreground">
            An alert set with its own channels keeps them.
          </p>
        </fieldset>

        <section aria-label="Test" className="flex flex-col gap-2">
          <button
            type="button"
            disabled={testing}
            onClick={() => void test()}
            className={cn(OUTLINED, 'self-start')}
          >
            <Send aria-hidden="true" className="size-5" />
            {testing ? 'Sending a test' : 'Send a test'}
          </button>
          {result && (
            <output className="flex flex-wrap gap-x-3 gap-y-1.5">
              {CHANNELS.map(([channel, name]) => {
                const delivery = result.deliveries[channel]
                return delivery ? (
                  <DeliveryChip key={channel} name={name} delivery={delivery} />
                ) : null
              })}
            </output>
          )}
        </section>

        {(problem || prefs.error) && (
          <p
            role="alert"
            className="flex items-start gap-1.5 rounded-[0.625rem] bg-[color:var(--down-tint)] px-3 py-2 text-sm font-semibold text-[color:var(--down)]"
          >
            <AlertTriangle aria-hidden="true" className="mt-0.5 size-4 shrink-0" />
            {problem ?? prefs.error?.message}
          </p>
        )}

        <Link
          to="/profile?tab=theme"
          className="inline-flex min-h-12 items-center gap-2 text-sm font-semibold text-[color:var(--brand)] underline underline-offset-2"
        >
          <Volume2 aria-hidden="true" className="size-4" />
          Market alert sound on this device, in Profile &gt; Display
        </Link>
      </div>
    </BottomSheet>
  )
}
