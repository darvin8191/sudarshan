import { ArrowDown, ArrowUp, Check, Star, Trash2 } from 'lucide-react'
import { useEffect, useRef, useState } from 'react'
import { Switch } from '@/components/ui/switch'
import { cn } from '@/lib/utils'
import { BottomSheet } from '@/sudarshan/watchlist/BottomSheet'
import { PortfolioError, portfolioCall } from '../api'
import {
  type Filters,
  GROUPS,
  type LayoutState,
  NAME_MAX,
  type SavedView,
  SORTS,
  type TabInfo,
  type Widget,
  WORDS,
} from '../types'
import {
  COLUMN_LABELS,
  type CustomForm,
  chosen,
  formOf,
  GROUP_LABELS,
  type Item,
  moved,
  SORT_LABELS,
  sameSpec,
  specOf,
  WIDGET_LABELS,
} from './customize'
import { CHOICE, FIELD, LABEL, OFF, ON, PRIMARY, ProblemLine, SECONDARY } from './TabFields'
import { useSend } from './useSend'

/**
 * "Customize" (design 4.9): which sections the tab shows and in what order, which holding columns,
 * how they are sorted and grouped, the views saved by name and which one the tab opens with.
 * Switches and up and down buttons, never dragging, each 48 dp. The layout is saved with its
 * version: a layout changed on another screen answers with the server's sentence and nothing is
 * saved. Every request is sent once.
 */
