// SUDARSHAN M14.3, JD4: the equity curve and its drawdown, drawn with lightweight-charts. The
// library is loaded when this chart first shows, so the page does not carry it before then. Two
// panes: the closed-trade equity as an area on top, and under it the drawdown from the peak as an
// area in the loss tint, with a label of its deepest point. The two are named under the chart and
// the last values are in words, never told by colour alone (design 6.2, 6.5).
import { useEffect, useRef, useState } from 'react'
import { directionOf } from '@/sudarshan/dashboard/format'
import { TrendArrow } from '@/sudarshan/dashboard/Trend'
import { chartDay, tileRupees } from './analyticsWords'
import { dayWords } from './live'
import type { Equity } from './types'

/** The D tokens as the chart library reads colours (hex): --brand, --down, --down-cvd, --text-secondary, --divider, --border-strong. */
export const CHART_COLOURS = {
  equity: '#0E5A73',
  equityFill: 'rgba(14, 90, 115, 0.16)',
  down: '#C3282E',
  downFill: 'rgba(195, 40, 46, 0.22)',
  downCvd: '#AC5700',
  downCvdFill: 'rgba(172, 87, 0, 0.24)',
  grid: '#EDF0F3',
  edge: '#808A99',
  text: '#475569',
} as const

const HEIGHT = 300
const SECTION = 'rounded-[1.125rem] border bg-card px-4 py-3.5 shadow-[var(--shadow-card)]'

/** Colour-blind mode swaps the loss red for its orange, as the rest of the app does. */
const colourBlind = () =>
  typeof document !== 'undefined' && document.documentElement.dataset.cvd === '1'

interface Drawing {
  chart: { remove: () => void }
}

export function EquityChart({ equity }: { equity: Equity }) {
  const host = useRef<HTMLDivElement>(null)
  const drawing = useRef<Drawing | null>(null)
  const [failed, setFailed] = useState(false)
  const points = equity.points
  const has = points.length > 0
  const dd = equity.drawdown
  const last = points[points.length - 1]

  // biome-ignore lint/correctness/useExhaustiveDependencies: the whole series is drawn again when it changes
  useEffect(() => {
    const el = host.current
    if (!has || !el) return
    let gone = false
    import('lightweight-charts')
      .then(({ createChart, AreaSeries, ColorType }) => {
        if (gone || !host.current) return
        const cvd = colourBlind()
        const chart = createChart(el, {
          height: HEIGHT,
          autoSize: true,
          layout: {
            background: { type: ColorType.Solid, color: 'transparent' },
            textColor: CHART_COLOURS.text,
            fontFamily: 'Barlow, sans-serif',
          },
          grid: { vertLines: { visible: false }, horzLines: { color: CHART_COLOURS.grid } },
          rightPriceScale: { borderColor: CHART_COLOURS.edge },
          timeScale: { borderColor: CHART_COLOURS.edge, timeVisible: false },
          localization: {
            priceFormatter: (v: number) => new Intl.NumberFormat('en-IN').format(Math.round(v)),
          },
        })
        const curve = chart.addSeries(AreaSeries, {
          lineColor: CHART_COLOURS.equity,
          topColor: CHART_COLOURS.equityFill,
          bottomColor: 'rgba(14, 90, 115, 0)',
          lineWidth: 3,
          priceLineVisible: false,
          lastValueVisible: true,
          title: 'Equity',
        })
        curve.setData(points.map((p) => ({ time: chartDay(p.day), value: p.equity_paise / 100 })))
        const under = chart.addSeries(
          AreaSeries,
          {
            lineColor: cvd ? CHART_COLOURS.downCvd : CHART_COLOURS.down,
            topColor: cvd ? CHART_COLOURS.downCvdFill : CHART_COLOURS.downFill,
            bottomColor: cvd ? CHART_COLOURS.downCvdFill : CHART_COLOURS.downFill,
            invertFilledArea: true,
            lineWidth: 2,
            priceLineVisible: false,
            lastValueVisible: false,
            title: 'Drawdown',
          },
          1
        )
        under.setData(points.map((p) => ({ time: chartDay(p.day), value: p.drawdown_paise / 100 })))
        chart.panes()[1]?.setHeight(90)
        chart.timeScale().fitContent()
        drawing.current = { chart }
      })
      .catch(() => setFailed(true))
    return () => {
      gone = true
      drawing.current?.chart.remove()
      drawing.current = null
    }
  }, [equity])

  const summary = last
    ? `Equity ${tileRupees(last.equity_paise)}. Deepest drawdown ${tileRupees(dd.max_drawdown_paise)}.`
    : ''
  // The legend's swatch is drawn in what the chart draws (review L7): orange in colour-blind mode.
  const cvd = colourBlind()
  return (
    <section aria-label="Equity curve" className={`${SECTION} flex flex-col gap-2`}>
      <h2 className="text-base font-bold">Equity and drawdown</h2>
      {!has || failed ? (
        <p className="text-sm text-[color:var(--text-secondary)]">
          {failed
            ? 'The chart could not be drawn. The figures are below.'
            : 'The curve starts with your first closed trade.'}
        </p>
      ) : (
        <div
          ref={host}
          role="img"
          aria-label={summary}
          data-testid="equity-chart"
          style={{ height: HEIGHT }}
          className="w-full"
        />
      )}
      {last && (
        <ul className="flex flex-col gap-1 text-sm text-[color:var(--text-secondary)]">
          <li className="inline-flex flex-wrap items-center gap-2">
            <svg aria-hidden="true" width="24" height="6" focusable="false">
              <line x1="0" y1="3" x2="24" y2="3" stroke={CHART_COLOURS.equity} strokeWidth="3" />
            </svg>
            <span>
              Equity, the sum of closed trades:{' '}
              <span className="inline-flex items-center gap-1 font-semibold text-foreground">
                <TrendArrow
                  direction={directionOf(last.equity_paise / 100)}
                  className="size-[0.5625rem]"
                />
                {tileRupees(last.equity_paise)}
              </span>
            </span>
          </li>
          <li className="inline-flex flex-wrap items-center gap-2" data-testid="drawdown-label">
            <svg aria-hidden="true" width="24" height="12" focusable="false">
              <rect
                x="0"
                y="0"
                width="24"
                height="12"
                fill={cvd ? CHART_COLOURS.downCvdFill : CHART_COLOURS.downFill}
              />
              <line
                x1="0"
                y1="12"
                x2="24"
                y2="12"
                stroke={cvd ? CHART_COLOURS.downCvd : CHART_COLOURS.down}
                strokeWidth="2"
              />
            </svg>
            <span>
              Drawdown, below the peak:{' '}
              <span className="font-semibold text-foreground">
                {dd.max_drawdown_paise === 0
                  ? 'none'
                  : `deepest ${tileRupees(dd.max_drawdown_paise)}`}
                {dd.max_drawdown_pct !== null && dd.max_drawdown_paise !== 0
                  ? ` (${dd.max_drawdown_pct.startsWith('-') ? '−' : ''}${dd.max_drawdown_pct.replace('-', '')}%)`
                  : ''}
              </span>
              {dd.trough_day !== null && dd.max_drawdown_paise !== 0 && (
                <> on {dayWords(dd.trough_day)}</>
              )}
              {dd.duration_state === 'recovered' && dd.duration_days !== null && (
                <>, recovered after {dd.duration_days} trading days</>
              )}
              {dd.duration_state === 'still_in_drawdown' && <>, still in drawdown</>}
            </span>
          </li>
        </ul>
      )}
    </section>
  )
}
