// SUDARSHAN (M5.13, M20.7): Settings > Risk. The kill switch: where it stands, turning it on and
// resuming, what its last activation did; where the guard checks orders; Upstox's own kill
// switch, which SUDARSHAN only reads; and the history. Server: blueprints/sudarshan_risk.py.
import { useQuery, useQueryClient } from '@tanstack/react-query'
import {
  Activity,
  CircleCheck,
  CircleMinus,
  FlaskConical,
  History,
  Loader2,
  OctagonX,
  Play,
  ShieldAlert,
  ShieldCheck,
  ShieldOff,
  TriangleAlert,
} from 'lucide-react'
import { type ReactNode, useEffect, useState } from 'react'
import { type AuditRow, type RiskStage, type RiskStatus, riskApi } from '@/api/risk'
import { useSocketContext } from '@/components/socket/SocketProvider'
import { Button } from '@/components/ui/button'
import { noteKillSwitch } from '@/sudarshan/risk/killSwitch'
import {
  clockOf,
  RISK_AUDIT_KEY,
  RISK_STATUS_KEY,
  StepList,
  stampOf,
  surfaceOf,
} from '@/sudarshan/risk/parts'
import { ResumeSheet } from '@/sudarshan/risk/ResumeSheet'
import { TurnOnSheet } from '@/sudarshan/risk/TurnOnSheet'

const CARD = 'rounded-[1.125rem] border bg-card px-4 py-4 shadow-[var(--shadow-card)] md:px-5'
const TITLE = 'flex items-center gap-2 text-[1.0625rem] font-bold'

const STAGES: Record<RiskStage, { label: string; note: string; icon: ReactNode }> = {
  on: {
    label: 'Checks Live and Sandbox',
    note: 'New orders are checked at Upstox and in the sandbox.',
    icon: <ShieldCheck aria-hidden="true" className="size-4" />,
  },
  sandbox: {
    label: 'Sandbox only',
    note: 'Only sandbox orders are checked; your Upstox account is not touched yet.',
    icon: <FlaskConical aria-hidden="true" className="size-4" />,
  },
  off: {
    label: 'Off on this server',
    note: "The risk guard is off in this release's settings, so the kill switch is not available here.",
    icon: <ShieldOff aria-hidden="true" className="size-4" />,
  },
}

function Card({ title, icon, children }: { title: string; icon: ReactNode; children: ReactNode }) {
  return (
    <section className={CARD} aria-label={title}>
      <h2 className={TITLE}>
        {icon}
        {title}
      </h2>
      <div className="mt-3 flex flex-col gap-3">{children}</div>
    </section>
  )
}

function StageChip({ stage }: { stage: RiskStage }) {
  const { label, icon } = STAGES[stage]
  const tone =
    stage === 'off'
      ? 'border-[color:var(--border-strong)] text-[color:var(--text-secondary)]'
      : stage === 'sandbox'
        ? 'border-[color:var(--sandbox-border)] bg-[color:var(--sandbox-tint)] text-[color:var(--sandbox-ink)]'
        : 'border-[color:var(--live-border)] bg-[color:var(--live-tint)] text-[color:var(--live-ink)]'
  return (
    <span
      data-testid="risk-stage"
      className={`inline-flex h-8 items-center gap-1.5 rounded-full border px-3 text-[0.8125rem] font-bold ${tone}`}
    >
      {icon}
      {label}
    </span>
  )
}

function SwitchChip({ on }: { on: boolean }) {
  return on ? (
    <span className="sd-risk-chip inline-flex h-8 items-center gap-1.5 rounded-full border px-3 text-[0.8125rem] font-bold uppercase tracking-[0.06em]">
      <OctagonX aria-hidden="true" className="size-4" />
      On
    </span>
  ) : (
    <span className="inline-flex h-8 items-center gap-1.5 rounded-full border border-[color:var(--border-strong)] px-3 text-[0.8125rem] font-bold uppercase tracking-[0.06em] text-[color:var(--text-secondary)]">
      <CircleMinus aria-hidden="true" className="size-4" />
      Off
    </span>
  )
}

