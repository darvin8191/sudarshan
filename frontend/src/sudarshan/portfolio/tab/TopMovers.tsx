// SUDARSHAN M7.23: widget 4 of the Overview (design 4.6): the best 3 and worst 3 by Today's %,
// and the 3 largest contributions in rupees. A holding with no Today figure is never listed.
import { Signed } from '../Signed'
import type { ListedRow, Movers } from './rows'

function Group({ title, rows }: { title: string; rows: ListedRow[] }) {
  if (rows.length === 0) return null
  return (
    <div className="flex flex-col">
      <h3 className="text-xs font-semibold text-muted-foreground">{title}</h3>
      <ul aria-label={title} className="divide-y divide-[color:var(--divider)]">
        {rows.map((r) => (
          <li key={r.row.asset.id} className="flex min-h-12 items-center justify-between gap-3">
            <span className="min-w-0 truncate text-sm font-semibold">{r.row.asset.name}</span>
            <Signed
              paise={r.figures.today.paise ?? 0}
              pct={r.figures.today.pct}
              className="text-sm font-semibold"
            />
          </li>
        ))}
      </ul>
    </div>
  )
}

export function TopMovers({ movers }: { movers: Movers }) {
  const none =
    movers.best.length === 0 && movers.worst.length === 0 && movers.contributors.length === 0
  return (
    <section
      aria-label="Top movers"
      className="flex flex-col gap-3 rounded-[1.125rem] border bg-card px-4 py-3.5 shadow-[var(--shadow-card)]"
    >
      <h2 className="text-[0.9375rem] font-bold">Top movers</h2>
      {none ? (
        <p className="text-sm text-[color:var(--text-secondary)]">
          No holding has a Today figure yet.
        </p>
      ) : (
        <>
          <Group title="Best today" rows={movers.best} />
          <Group title="Worst today" rows={movers.worst} />
          <Group title="Largest moves in rupees" rows={movers.contributors} />
        </>
      )}
    </section>
  )
}
