// SUDARSHAN M7.18: the Portfolio page, phone first. The Upstox account (read-only) and the
// owner's own tabs, as the portfolio service holds them (blueprints/sudarshan_portfolio.py).
// This is the shell: the tab strip, what is open kept in the address, and the states in words.
// Each part of a tab arrives as its own change and replaces its stub in sudarshan/portfolio.
import { useQuery, useQueryClient } from '@tanstack/react-query'
import {
  Archive,
  LoaderCircle,
  Plus,
  Power,
  RefreshCw,
  Settings2,
  TriangleAlert,
} from 'lucide-react'
import { useMemo } from 'react'
import { portfolioCall, problemOf } from '@/sudarshan/portfolio/api'
import { CustomStub } from '@/sudarshan/portfolio/StubViews'
import { ActivitySheet } from '@/sudarshan/portfolio/sheets/ActivitySheet'
import { ImportSheet } from '@/sudarshan/portfolio/sheets/ImportSheet'
import { NewTabSheet } from '@/sudarshan/portfolio/sheets/NewTabSheet'
import { TabSettingsSheet } from '@/sudarshan/portfolio/sheets/TabSettingsSheet'
import { TabStrip } from '@/sudarshan/portfolio/TabStrip'
import { ActivityTab, PORTFOLIO_KEY } from '@/sudarshan/portfolio/tab/ActivityTab'
import { TabOverview } from '@/sudarshan/portfolio/tab/Overview'
import { PerformanceTab } from '@/sudarshan/portfolio/tab/PerformanceTab'
import { WORDS } from '@/sudarshan/portfolio/types'
import { UpstoxTab } from '@/sudarshan/portfolio/upstox/UpstoxTab'
import {
  type CustomView,
  type PortfolioOverlay,
  UPSTOX_VIEWS,
  type UpstoxView,
  usePortfolioOverlay,
} from '@/sudarshan/portfolio/usePortfolioOverlay'
import { BottomSheet } from '@/sudarshan/watchlist/BottomSheet'

export const PORTFOLIO_STATE_KEY = [...PORTFOLIO_KEY, 'state'] as const

const CARD = 'rounded-[1.125rem] border bg-card px-4 py-6 text-center shadow-[var(--shadow-card)]'
const ACTION =
  'inline-flex min-h-12 items-center justify-center gap-2 rounded-[0.875rem] px-4 text-[0.9375rem] font-semibold outline-none focus-visible:ring-[3px] focus-visible:ring-ring/50'

/** What a sheet is for, until its own screen arrives. */
const SHEET_WORDS: Record<PortfolioOverlay['kind'], { title: string; body: string }> = {
  'new-tab': { title: 'New tab', body: 'Name a tab, pick its kind, an icon and a colour.' },
  settings: { title: 'Tab settings', body: 'Rename, reorder, pin, archive or delete this tab.' },
  add: { title: 'Add an entry', body: 'Record a buy, sell, dividend, fee or transfer.' },
  import: { title: 'Import entries', body: 'Bring in a CSV file and check it before it is saved.' },
  customize: { title: 'Customize', body: 'Choose the parts, columns, sorting and grouping.' },
  edit: { title: 'Edit entry', body: 'Change this entry.' },
  duplicate: { title: 'Duplicate entry', body: 'Copy this entry.' },
  activity: { title: 'Entry', body: 'The details of this entry.' },
  price: { title: 'Update value', body: 'Set the value of a manual asset.' },
  asset: { title: 'Asset', body: 'The details of this asset.' },
}

function Notice({
  icon,
  title,
  children,
}: {
  icon: React.ReactNode
  title: string
  children?: React.ReactNode
}) {
  return (
    <div className={CARD} aria-live="polite">
      <div className="mx-auto mb-2 flex size-10 items-center justify-center text-[color:var(--text-secondary)]">
        {icon}
      </div>
      <p className="text-base font-bold">{title}</p>
      {children}
    </div>
  )
}

