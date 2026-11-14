// SUDARSHAN M12.1: the Automation page. The TradingView strategies whose alerts SUDARSHAN turns
// into orders: a card each, read once and changed by the server's own event (nothing polls), and
// the other automation on this server, read only. Server: blueprints/sudarshan_automation.py.
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { ChevronRight, FlaskConical, Loader2, Radio, TriangleAlert, Workflow } from 'lucide-react'
import { useEffect } from 'react'
import { Link } from 'react-router'
import { useSocketContext } from '@/components/socket/SocketProvider'
import { Button } from '@/components/ui/button'
import { automationApi, dayKey, LIST_KEY, OTHERS_KEY } from '@/sudarshan/automation/api'
import { StrategyCard } from '@/sudarshan/automation/StrategyCard'
import {
  AUTOMATION_EVENT,
  type AutomationEvent,
  type ListAnswer,
  type OtherAutomation,
} from '@/sudarshan/automation/types'
import { applied } from '@/sudarshan/automation/words'

const NOTE =
  'flex items-start gap-2 rounded-xl border border-[color:var(--warning-text)] bg-[color:var(--gold-tint)] p-3 text-[0.875rem] font-semibold text-[color:var(--warning-text)]'

function StageLine({ list }: { list: ListAnswer }) {
  const live = list.stage === 'on'
  const Icon = live ? Radio : list.stage === 'sandbox' ? FlaskConical : TriangleAlert
  return (
    <p
      data-testid="auto-stage"
      className="flex items-start gap-2 text-[0.9375rem] font-semibold text-[color:var(--text-secondary)]"
    >
      <Icon aria-hidden="true" className="mt-0.5 size-4 shrink-0" />
      {list.stage_words}
    </p>
  )
}

function Others({ others }: { others: OtherAutomation[] }) {
  return (
    <section aria-label="Other automation on this server" className="flex flex-col gap-2">
      <h2 className="text-[1.0625rem] font-bold">Other automation on this server</h2>
      {others.length === 0 ? (
        <p className="text-[0.875rem] text-[color:var(--text-secondary)]">
          Nothing else here can send an order by itself.
        </p>
      ) : (
        <ul className="divide-y divide-[color:var(--divider)] rounded-[1.125rem] border bg-card shadow-[var(--shadow-card)]">
          {others.map((other) => (
            <li key={`${other.kind}-${other.name}`}>
              <Link
                to={other.path}
                className="flex min-h-12 items-center gap-2 px-4 py-2 text-[0.875rem]"
              >
                <span className="min-w-0 flex-1">
                  <span className="block truncate font-semibold">{other.name}</span>
                  <span className="block text-[color:var(--text-secondary)]">
                    {other.on ? 'On' : 'Off'} · {other.detail}
                  </span>
                </span>
                <ChevronRight aria-hidden="true" className="size-5 shrink-0" />
              </Link>
            </li>
          ))}
        </ul>
      )}
      <p className="text-[0.8125rem] text-[color:var(--text-tertiary)]">
        The kill switch on the Risk page stops new orders from all of these.
      </p>
    </section>
  )
}

export default function Automation() {
  const queryClient = useQueryClient()
  const { socket } = useSocketContext()
  // Not market data: read once, then again whenever the server says an alert was handled or a
  // strategy changed, and when the page or the connection comes back (what was announced
  // meanwhile reached nobody).
  const list = useQuery({
    queryKey: LIST_KEY,
    queryFn: automationApi.list,
    refetchOnWindowFocus: 'always',
  })
  const others = useQuery({ queryKey: OTHERS_KEY, queryFn: automationApi.others })

  useEffect(() => {
    if (!socket) return
    const onEvent = (event: AutomationEvent | undefined) => {
      if (typeof event?.strategy_id !== 'number') return
      queryClient.setQueryData<ListAnswer>(LIST_KEY, (old) => (old ? applied(old, event) : old))
      void queryClient.invalidateQueries({ queryKey: LIST_KEY })
      void queryClient.invalidateQueries({ queryKey: dayKey(event.strategy_id) })
    }
    const onConnect = () =>
      void queryClient.invalidateQueries({ queryKey: ['sudarshan-automation'] })
    socket.on(AUTOMATION_EVENT, onEvent)
    socket.on('connect', onConnect)
    return () => {
      socket.off(AUTOMATION_EVENT, onEvent)
      socket.off('connect', onConnect)
    }
  }, [socket, queryClient])

  if (list.isPending) {
    return (
      <output className="flex items-center justify-center py-16">
        <Loader2 className="size-8 animate-spin text-muted-foreground" aria-hidden="true" />
        <span className="sr-only">Loading your strategies</span>
      </output>
    )
  }
  if (list.isError || !list.data) {
    return (
      <div className="flex flex-col gap-4 py-6">
        <p role="alert" className="sd-risk-note">
          <TriangleAlert aria-hidden="true" className="size-5 shrink-0" />
          {list.error?.message ?? 'Your strategies could not be read. Try again in a few seconds.'}
        </p>
        <Button className="h-12" onClick={() => list.refetch()}>
          Try again
        </Button>
      </div>
    )
  }

  const data = list.data
  return (
    <div className="flex flex-col gap-4 md:mx-auto md:max-w-2xl">
      <div className="flex flex-col gap-1">
        <h1 className="flex items-center gap-2 text-[1.75rem] leading-none font-bold md:text-[2rem]">
          <Workflow aria-hidden="true" className="size-7" />
          Automation
        </h1>
        <p className="text-[0.9375rem] text-[color:var(--text-secondary)]">
          Your TradingView strategies, and the orders SUDARSHAN sends for their alerts.
        </p>
      </div>
      <StageLine list={data} />
      {data.notes.map((note) => (
        <p key={note} role="alert" className={NOTE}>
          <TriangleAlert aria-hidden="true" className="size-5 shrink-0" />
          {note}
        </p>
      ))}
      {data.strategies.length === 0 ? (
        <p
          data-testid="auto-empty"
          className="rounded-[1.125rem] border bg-card px-4 py-6 text-[0.9375rem] shadow-[var(--shadow-card)]"
        >
          No strategy yet. A strategy here is one TradingView alert: SUDARSHAN reads each alert it
          sends and places the order, in the Sandbox first.
        </p>
      ) : (
        <ul className="flex flex-col gap-3">
          {data.strategies.map((strategy) => (
            <StrategyCard key={strategy.id} strategy={strategy} />
          ))}
        </ul>
      )}
      {others.data && <Others others={others.data.others} />}
    </div>
  )
}
