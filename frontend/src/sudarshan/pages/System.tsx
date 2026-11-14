// SUDARSHAN (M20.8): the System page. What the server's watch last saw, in one list: every
// service, the market day's connections, the disk, the certificate, the backups and the last
// restore test, the safety settings, and the AI assistant with "Disconnect the assistant".
// It is the page a support session asks the owner to open. Server: blueprints/sudarshan_system.py.
//
// None of this is market data: the watch looks every 2 minutes, so the page reads once, again
// when it comes back into view, and every 2 minutes while it is open.
import { useQuery, useQueryClient } from '@tanstack/react-query'
import {
  Bot,
  CircleCheck,
  CircleHelp,
  CircleMinus,
  HeartPulse,
  Loader2,
  MonitorSmartphone,
  OctagonX,
  RefreshCw,
  ServerCog,
  TriangleAlert,
  Unplug,
} from 'lucide-react'
import { type ReactNode, useState } from 'react'
import { Link } from 'react-router'
import {
  type AssistantState,
  type CheckLevel,
  type SystemStatus,
  systemApi,
  type WatchCheck,
} from '@/api/system'
import {
  AlertDialog,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from '@/components/ui/alert-dialog'
import { Button } from '@/components/ui/button'
import { GROUPS, type Group, LEVEL_WORDS, lineOf, whenOf } from '@/sudarshan/system/words'
import { useOpenGuard } from '@/sudarshan/watchlist/openGuard'

export const SYSTEM_STATUS_KEY = ['sudarshan-system-status'] as const
const LOOK_EVERY_MS = 120_000

const CARD = 'rounded-[1.125rem] border bg-card px-4 py-4 shadow-[var(--shadow-card)] md:px-5'
const TITLE = 'flex items-center gap-2 text-[1.0625rem] font-bold'

const LEVEL_ICONS: Record<CheckLevel, { icon: typeof CircleCheck; tone: string }> = {
  fine: { icon: CircleCheck, tone: 'text-[color:var(--live-ink)]' },
  warning: { icon: TriangleAlert, tone: 'text-[color:var(--warning-text)]' },
  failing: { icon: OctagonX, tone: 'text-[color:var(--down)]' },
  unread: { icon: CircleHelp, tone: 'text-[color:var(--text-secondary)]' },
  off: { icon: CircleMinus, tone: 'text-[color:var(--text-secondary)]' },
}

/** A level as an icon and its word, never colour alone. */
function LevelMark({ level }: { level: CheckLevel }) {
  const { icon: Icon, tone } = LEVEL_ICONS[level] ?? LEVEL_ICONS.unread
  return (
    <span
      className={`inline-flex shrink-0 items-center gap-1 text-[0.8125rem] font-bold ${tone}`}
      data-level={level}
    >
      <Icon aria-hidden="true" className="size-4" />
      {LEVEL_WORDS[level] ?? LEVEL_WORDS.unread}
    </span>
  )
}

function Row({
  label,
  detail,
  level,
  since,
}: {
  label: string
  detail: string
  level: CheckLevel
  since?: string
}) {
  return (
    <li className="flex flex-col gap-0.5 py-2.5">
      <div className="flex items-start justify-between gap-3">
        <span className="text-[0.9375rem] font-semibold">{label}</span>
        <LevelMark level={level} />
      </div>
      <p className="text-[0.875rem] leading-snug text-[color:var(--text-secondary)]">{detail}</p>
      {since && <p className="text-[0.8125rem] text-[color:var(--text-tertiary)]">Since {since}</p>}
    </li>
  )
}

function Card({ title, icon, children }: { title: string; icon?: ReactNode; children: ReactNode }) {
  return (
    <section className={CARD} aria-label={title}>
      <h2 className={TITLE}>
        {icon}
        {title}
      </h2>
      <div className="mt-2 flex flex-col gap-3">{children}</div>
    </section>
  )
}

function ChecksCard({
  group,
  title,
  status,
  extra,
}: {
  group: Group
  title: string
  status: SystemStatus
  extra?: ReactNode
}) {
  const rows = (status.watch?.checks ?? [])
    .map((check: WatchCheck) => ({ check, line: lineOf(check, status.drill) }))
    .filter((row) => row.line?.group === group)
  if (rows.length === 0 && !extra) return null
  return (
    <Card title={title}>
      <ul className="divide-y divide-[color:var(--divider)]">
        {rows.map(({ check, line }) => (
          <Row
            key={check.name}
            label={line?.label ?? check.name}
            detail={line?.detail ?? ''}
            level={check.level}
            since={check.level === 'fine' || check.level === 'off' ? '' : whenOf(check.since)}
          />
        ))}
        {extra}
      </ul>
    </Card>
  )
}

function AssistantCard({ assistant }: { assistant: AssistantState }) {
  const queryClient = useQueryClient()
  const [asking, setAsking] = useState(false)
  const [sending, setSending] = useState(false)
  const [answer, setAnswer] = useState<{ ok: boolean; message: string } | null>(null)
  const guard = useOpenGuard(asking)

  const disconnect = async () => {
    if (sending) return
    setSending(true)
    try {
      setAnswer(await systemApi.disconnect())
    } catch {
      setAnswer({
        ok: false,
        message:
          'SUDARSHAN could not confirm this. Turn on the kill switch on the Risk page: it ends the assistant too.',
      })
    } finally {
      setSending(false)
      setAsking(false)
      queryClient.invalidateQueries({ queryKey: SYSTEM_STATUS_KEY })
    }
  }

  return (
    <Card title="AI assistant" icon={<Bot aria-hidden="true" className="size-5" />}>
      <div className="flex items-start justify-between gap-3">
        <p className="text-[0.9375rem] leading-snug">{assistant.words}</p>
        <LevelMark level={assistant.running ? 'fine' : assistant.switched_on ? 'warning' : 'off'} />
      </div>
      {assistant.switched_on && !assistant.approval_required && (
        <p className="flex items-start gap-2 text-[0.875rem]">
          <TriangleAlert
            aria-hidden="true"
            className="mt-0.5 size-4 shrink-0 text-[color:var(--warning-text)]"
          />
          A new AI client would be approved without asking you. Set MCP_OAUTH_REQUIRE_APPROVAL=True
          in OpenAlgo's settings.
        </p>
      )}
      <p className="text-[0.875rem] text-[color:var(--text-secondary)]">
        Disconnect ends every AI connection at once, also while the kill switch is off or checks the
        sandbox only. Nothing comes back by itself: you connect and approve again.
      </p>
      {answer && (
        <output
          role={answer.ok ? 'status' : 'alert'}
          className={answer.ok ? 'flex items-start gap-2 text-[0.9375rem]' : 'sd-risk-note'}
        >
          {answer.ok ? (
            <CircleCheck
              aria-hidden="true"
              className="mt-0.5 size-5 shrink-0 text-[color:var(--live-ink)]"
            />
          ) : (
            <TriangleAlert aria-hidden="true" className="size-5 shrink-0" />
          )}
          <span>{answer.message}</span>
        </output>
      )}
      <Button
        variant="destructive"
        className="h-12 text-base"
        onClick={() => setAsking(true)}
        aria-haspopup="dialog"
      >
        <Unplug aria-hidden="true" className="size-5" />
        Disconnect the assistant
      </Button>
      <AlertDialog open={asking} onOpenChange={(open) => !sending && setAsking(open)}>
        <AlertDialogContent
          {...guard.handlers}
          data-settling={guard.settling ? '' : undefined}
          onEscapeKeyDown={(event) => sending && event.preventDefault()}
        >
          <AlertDialogHeader>
            <AlertDialogTitle>Disconnect the assistant?</AlertDialogTitle>
            <AlertDialogDescription>
              Every AI connection to SUDARSHAN ends now. To use the assistant again you connect it
              again in Claude and approve it.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel className="h-12" disabled={sending}>
              Keep connected
            </AlertDialogCancel>
            <Button variant="destructive" className="h-12" onClick={disconnect} disabled={sending}>
              {sending ? (
                <Loader2 aria-hidden="true" className="size-5 animate-spin" />
              ) : (
                <Unplug aria-hidden="true" className="size-5" />
              )}
              {sending ? 'Disconnecting' : 'Disconnect'}
            </Button>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </Card>
  )
}

function Header({
  status,
  onLook,
  looking,
}: {
  status: SystemStatus
  onLook: () => void
  looking: boolean
}) {
  const watch = status.watch
  const needs = (watch?.checks ?? []).filter(
    (check) => check.level === 'failing' || check.level === 'warning'
  ).length
  return (
    <div className="flex flex-col gap-3">
      <div className="flex flex-col gap-1">
        <h1 className="flex items-center gap-2 text-[1.75rem] leading-none font-bold md:text-[2rem]">
          <ServerCog aria-hidden="true" className="size-7" />
          System
        </h1>
        <p className="text-[0.9375rem] text-[color:var(--text-secondary)]">
          What the server's own watch last saw. It looks every 2 minutes and tells you on Telegram
          when something changes.
        </p>
      </div>
      {!watch && (
        <p role="alert" className="sd-risk-note sd-risk-note--day">
          <TriangleAlert aria-hidden="true" className="size-5 shrink-0" />
          The server's watch has not reported yet. It starts with the release that brings it; if
          this stays after ten minutes, have the server looked at.
        </p>
      )}
      {watch?.stale && (
        <p role="alert" className="sd-risk-note">
          <TriangleAlert aria-hidden="true" className="size-5 shrink-0" />
          The watch has not looked since {whenOf(watch.at)}. What is below is old: have the server
          looked at.
        </p>
      )}
      {watch && (
        <div className="flex flex-wrap items-center justify-between gap-3">
          <p className="text-[0.9375rem]" data-testid="system-summary">
            <span className="font-semibold">
              {needs === 0 ? 'Nothing needs you.' : `${needs} to look at.`}
            </span>{' '}
            Release {watch.release ?? 'not recorded'}, looked at {whenOf(watch.at)}.
          </p>
          <Button variant="outline" className="h-12" onClick={onLook} disabled={looking}>
            <RefreshCw aria-hidden="true" className={`size-5 ${looking ? 'animate-spin' : ''}`} />
            Read again
          </Button>
        </div>
      )}
    </div>
  )
}

export default function System() {
  const status = useQuery({
    queryKey: SYSTEM_STATUS_KEY,
    queryFn: systemApi.status,
    refetchInterval: LOOK_EVERY_MS,
    refetchOnWindowFocus: true,
  })

  if (status.isPending) {
    return (
      <output className="flex items-center justify-center py-16">
        <Loader2 className="size-8 animate-spin text-muted-foreground" aria-hidden="true" />
        <span className="sr-only">Reading the server's state</span>
      </output>
    )
  }
  if (status.isError || !status.data) {
    return (
      <div className="flex flex-col gap-4 py-6">
        <p role="alert" className="sd-risk-note">
          <TriangleAlert aria-hidden="true" className="size-5 shrink-0" />
          The server's state could not be read. Only the owner's account can open this page. Try
          again in a few seconds.
        </p>
        <Button className="h-12" onClick={() => status.refetch()}>
          Try again
        </Button>
      </div>
    )
  }

  const data = status.data
  const heartbeat = data.watch?.heartbeat
  return (
    <div className="flex flex-col gap-4 md:mx-auto md:max-w-2xl">
      <Header status={data} onLook={() => status.refetch()} looking={status.isFetching} />
      {GROUPS.filter((group) => group.id !== 'connections').map((group) => (
        <ChecksCard
          key={group.id}
          group={group.id}
          title={group.title}
          status={data}
          extra={
            group.id === 'server' && heartbeat?.words ? (
              <li className="flex items-start gap-2 py-2.5 text-[0.875rem] leading-snug">
                <HeartPulse aria-hidden="true" className="mt-0.5 size-4 shrink-0" />
                <span>
                  <span className="font-semibold">Outside heartbeat.</span> {heartbeat.words}
                </span>
              </li>
            ) : undefined
          }
        />
      ))}
      <AssistantCard assistant={data.assistant} />
      <ChecksCard
        group="connections"
        title="Connections"
        status={data}
        extra={
          <li className="flex items-center justify-between gap-3 py-1">
            <span className="flex items-center gap-2 text-[0.9375rem]">
              <MonitorSmartphone aria-hidden="true" className="size-4 shrink-0" />
              {data.sessions === null
                ? 'Your signed-in devices could not be counted.'
                : `Your signed-in devices: ${data.sessions}.`}
            </span>
            <Link
              to="/profile"
              className="inline-flex h-12 items-center px-2 font-semibold text-[color:var(--brand)] underline"
            >
              Profile
            </Link>
          </li>
        }
      />
    </div>
  )
}
