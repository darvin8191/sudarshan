// SUDARSHAN M7.18: the Portfolio page, phone first. The Upstox account (read-only) and the
// owner's own tabs, as the portfolio service holds them (blueprints/sudarshan_portfolio.py).
// This is the shell: the tab strip, what is open kept in the address, and the states in words.
// Each part of a tab arrives as its own change and replaces its stub in sudarshan/portfolio.
import { useQuery } from '@tanstack/react-query'
import { LoaderCircle, Plus, Power, RefreshCw, TriangleAlert } from 'lucide-react'
import { useMemo } from 'react'
import { portfolioCall, problemOf } from '@/sudarshan/portfolio/api'
import { CustomStub, UpstoxStub } from '@/sudarshan/portfolio/StubViews'
import { TabStrip } from '@/sudarshan/portfolio/TabStrip'
import { WORDS } from '@/sudarshan/portfolio/types'
import {
  type CustomView,
  type PortfolioOverlay,
  UPSTOX_VIEWS,
  type UpstoxView,
  usePortfolioOverlay,
} from '@/sudarshan/portfolio/usePortfolioOverlay'
import { BottomSheet } from '@/sudarshan/watchlist/BottomSheet'

export const PORTFOLIO_STATE_KEY = ['sudarshan', 'portfolio', 'state'] as const

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
  const tabs = useMemo(
    () =>
      result?.enabled
        ? result.data.tabs
            .filter((t) => !t.system && !t.archived)
            .sort((a, b) => a.position - b.position)
        : [],
    [result]
  )
  const shown = tab !== null && tabs.some((t) => t.id === tab) ? tab : null
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
      shown === null ? (
        <UpstoxStub view={part as UpstoxView} onView={setView} />
      ) : (
        <CustomStub view={part as CustomView} onView={setView} />
      )
  }

  const usable = result?.enabled === true
  return (
    <div className="mx-auto flex w-full max-w-3xl flex-col gap-3 px-4 py-4 md:px-0">
      <div className="flex min-h-12 items-center justify-between gap-2">
        <h1 className="sd-display text-[1.75rem] leading-none font-bold md:text-[2rem]">
          Portfolio
        </h1>
        {usable && shown !== null && (
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
      {usable && (
        <TabStrip
          tabs={tabs}
          selected={shown}
          onSelect={selectTab}
          onNew={() => open({ kind: 'new-tab' })}
        />
      )}
      {body}
      <BottomSheet
        open={sheet !== null}
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
