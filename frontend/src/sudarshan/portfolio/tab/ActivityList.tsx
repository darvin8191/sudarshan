import {
  ArrowLeftRight,
  Coins,
  EllipsisVertical,
  FilePen,
  Gift,
  type LucideIcon,
  Minus,
  Percent,
  Plus,
  Receipt,
  Split,
  Ticket,
} from 'lucide-react'
import { Fragment, useState } from 'react'
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu'
import { cn } from '@/lib/utils'
import { BottomSheet } from '@/sudarshan/watchlist/BottomSheet'
import { DANGER, ProblemLine, SECONDARY } from '../sheets/TabFields'
import { useSend } from '../sheets/useSend'
import {
  ACTIVITY_KIND_LABELS,
  type Account,
  type Activity,
  type ActivityKind,
  type Asset,
} from '../types'
import { dayText, monthOf, priceText, quantityText, rupeesText } from './format'

// A kind is told by its icon and its word, never by colour.
const KIND_ICONS: Record<ActivityKind, LucideIcon> = {
  BUY: Plus,
  SELL: Minus,
  DIVIDEND: Coins,
  INTEREST: Percent,
  FEE: Receipt,
  TRANSFER: ArrowLeftRight,
  SPLIT: Split,
  BONUS: Gift,
  RIGHTS: Ticket,
}

export function KindChip({ kind }: { kind: ActivityKind }) {
  const Icon = KIND_ICONS[kind]
  return (
    <span className="inline-flex h-7 items-center gap-1 rounded-full border border-[color:var(--border-strong)] bg-card px-2.5 text-xs font-bold text-[color:var(--text-primary)]">
      <Icon aria-hidden="true" className="size-3.5" />
      {ACTIVITY_KIND_LABELS[kind]}
    </span>
  )
}

/** What the middle of a row says: the quantity and price, a split's ratio, or nothing. */
export function detailText(a: Activity): string | null {
  if (a.kind === 'SPLIT' && a.ratio_num && a.ratio_den) {
    return `${a.ratio_num} new for every ${a.ratio_den} held`
  }
  if (a.qty_u !== null && a.price_u !== null) {
    return `${quantityText(a.qty_u)} × ${priceText(a.price_u)}`
  }
  if (a.qty_u !== null) return quantityText(a.qty_u)
  return null
}

function figureOf(a: Activity): number | null {
  return a.amount_paise ?? a.value_paise
}

interface Group {
  key: number
  label: string
  rows: Activity[]
}

/** The entries as the service sent them (newest first), under their month. */
export function groupByMonth(items: readonly Activity[]): Group[] {
  const groups: Group[] = []
  for (const item of items) {
    const month = monthOf(item.trade_date)
    const last = groups[groups.length - 1]
    if (last && last.key === month.key) last.rows.push(item)
    else groups.push({ ...month, rows: [item] })
  }
  return groups
}

const ACTION =
  'inline-flex min-h-12 items-center justify-center gap-2 rounded-[0.875rem] px-4 text-[0.9375rem] font-semibold outline-none focus-visible:ring-[3px] focus-visible:ring-ring/50'

/**
 * The entries of a tab: grouped by month, newest first. Each row has its date, a kind chip, the
 * asset, quantity times price, the amount and the account, and a menu with Edit, Duplicate and
 * Delete. Delete asks first and is sent once. With nothing in the tab it says so, with the two
 * ways to fill it.
 */