export function CustomizeSheet({
  open,
  onClose,
  tab,
  layout,
  views,
  filters,
  drawn,
  appliedViewId,
  onUseView,
  onChanged,
}: {
  open: boolean
  onClose: () => void
  tab: TabInfo
  layout: LayoutState
  views: readonly SavedView[]
  /** The filters now on screen: what a view saved from here keeps. */
  filters: Filters
  /** The sections this screen can draw; the others keep their place in the layout. */
  drawn: readonly Widget[]
  appliedViewId: number | null
  onUseView: (view: SavedView | null) => void
  onChanged: () => void
}) {
  const [form, setForm] = useState<CustomForm>(() => formOf(layout.spec, drawn))
  const [name, setName] = useState('')
  const [asDefault, setAsDefault] = useState(false)
  const [confirming, setConfirming] = useState<number | null>(null)
  const requestId = useRef(crypto.randomUUID())
  const send = useSend()

  // biome-ignore lint/correctness/useExhaustiveDependencies: a fresh form on each opening, or when the layout was saved
  useEffect(() => {
    if (!open) return
    setForm(formOf(layout.spec, drawn))
    setName('')
    setAsDefault(false)
    setConfirming(null)
    requestId.current = crypto.randomUUID()
    send.clear()
  }, [open, layout.version])

  const spec = specOf(form, layout.spec, drawn)
  const dirty = !sameSpec(spec, layout.spec)
  const columnsOn = chosen(form.columns).length
  const problem = send.problem
    ? { message: send.problem.code === 'unanswered' ? WORDS.not_confirmed : send.problem.message }
    : null

  const call = async (
    fn: () => Promise<{ enabled: boolean; message?: string }>,
    then: () => void
  ) => {
    const ok = await send.run(async () => {
      const result = await fn()
      if (!result.enabled) {
        throw new PortfolioError({
          code: 'unavailable',
          message: result.message ?? '',
          entryId: null,
        })
      }
    })
    if (ok) then()
  }

  const saveLayout = () =>
    call(
      () => portfolioCall('layout_set', { id: tab.id, version: layout.version, spec }),
      () => {
        onChanged()
        onClose()
      }
    )
  const reset = () =>
    call(
      () => portfolioCall('layout_set', { id: tab.id, version: layout.version, spec: null }),
      () => {
        onChanged()
        onClose()
      }
    )
  const saveView = () =>
    call(
      () =>
        portfolioCall('view_create', {
          id: tab.id,
          request_id: requestId.current,
          name: name.trim(),
          spec: { filters, sort: form.sort, group: form.group, columns: chosen(form.columns) },
          is_default: asDefault,
        }),
      () => {
        requestId.current = crypto.randomUUID()
        setName('')
        setAsDefault(false)
        onChanged()
      }
    )
  const makeDefault = (view: SavedView) =>
    call(
      () =>
        portfolioCall('view_update', {
          id: view.id,
          version: view.version,
          is_default: !view.is_default,
        }),
      onChanged
    )
  const remove = (view: SavedView) =>
    call(
      () => portfolioCall('view_delete', { id: view.id, version: view.version }),
      () => {
        setConfirming(null)
        if (appliedViewId === view.id) onUseView(null)
        onChanged()
      }
    )

  const list = <T extends string>(
    kind: 'widgets' | 'columns',
    items: Item<T>[],
    label: (id: T) => string,
    mayTurnOff: (item: Item<T>) => boolean
  ) => (
    <ul
      aria-label={kind === 'widgets' ? 'Sections of the page' : 'Columns of the holdings'}
      className="flex flex-col divide-y divide-[color:var(--divider)] rounded-[0.875rem] border"
    >
      {items.map((item, n) => {
        const text = label(item.id)
        const set = (next: Item<T>[]) => setForm({ ...form, [kind]: next } as CustomForm)
        return (
          <li key={item.id} className="flex min-h-12 items-center gap-2 px-3">
            <span
              className={cn(
                'min-w-0 flex-1 text-[0.9375rem]',
                item.on ? 'font-semibold' : 'text-[color:var(--text-secondary)]'
              )}
            >
              {text}
            </span>
            <label className="flex min-h-12 items-center gap-2">
              <span className="w-7 text-right text-xs font-semibold text-[color:var(--text-secondary)]">
                {item.on ? 'On' : 'Off'}
              </span>
              <Switch
                aria-label={text}
                checked={item.on}
                disabled={send.pending || (item.on && !mayTurnOff(item))}
                onCheckedChange={(on) => set(items.map((i, k) => (k === n ? { ...i, on } : i)))}
              />
            </label>
            <button
              type="button"
              aria-label={`Move ${text} up`}
              disabled={n === 0 || send.pending}
              onClick={() => set(moved(items, n, -1))}
              className="inline-flex size-12 items-center justify-center rounded-full outline-none hover:bg-accent focus-visible:ring-[3px] focus-visible:ring-ring/50 disabled:opacity-40"
            >
              <ArrowUp aria-hidden="true" className="size-5" />
            </button>
            <button
              type="button"
              aria-label={`Move ${text} down`}
              disabled={n === items.length - 1 || send.pending}
              onClick={() => set(moved(items, n, 1))}
              className="inline-flex size-12 items-center justify-center rounded-full outline-none hover:bg-accent focus-visible:ring-[3px] focus-visible:ring-ring/50 disabled:opacity-40"
            >
              <ArrowDown aria-hidden="true" className="size-5" />
            </button>
          </li>
        )
      })}
    </ul>
  )

  const choices = <T extends string>(
    title: string,
    values: readonly T[],
    labels: Record<T, string>,
    value: T,
    onChange: (v: T) => void
  ) => (
    <fieldset className="flex flex-col gap-1.5">
      <legend className={LABEL}>{title}</legend>
      <div className="flex flex-wrap gap-2">
        {values.map((v) => (
          <button
            key={v}
            type="button"
            aria-pressed={value === v}
            disabled={send.pending}
            onClick={() => onChange(v)}
            className={cn(CHOICE, value === v ? ON : OFF)}
          >
            {value === v && <Check aria-hidden="true" className="size-4" />}
            {labels[v]}
          </button>
        ))}
      </div>
    </fieldset>
  )

  return (
    <BottomSheet
      open={open}
      onOpenChange={(next) => !next && onClose()}
      title="Customize"
      description={tab.name}
    >
      <div className="flex flex-col gap-5 overflow-y-auto px-4 pb-4">
        <section className="flex flex-col gap-2">
          <h3 className={LABEL}>Sections of the page</h3>
          {list(
            'widgets',
            form.widgets,
            (id) => WIDGET_LABELS[id],
            () => true
          )}
        </section>
        <section className="flex flex-col gap-2">
          <h3 className={LABEL}>Columns of the holdings</h3>
          {list(
            'columns',
            form.columns,
            (id) => COLUMN_LABELS[id],
            () => columnsOn > 1
          )}
          {columnsOn <= 1 && (
            <p className="text-xs text-[color:var(--text-tertiary)]">The last column stays on.</p>
          )}
        </section>
        {choices('Sort holdings by', SORTS, SORT_LABELS, form.sort, (sort) =>
          setForm({ ...form, sort })
        )}
        {choices('Group holdings by', GROUPS, GROUP_LABELS, form.group, (group) =>
          setForm({ ...form, group })
        )}

        <div className="flex flex-col gap-2">
          <button
            type="button"
            className={PRIMARY}
            disabled={!dirty || send.pending}
            onClick={saveLayout}
          >
            Save layout
          </button>
          <button type="button" className={SECONDARY} disabled={send.pending} onClick={reset}>
            Reset to the defaults of this kind of tab
          </button>
        </div>

        <section className="flex flex-col gap-2" aria-label="Saved views">
          <h3 className={LABEL}>Saved views</h3>
          {views.length === 0 ? (
            <p className="text-sm text-[color:var(--text-secondary)]">
              No saved views yet. A view keeps the filters on screen with the sort, grouping and
              columns above.
            </p>
          ) : (
            <ul className="flex flex-col divide-y divide-[color:var(--divider)] rounded-[0.875rem] border">
              {views.map((view) => (
                <li key={view.id} className="flex flex-col gap-1 px-3 py-2">
                  <p className="flex flex-wrap items-center gap-2 text-[0.9375rem] font-semibold">
                    {view.name}
                    {view.is_default && (
                      <span className="inline-flex items-center gap-1 rounded-full bg-[color:var(--brand-tint)] px-2 py-0.5 text-xs font-bold text-[color:var(--brand)]">
                        <Star aria-hidden="true" className="size-3" />
                        Opens first
                      </span>
                    )}
                    {appliedViewId === view.id && (
                      <span className="inline-flex items-center gap-1 text-xs font-bold text-[color:var(--text-secondary)]">
                        <Check aria-hidden="true" className="size-3" />
                        In use
                      </span>
                    )}
                  </p>
                  <div className="flex flex-wrap gap-2">
                    <button
                      type="button"
                      className={SECONDARY}
                      disabled={send.pending}
                      onClick={() => {
                        onUseView(view)
                        onClose()
                      }}
                    >
                      Use {view.name}
                    </button>
                    <button
                      type="button"
                      className={SECONDARY}
                      disabled={send.pending}
                      onClick={() => makeDefault(view)}
                    >
                      {view.is_default
                        ? `Do not open ${view.name} first`
                        : `Open ${view.name} first`}
                    </button>
                    {confirming === view.id ? (
                      <button
                        type="button"
                        className={SECONDARY}
                        disabled={send.pending}
                        onClick={() => remove(view)}
                      >
                        <Trash2 aria-hidden="true" className="size-4" />
                        Delete {view.name} for good
                      </button>
                    ) : (
                      <button
                        type="button"
                        className={SECONDARY}
                        disabled={send.pending}
                        onClick={() => setConfirming(view.id)}
                      >
                        <Trash2 aria-hidden="true" className="size-4" />
                        Delete {view.name}
                      </button>
                    )}
                  </div>
                </li>
              ))}
            </ul>
          )}
          <label className="flex flex-col gap-1.5">
            <span className={LABEL}>Name for a new view</span>
            <input
              className={FIELD}
              value={name}
              maxLength={NAME_MAX}
              disabled={send.pending}
              onChange={(event) => setName(event.target.value)}
            />
          </label>
          <label className="flex min-h-12 items-center gap-3">
            <input
              type="checkbox"
              className="size-6 accent-[color:var(--brand)]"
              checked={asDefault}
              disabled={send.pending}
              onChange={(event) => setAsDefault(event.target.checked)}
            />
            <span className="text-base font-semibold">Open this tab with it</span>
          </label>
          <button
            type="button"
            className={SECONDARY}
            disabled={name.trim() === '' || send.pending}
            onClick={saveView}
          >
            Save this view
          </button>
        </section>

        <ProblemLine problem={problem} />
      </div>
    </BottomSheet>
  )
}
