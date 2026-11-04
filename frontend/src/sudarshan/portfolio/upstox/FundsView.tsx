// SUDARSHAN M7.1: the Upstox funds, line by line, read-only. Between midnight and 5:30 AM the
// service reads nothing and says so; a figure that is not there is a sentence, never a zero.
import { cn } from '@/lib/utils'
import { Signed } from '../Signed'
import { rupeesText } from '../tab/format'
import type { UpstoxFunds, UpstoxPart } from '../types'
import { CARD, Figure, NoFigures, PartNote, ReadStamp } from './parts'

export function FundsView({ part }: { part: UpstoxPart<UpstoxFunds> | undefined }) {
  const funds = part?.data
  if (!funds) return <NoFigures part={part}>The funds could not be read yet.</NoFigures>
  return (
    <div className="flex flex-col gap-3">
      <PartNote part={part} />
      <section aria-label="Funds summary" className={cn(CARD, 'flex flex-col gap-2 px-4 py-3.5')}>
        <dl>
          <Figure label="Available to trade">
            <Signed paise={funds.available_paise} balance decimals={2} />
          </Figure>
        </dl>
        <ReadStamp at={part?.read_at ?? null} />
      </section>
      {funds.groups.map((group) => (
        <section key={group.key} aria-label={group.label} className={cn(CARD, 'px-4 py-3')}>
          <div className="flex min-h-12 items-center justify-between gap-3">
            <h2 className="text-[0.9375rem] font-bold">{group.label}</h2>
            <span className="text-[0.9375rem] font-bold">
              {group.total_paise === null ? (
                <span className="text-sm font-semibold text-[color:var(--text-secondary)]">
                  Not known yet
                </span>
              ) : (
                <Signed paise={group.total_paise} balance />
              )}
            </span>
          </div>
          <dl className="divide-y divide-[color:var(--divider)]">
            {group.lines.map((line) => (
              <div
                key={line.key}
                className="flex min-h-12 items-center justify-between gap-3 text-sm"
              >
                <dt className="text-[color:var(--text-secondary)]">{line.label}</dt>
                <dd className="font-semibold" title={rupeesText(line.paise)}>
                  <Signed paise={line.paise} balance />
                </dd>
              </div>
            ))}
          </dl>
        </section>
      ))}
    </div>
  )
}