export function ActivityList({
  items,
  assets,
  accounts,
  hasMore,
  loadingMore,
  onMore,
  onAdd,
  onImport,
  onEdit,
  onDuplicate,
  onDelete,
}: {
  items: readonly Activity[]
  assets: readonly Asset[]
  accounts: readonly Account[]
  hasMore: boolean
  loadingMore: boolean
  onMore: () => void
  onAdd: () => void
  onImport: () => void
  onEdit: (entry: Activity) => void
  onDuplicate: (entry: Activity) => void
  /** Sends the delete; throws a `PortfolioError` in the owner's words. */
  onDelete: (entry: Activity) => Promise<void>
}) {
  const [asking, setAsking] = useState<Activity | null>(null)
  const send = useSend()
  const assetName = (id: number | null) =>
    id === null ? null : (assets.find((a) => a.id === id)?.name ?? null)
  const accountName = (id: number | null) =>
    id === null ? null : (accounts.find((a) => a.id === id)?.name ?? null)

  if (items.length === 0) {
    return (
      <section
        aria-label="Activity"
        className="flex flex-col items-center gap-3 rounded-[1.125rem] border bg-card px-6 py-8 text-center shadow-[var(--shadow-card)]"
      >
        <p className="text-base font-bold">Nothing here yet. Add an entry or import a file.</p>
        <div className="flex flex-wrap justify-center gap-2">
          <button
            type="button"
            onClick={onAdd}
            className={`${ACTION} bg-[color:var(--brand)] text-white hover:bg-[color:var(--brand-hover)]`}
          >
            <Plus aria-hidden="true" className="size-4" />
            Add an entry
          </button>
          <button type="button" onClick={onImport} className={SECONDARY}>
            Import a file
          </button>
        </div>
      </section>
    )
  }

  const confirm = async () => {
    if (!asking) return
    const entry = asking
    const ok = await send.run(() => onDelete(entry))
    if (ok) setAsking(null)
  }

  return (
    <section aria-label="Activity" className="flex flex-col gap-2">
      {groupByMonth(items).map((group) => (
        <Fragment key={group.key}>
          <h2 className="mt-2 text-sm font-bold text-[color:var(--text-secondary)]">
            {group.label}
          </h2>
          <ul className="flex flex-col gap-2">
            {group.rows.map((entry) => {
              const asset = assetName(entry.asset_id)
              const detail = detailText(entry)
              const figure = figureOf(entry)
              const account = accountName(entry.account_id)
              const to = accountName(entry.to_account_id)
              return (
                <li
                  key={entry.id}
                  data-testid="activity-row"
                  className="flex items-start gap-2 rounded-[1.125rem] border bg-card py-3 pr-1 pl-4 shadow-[var(--shadow-card)]"
                >
                  <div className="flex min-w-0 flex-1 flex-col gap-1">
                    <div className="flex flex-wrap items-center gap-2">
                      <span className="text-sm font-semibold text-[color:var(--text-secondary)]">
                        {dayText(entry.trade_date)}
                      </span>
                      <KindChip kind={entry.kind} />
                      {entry.status === 'draft' && (
                        <span className="inline-flex h-7 items-center gap-1 rounded-full border border-[color:var(--border-strong)] px-2.5 text-xs font-bold text-[color:var(--warning-text)]">
                          <FilePen aria-hidden="true" className="size-3.5" />
                          Draft
                        </span>
                      )}
                    </div>
                    <div className="flex items-baseline justify-between gap-3">
                      <span className="min-w-0 truncate text-base font-bold">
                        {asset ?? (entry.note ? entry.note : ACTIVITY_KIND_LABELS[entry.kind])}
                      </span>
                      {figure !== null && (
                        <span className="shrink-0 text-base font-bold tabular-nums">
                          {rupeesText(figure)}
                        </span>
                      )}
                    </div>
                    <div className="flex flex-wrap items-baseline justify-between gap-x-3 text-sm text-[color:var(--text-secondary)]">
                      <span className="tabular-nums">{detail ?? ' '}</span>
                      {entry.charges_paise !== 0 && (
                        <span className="tabular-nums">
                          Charges {rupeesText(entry.charges_paise)}
                        </span>
                      )}
                    </div>
                    {(account || to) && (
                      <span className="text-xs text-[color:var(--text-tertiary)]">
                        {to ? `${account ?? 'Outside'} to ${to}` : account}
                      </span>
                    )}
                  </div>
                  <DropdownMenu>
                    <DropdownMenuTrigger asChild>
                      <button
                        type="button"
                        aria-label={`Actions for the ${ACTIVITY_KIND_LABELS[entry.kind]} of ${dayText(entry.trade_date)}`}
                        className="inline-flex size-12 shrink-0 items-center justify-center rounded-full outline-none hover:bg-accent focus-visible:ring-[3px] focus-visible:ring-ring/50"
                      >
                        <EllipsisVertical aria-hidden="true" className="size-5" />
                      </button>
                    </DropdownMenuTrigger>
                    <DropdownMenuContent align="end">
                      <DropdownMenuItem className="min-h-12" onSelect={() => onEdit(entry)}>
                        Edit
                      </DropdownMenuItem>
                      <DropdownMenuItem className="min-h-12" onSelect={() => onDuplicate(entry)}>
                        Duplicate
                      </DropdownMenuItem>
                      <DropdownMenuItem
                        className={cn('min-h-12 text-[color:var(--down)]')}
                        onSelect={() => {
                          send.clear()
                          setAsking(entry)
                        }}
                      >
                        Delete
                      </DropdownMenuItem>
                    </DropdownMenuContent>
                  </DropdownMenu>
                </li>
              )
            })}
          </ul>
        </Fragment>
      ))}
      {hasMore && (
        <button
          type="button"
          onClick={onMore}
          disabled={loadingMore}
          className={`${SECONDARY} self-center`}
        >
          {loadingMore ? 'Loading' : 'Show older entries'}
        </button>
      )}
      <BottomSheet
        open={asking !== null}
        onOpenChange={(next) => !next && !send.pending && setAsking(null)}
        title="Delete this entry?"
        description={
          asking
            ? `${ACTIVITY_KIND_LABELS[asking.kind]} on ${dayText(asking.trade_date)}`
            : undefined
        }
      >
        <div className="flex flex-col gap-3 px-4 pb-4">
          <p className="text-sm text-[color:var(--text-secondary)]">
            The entry is removed from this tab and its figures are worked out again.
          </p>
          <ProblemLine problem={send.problem} />
          <div className="flex gap-2">
            <button
              type="button"
              className={`${SECONDARY} flex-1`}
              disabled={send.pending}
              onClick={() => setAsking(null)}
            >
              Keep it
            </button>
            <button
              type="button"
              className={`${DANGER} flex-1`}
              disabled={send.pending}
              onClick={() => void confirm()}
            >
              {send.pending ? 'Deleting' : 'Delete'}
            </button>
          </div>
        </div>
      </BottomSheet>
    </section>
  )
}
