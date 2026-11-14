import { cn } from '@/lib/utils'
import { formatPrice } from '@/sudarshan/dashboard/format'
import type { ChainModel } from './model'
import { Smile, SmileKey, type SmilePoint } from './Smile'
import { strikeWords } from './words'

export const NO_IV_RANK = 'IV rank needs stored history, which is not built yet.'

const dash = (
  <>
    <span aria-hidden="true">&mdash;</span>
    <span className="sr-only">not available</span>
  </>
)

const iv = (value: number | null | undefined) => (value != null && value > 0 ? value : null)

/** "+1.4", "−0.8", and "0.0" with no sign for a skew that rounds to nothing. */
export function skewWords(skew: number): string {
  const shown = Number(skew.toFixed(1))
  return `${shown > 0 ? '+' : shown < 0 ? '−' : ''}${Math.abs(shown).toFixed(1)}`
}

/** The smile's points: the followed strikes only (a far strike's price is too old to invert). */
export function smilePoints(model: ChainModel): SmilePoint[] {
  return model.rows
    .filter((row) => row.live)
    .map((row) => ({
      strike: row.strike,
      call: iv(row.ce?.greeks?.iv),
      put: iv(row.pe?.greeks?.iv),
    }))
    .filter((point) => point.call !== null || point.put !== null)
}

function Fact({
  label,
  children,
  hint,
}: {
  label: string
  children: React.ReactNode
  hint?: string
}) {
  return (
    <div className="flex flex-col">
      <dt className="text-xs font-semibold text-[color:var(--text-secondary)]">{label}</dt>
      <dd className="text-base font-bold">{children}</dd>
      {hint && <dd className="text-xs font-medium text-[color:var(--text-tertiary)]">{hint}</dd>}
    </div>
  )
}

/**
 * The IV tab (M11.5, O7): ATM IV, the skew and the expected move now, the smile of the expiry, and
 * the same figures as a table. IV is worked out on the phone from the followed strikes' prices
 * (Black-76 on the forward), so it moves with ticks; the far strikes are left out. IV rank is not
 * shown: it needs stored history (M11.10), and the tab says so.
 */
export function IvTab({ model, dim }: { model: ChainModel; dim: boolean }) {
  const points = smilePoints(model)
  const move = model.expectedMove
  const stopped = model.years <= 0
  return (
    <section
      aria-label="Implied volatility"
      className={cn('flex flex-col gap-3', dim && 'text-[color:var(--stale)]')}
    >
      <dl
        data-iv-facts="true"
        className="grid grid-cols-3 gap-2 rounded-[1.125rem] border bg-card px-4 py-3 shadow-[var(--shadow-card)]"
      >
        <Fact label="ATM IV">{model.atmIv === null ? dash : `${model.atmIv.toFixed(1)}%`}</Fact>
        <Fact label="Skew" hint="put IV 2 below less call IV 2 above">
          {model.skew === null ? dash : skewWords(model.skew)}
        </Fact>
        <Fact label="Expected move" hint="the ATM straddle">
          {move === null ? (
            dash
          ) : (
            <>
              ±{formatPrice(move.premium)}
              {move.pct !== null && ` (${move.pct.toFixed(2)}%)`}
            </>
          )}
        </Fact>
      </dl>
      {points.length >= 2 ? (
        <>
          <figure className="rounded-[1.125rem] border bg-card px-2 py-3 shadow-[var(--shadow-card)]">
            <Smile points={points} atm={model.atm} />
            <figcaption className="flex flex-wrap items-center gap-x-4 gap-y-1 px-2 pt-1 text-xs font-semibold text-[color:var(--text-secondary)]">
              <span className="inline-flex items-center gap-1.5">
                <SmileKey side="call" /> Call IV
              </span>
              <span className="inline-flex items-center gap-1.5">
                <SmileKey side="put" /> Put IV
              </span>
              <span>percent, by strike</span>
            </figcaption>
          </figure>
          <table data-iv-table="true" className="w-full border-collapse text-sm">
            <caption className="sr-only">Implied volatility by strike, in percent</caption>
            <thead>
              <tr className="text-xs font-bold text-[color:var(--text-tertiary)]">
                <th scope="col" className="py-1.5 text-left">
                  STRIKE
                </th>
                <th scope="col" className="py-1.5 text-right">
                  CALL IV
                </th>
                <th scope="col" className="py-1.5 text-right">
                  PUT IV
                </th>
              </tr>
            </thead>
            <tbody className="divide-y divide-[color:var(--divider)]">
              {points.map((point) => (
                <tr key={point.strike} data-iv-strike={point.strike}>
                  <th scope="row" className="py-2 text-left font-bold">
                    {strikeWords(point.strike)}
                    {point.strike === model.atm && (
                      <span className="ml-1.5 rounded bg-[color:var(--primary-tint)] px-1 text-[0.6875rem] font-bold text-primary">
                        ATM
                      </span>
                    )}
                  </th>
                  <td className="py-2 text-right font-semibold">
                    {point.call === null ? dash : point.call.toFixed(1)}
                  </td>
                  <td className="py-2 text-right font-semibold">
                    {point.put === null ? dash : point.put.toFixed(1)}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </>
      ) : (
        <p data-iv-none="true" className="text-sm font-semibold text-[color:var(--text-secondary)]">
          {stopped
            ? 'These contracts have stopped trading, so there is no IV to work out.'
            : 'IV is not worked out yet: the followed strikes have no prices.'}
        </p>
      )}
      <p className="text-xs font-medium text-muted-foreground">{NO_IV_RANK}</p>
    </section>
  )
}