function KillSwitchCard({
  status,
  onTurnOn,
  onResume,
}: {
  status: RiskStatus
  onTurnOn: () => void
  onResume: () => void
}) {
  const ks = status.kill_switch
  const report = ks.report
  if (!ks.known) {
    return (
      <Card title="Kill switch" icon={<OctagonX aria-hidden="true" className="size-5" />}>
        <p role="alert" className="sd-risk-note">
          <TriangleAlert aria-hidden="true" className="size-5 shrink-0" />
          Its state could not be read, so new orders are refused until it can. Try again in a few
          seconds.
        </p>
      </Card>
    )
  }
  return (
    <Card title="Kill switch" icon={<OctagonX aria-hidden="true" className="size-5" />}>
      <div className="flex flex-wrap items-center justify-between gap-3">
        <SwitchChip on={ks.on === true} />
        {ks.on && (
          <p className="text-[0.875rem] text-[color:var(--text-secondary)]">
            Since {clockOf(ks.since)}, from {surfaceOf(ks.surface)}
            {ks.exit_all ? ', with Exit all' : ''}
          </p>
        )}
      </div>
      <p className="text-[0.9375rem] leading-snug">
        {ks.on
          ? 'New orders are refused from every place. Exits and cancels still go.'
          : 'Stops every new order at once, from every place, and cancels open orders and GTTs. Exits and cancels always go.'}
      </p>
      {ks.running && (
        <output className="flex items-center gap-2 text-[0.9375rem] font-semibold">
          <Loader2 aria-hidden="true" className="size-5 animate-spin" />
          Being turned on: cancelling and exiting at Upstox.
        </output>
      )}
      {report?.restarted && (
        <p role="alert" className="sd-risk-note">
          <TriangleAlert aria-hidden="true" className="size-5 shrink-0" />
          {report.restarted}
        </p>
      )}
      {report && report.steps.length > 0 && (
        <div className="flex flex-col gap-1.5">
          <h3 className="text-[0.875rem] font-semibold text-[color:var(--text-secondary)]">
            Last time it was turned on
          </h3>
          <StepList steps={report.steps} />
        </div>
      )}
      {ks.on ? (
        <Button className="h-12 text-base" onClick={onResume} disabled={ks.running === true}>
          <Play aria-hidden="true" className="size-5" />
          Resume trading
        </Button>
      ) : (
        <Button
          variant="destructive"
          className="h-12 text-base"
          onClick={onTurnOn}
          disabled={status.stage === 'off'}
          aria-haspopup="dialog"
        >
          <OctagonX aria-hidden="true" className="size-5" />
          Turn kill switch on
        </Button>
      )}
    </Card>
  )
}

function ChecksCard({ status }: { status: RiskStatus }) {
  const { gate, upstox_kill_switch: upstox } = status
  const active = (upstox ?? []).filter((segment) => segment.active)
  return (
    <Card title="Order checks" icon={<Activity aria-hidden="true" className="size-5" />}>
      <div className="flex flex-col gap-1.5">
        <StageChip stage={status.stage} />
        <p className="text-[0.875rem] text-[color:var(--text-secondary)]">
          {STAGES[status.stage].note}
        </p>
      </div>
      <p className="text-[0.875rem]" data-testid="risk-timing">
        {gate.orders > 0 && gate.p50_ms !== null && gate.p95_ms !== null
          ? `Checking took ${gate.p50_ms} ms for a typical order and at most ${gate.p95_ms} ms for 95 in 100, over the last ${gate.orders} orders.`
          : 'No order has been checked since the app started.'}
      </p>
      {gate.last_problem && (
        <p className="flex items-start gap-2 text-[0.875rem]">
          <TriangleAlert
            aria-hidden="true"
            className="mt-0.5 size-4 shrink-0 text-[color:var(--warning-text)]"
          />
          <span>
            Last problem, {stampOf(gate.last_problem.at)}: {gate.last_problem.words}
          </span>
        </p>
      )}
      {upstox && (
        <div className="flex flex-col gap-1.5" data-testid="upstox-kill-switch">
          <h3 className="text-[0.875rem] font-semibold text-[color:var(--text-secondary)]">
            Upstox's own kill switch
          </h3>
          <p className="text-[0.8125rem] text-[color:var(--text-tertiary)]">
            Set only in the Upstox app. SUDARSHAN reads it and never changes it.
          </p>
          <ul className="flex flex-wrap gap-2">
            {active.map((segment) => (
              <li
                key={segment.segment}
                className="inline-flex h-8 items-center gap-1.5 rounded-full border px-3 text-[0.8125rem] font-semibold"
              >
                {segment.on ? (
                  <OctagonX aria-hidden="true" className="size-4 text-[color:var(--down)]" />
                ) : (
                  <CircleCheck aria-hidden="true" className="size-4 text-[color:var(--live-ink)]" />
                )}
                {segment.label}: {segment.on ? 'on' : 'off'}
              </li>
            ))}
          </ul>
        </div>
      )}
    </Card>
  )
}