export default function Portfolio() {
  const { overlay, tab, view, open, close, selectTab, setView } = usePortfolioOverlay()
  const state = useQuery({
    queryKey: PORTFOLIO_STATE_KEY,
    queryFn: async () => portfolioCall('state'),
    retry: false,
    staleTime: 30_000,
  })

  const result = state.data
  const custom = useMemo(
    () => (result?.enabled ? result.data.tabs.filter((t) => !t.system) : []),
    [result]
  )
  const tabs = useMemo(
    () => custom.filter((t) => !t.archived).sort((a, b) => a.position - b.position),
    [custom]
  )
  const archived = useMemo(() => custom.filter((t) => t.archived), [custom])
  const accounts = result?.enabled ? result.data.accounts : []
  const members = result?.enabled ? result.data.members : []
  const shown = tab !== null && custom.some((t) => t.id === tab) ? tab : null
  const current = shown === null ? null : (custom.find((t) => t.id === shown) ?? null)
  const queries = useQueryClient()
  const refresh = () => queries.invalidateQueries({ queryKey: PORTFOLIO_KEY })
  // A tab that is gone (archived or deleted elsewhere) shows Upstox, from its first part.
  const part = shown === tab ? view : UPSTOX_VIEWS[0]
  const sheet = overlay ? SHEET_WORDS[overlay.kind] : null

  let body: React.ReactNode
  if (state.isPending) {
    body = (
      <Notice
        icon={<LoaderCircle aria-hidden="true" className="size-6 animate-spin" />}
        title="Loading the portfolio"
      />
    )
  } else if (state.isError) {
    body = (
      <Notice
        icon={<TriangleAlert aria-hidden="true" className="size-6" />}
        title={problemOf(state.error).message}
      >
        <button
          type="button"
          onClick={() => state.refetch()}
          className={`${ACTION} mt-3 border border-input bg-card hover:bg-accent`}
        >
          <RefreshCw aria-hidden="true" className="size-4" />
          Try again
        </button>
      </Notice>
    )
  } else if (result && !result.enabled) {
    body = (
      <Notice
        icon={<Power aria-hidden="true" className="size-6" />}
        title={result.message || WORDS.off}
      >
        <p className="mx-auto mt-1 max-w-sm text-sm text-[color:var(--text-secondary)]">
          Your entries are kept. Turn the portfolio on in the server settings to see them.
        </p>
      </Notice>
    )
  } else {
    body =
      current === null ? (
        <UpstoxTab view={part as UpstoxView} onView={setView} />
      ) : (
        <div className="flex flex-col gap-3">
          {current.archived && (
            <p className="flex items-center gap-2 rounded-[0.875rem] border border-[color:var(--border-strong)] bg-card px-3 py-2 text-sm font-medium text-[color:var(--text-secondary)]">
              <Archive aria-hidden="true" className="size-4 shrink-0" />
              This tab is archived. It is hidden from the dashboard and kept as it is.
            </p>
          )}
          <CustomStub
            view={part as CustomView}
            onView={setView}
            hideBody={
              part === 'activity' ||
              part === 'overview' ||
              part === 'holdings' ||
              part === 'performance'
            }
          />
          {(part === 'overview' || part === 'holdings') && (
            <TabOverview
              key={current.id}
              tab={current}
              part={part}
              accounts={accounts}
              onAdd={() => open({ kind: 'add' })}
              onImport={() => open({ kind: 'import' })}
            />
          )}
          {part === 'performance' && <PerformanceTab key={current.id} tab={current} />}
          {part === 'activity' && (
            <ActivityTab
              tab={current}
              accounts={accounts}
              onAdd={() => open({ kind: 'add' })}
              onImport={() => open({ kind: 'import' })}
              onEdit={(entry) => open({ kind: 'edit', activityId: entry.id })}
              onDuplicate={(entry) => open({ kind: 'duplicate', activityId: entry.id })}
            />
          )}
        </div>
      )
  }

  const usable = result?.enabled === true
  return (
    <div className="mx-auto flex w-full max-w-3xl flex-col gap-3 px-4 py-4 md:px-0">
      <div className="flex min-h-12 items-center justify-between gap-2">
        <h1 className="sd-display text-[1.75rem] leading-none font-bold md:text-[2rem]">
          Portfolio
        </h1>
        {usable && current !== null && (
          <div className="flex gap-2">
            <button
              type="button"
              aria-label="Tab settings"
              onClick={() => open({ kind: 'settings' })}
              className={`${ACTION} border border-input bg-card px-3 hover:bg-accent`}
            >
              <Settings2 aria-hidden="true" className="size-5" />
            </button>
            {!current.archived && (
              <button
                type="button"
                onClick={() => open({ kind: 'add' })}
                className={`${ACTION} bg-[color:var(--brand)] text-white hover:bg-[color:var(--brand-hover)]`}
              >
                <Plus aria-hidden="true" className="size-4" />
                Add
              </button>
            )}
          </div>
        )}
      </div>
      {usable && (
        <TabStrip
          tabs={tabs}
          selected={shown}
          onSelect={selectTab}
          onNew={() => open({ kind: 'new-tab' })}
        />
      )}
      {usable && archived.length > 0 && (
        <div className="flex flex-wrap items-center gap-2">
          <span className="text-sm font-semibold text-[color:var(--text-secondary)]">Archived</span>
          {archived.map((t) => (
            <button
              key={t.id}
              type="button"
              onClick={() => selectTab(t.id)}
              aria-pressed={shown === t.id}
              className="inline-flex min-h-12 items-center gap-2 rounded-full border border-dashed border-[color:var(--border-strong)] bg-card px-4 text-sm font-medium text-[color:var(--text-secondary)] outline-none hover:bg-accent focus-visible:ring-[3px] focus-visible:ring-ring/50"
            >
              <Archive aria-hidden="true" className="size-4" />
              {t.name}
            </button>
          ))}
        </div>
      )}
      {body}
      <NewTabSheet
        open={overlay?.kind === 'new-tab'}
        onClose={close}
        tabCount={tabs.length}
        onCreated={(made) => {
          void refresh()
          close()
          selectTab(made.id)
        }}
      />
      <TabSettingsSheet
        open={overlay?.kind === 'settings' && current !== null}
        onClose={close}
        tab={current}
        siblings={tabs}
        onChanged={() => void refresh()}
        onDeleted={() => {
          void refresh()
          close()
          selectTab(null)
        }}
      />
      <ActivitySheet
        open={
          (overlay?.kind === 'add' || overlay?.kind === 'edit' || overlay?.kind === 'duplicate') &&
          current !== null
        }
        onClose={close}
        tab={current}
        accounts={accounts}
        members={members}
        mode={
          overlay?.kind === 'edit' || overlay?.kind === 'duplicate'
            ? { kind: overlay.kind, activityId: overlay.activityId }
            : { kind: 'add' }
        }
        onSaved={() => {
          void refresh()
          close()
        }}
        onAccountCreated={() => void refresh()}
      />
      <ImportSheet
        open={overlay?.kind === 'import' && current !== null}
        onClose={close}
        tab={current}
        accounts={accounts}
        onChanged={() => void refresh()}
      />
      <BottomSheet
        open={
          sheet !== null &&
          overlay?.kind !== 'new-tab' &&
          overlay?.kind !== 'settings' &&
          overlay?.kind !== 'import' &&
          overlay?.kind !== 'add' &&
          overlay?.kind !== 'edit' &&
          overlay?.kind !== 'duplicate'
        }
        onOpenChange={(next) => !next && close()}
        title={sheet?.title ?? ''}
      >
        <div className="px-4 pb-4">
          <p className="text-sm text-[color:var(--text-secondary)]">{sheet?.body}</p>
          <p className="mt-2 text-sm text-[color:var(--text-tertiary)]">{WORDS.not_ready}</p>
        </div>
      </BottomSheet>
    </div>
  )
}