function HistoryCard({ rows, loading }: { rows: AuditRow[] | undefined; loading: boolean }) {
  return (
    <Card title="History" icon={<History aria-hidden="true" className="size-5" />}>
      {loading ? (
        <p className="text-[0.875rem] text-[color:var(--text-secondary)]">Reading the history.</p>
      ) : !rows || rows.length === 0 ? (
        <p className="text-[0.875rem] text-[color:var(--text-secondary)]">
          Nothing yet. Turning the kill switch on, resuming, and every order it refuses show here.
        </p>
      ) : (
        <ol className="divide-y divide-[color:var(--divider)]">
          {rows.map((row, index) => (
            <li
              key={`${row.at}-${index}`}
              className="flex flex-col gap-0.5 py-2.5 text-[0.875rem] leading-snug"
            >
              <span className="text-[0.78125rem] font-medium text-[color:var(--text-tertiary)]">
                {stampOf(row.last_at)}
                {row.count > 1 ? `, ${row.count} times since ${clockOf(row.at)}` : ''}
              </span>
              <span>{row.summary}</span>
            </li>
          ))}
        </ol>
      )}
    </Card>
  )
}

export default function Risk() {
  const queryClient = useQueryClient()
  const { socket } = useSocketContext()
  const [sheet, setSheet] = useState<'on' | 'resume' | null>(null)
  const status = useQuery({
    queryKey: RISK_STATUS_KEY,
    queryFn: riskApi.status,
    // Not market data: read once, again on every change the server announces, and every two
    // seconds only while an activation is still running its steps.
    refetchInterval: (query) => (query.state.data?.kill_switch.running ? 2_000 : false),
  })
  const audit = useQuery({ queryKey: RISK_AUDIT_KEY, queryFn: () => riskApi.audit(50) })

  // The banner's copy follows what this page read.
  useEffect(() => {
    if (status.data) noteKillSwitch(status.data.kill_switch, status.data.stage)
  }, [status.data])

  useEffect(() => {
    if (!socket) return
    const onRisk = () => {
      void queryClient.invalidateQueries({ queryKey: RISK_STATUS_KEY })
      void queryClient.invalidateQueries({ queryKey: RISK_AUDIT_KEY })
    }
    socket.on('sudarshan_risk', onRisk)
    return () => {
      socket.off('sudarshan_risk', onRisk)
    }
  }, [socket, queryClient])

  if (status.isPending) {
    return (
      <output className="flex items-center justify-center py-16">
        <Loader2 className="size-8 animate-spin text-muted-foreground" aria-hidden="true" />
        <span className="sr-only">Loading the risk settings</span>
      </output>
    )
  }
  if (status.isError || !status.data) {
    return (
      <div className="flex flex-col gap-4 py-6">
        <p role="alert" className="sd-risk-note">
          <TriangleAlert aria-hidden="true" className="size-5 shrink-0" />
          The risk settings could not be read. Try again in a few seconds.
        </p>
        <Button className="h-12" onClick={() => status.refetch()}>
          Try again
        </Button>
      </div>
    )
  }

  const data = status.data
  return (
    <div className="flex flex-col gap-4 md:mx-auto md:max-w-2xl">
      <div className="flex flex-col gap-1">
        <h1 className="flex items-center gap-2 text-[1.75rem] leading-none font-bold md:text-[2rem]">
          <ShieldAlert aria-hidden="true" className="size-7" />
          Risk
        </h1>
        <p className="text-[0.9375rem] text-[color:var(--text-secondary)]">
          One switch that stops every new order, and what the guard did.
        </p>
      </div>
      <KillSwitchCard
        status={data}
        onTurnOn={() => setSheet('on')}
        onResume={() => setSheet('resume')}
      />
      <ChecksCard status={data} />
      <HistoryCard rows={audit.data} loading={audit.isPending} />
      <TurnOnSheet
        open={sheet === 'on'}
        onOpenChange={(open) => setSheet(open ? 'on' : null)}
        stage={data.stage}
      />
      <ResumeSheet
        open={sheet === 'resume'}
        onOpenChange={(open) => setSheet(open ? 'resume' : null)}
        killSwitch={data.kill_switch}
      />
    </div>
  )
}
