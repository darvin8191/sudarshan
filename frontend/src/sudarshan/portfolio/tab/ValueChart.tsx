// SUDARSHAN M7.29: widget 6 of the Overview (design 4.6): the value of the tab against the net
// money put in, one line each, drawn with lightweight-charts. The library is loaded when this
// widget first shows, so the rest of the page does not carry it. The lines differ in weight and
// stroke (solid and dashed) and are named under the chart, never told by colour alone. Today's
// point follows the live value from the Overview, as the figures above it do.
import { useEffect, useMemo, useRef, useState } from 'react'
import type { Day, PerformanceReport } from '../types'
import { rupeesText } from './format'
import { chartDay, withToday } from './performance'

// The D tokens as the chart library reads colours (hex): --brand, --text-secondary, --divider
// and --border-strong of sudarshan/theme.css.
export const CHART_COLOURS = {
  value: '#0E5A73',
  put_in: '#475569',
  grid: '#EDF0F3',
  edge: '#808A99',
  text: '#475569',
} as const

const HEIGHT = 220
const SECTION = 'rounded-[1.125rem] border bg-card px-4 py-3.5 shadow-[var(--shadow-card)]'

interface Drawing {
  value: { setData: (d: unknown[]) => void; update: (d: unknown) => void }
  putIn: { setData: (d: unknown[]) => void; update: (d: unknown) => void }
  chart: { remove: () => void; timeScale: () => { fitContent: () => void } }
  length: number
  first: Day
}

type Pair = { value: { time: string; value: number }; putIn: { time: string; value: number } }
const pairOf = (p: { day: Day; value_paise: number; net_put_in_paise: number }): Pair => ({
  value: { time: chartDay(p.day), value: p.value_paise / 100 },
  putIn: { time: chartDay(p.day), value: p.net_put_in_paise / 100 },
})

export function ValueChart({
  report,
  today,
  liveValuePaise,
}: {
  report: PerformanceReport
  today: Day
  /** The tab's value at the latest tick; null when it is not live or the tab is narrowed. */
  liveValuePaise: number | null
}) {
  const points = useMemo(
    () => withToday(report.series, today, liveValuePaise, report.net_put_in_paise),
    [report.series, report.net_put_in_paise, today, liveValuePaise]
  )
  const host = useRef<HTMLDivElement>(null)
  const drawing = useRef<Drawing | null>(null)
  const latest = useRef(points)
  latest.current = points
  const [failed, setFailed] = useState(false)
  const has = points.length > 0

  // The chart is made once the library arrives, and taken down with the widget.
  useEffect(() => {
    const el = host.current
    if (!has || !el) return
    let gone = false
    import('lightweight-charts')
      .then(({ createChart, LineSeries, ColorType, LineStyle }) => {
        if (gone || !host.current) return
        const chart = createChart(el, {
          height: HEIGHT,
          autoSize: true,
          layout: {
            background: { type: ColorType.Solid, color: 'transparent' },
            textColor: CHART_COLOURS.text,
            fontFamily: 'Barlow, sans-serif',
          },
          grid: {
            vertLines: { visible: false },
            horzLines: { color: CHART_COLOURS.grid },
          },
          rightPriceScale: { borderColor: CHART_COLOURS.edge },
          timeScale: { borderColor: CHART_COLOURS.edge, timeVisible: false },
          localization: {
            priceFormatter: (v: number) => new Intl.NumberFormat('en-IN').format(Math.round(v)),
          },
        })
        const putIn = chart.addSeries(LineSeries, {
          color: CHART_COLOURS.put_in,
          lineWidth: 2,
          lineStyle: LineStyle.Dashed,
          priceLineVisible: false,
          lastValueVisible: false,
        })
        const value = chart.addSeries(LineSeries, {
          color: CHART_COLOURS.value,
          lineWidth: 3,
          priceLineVisible: false,
          lastValueVisible: false,
        })
        const now = latest.current
        value.setData(now.map((p) => pairOf(p).value))
        putIn.setData(now.map((p) => pairOf(p).putIn))
        chart.timeScale().fitContent()
        drawing.current = {
          value,
          putIn,
          chart,
          length: now.length,
          first: now[0]?.day ?? 0,
        } as unknown as Drawing
      })
      .catch(() => setFailed(true))
    return () => {
      gone = true
      drawing.current?.chart.remove()
      drawing.current = null
    }
  }, [has])

  // A tick moves the last point; any other change draws the whole series again.
  useEffect(() => {
    const d = drawing.current
    if (!d || points.length === 0) return
    const last = points[points.length - 1]
    if (last === undefined) return
    if (d.length === points.length && d.first === points[0]?.day) {
      d.value.update(pairOf(last).value)
      d.putIn.update(pairOf(last).putIn)
    } else {
      d.value.setData(points.map((p) => pairOf(p).value))
      d.putIn.setData(points.map((p) => pairOf(p).putIn))
      d.chart.timeScale().fitContent()
    }
    d.length = points.length
    d.first = points[0]?.day ?? 0
  }, [points])

  const last = points[points.length - 1]
  return (
    <section aria-label="Value against money put in" className={`${SECTION} flex flex-col gap-2`}>
      <h2 className="text-base font-bold">Value and money put in</h2>
      {!has || failed ? (
        <p className="text-sm text-[color:var(--text-secondary)]">
          {failed
            ? 'The chart could not be drawn. The figures are below.'
            : (report.series_words ??
              'Daily history starts once every holding here has a closing price.')}
        </p>
      ) : (
        <>
          <div
            ref={host}
            role="img"
            aria-label={`Value ${last ? rupeesText(last.value_paise) : ''} against net money put in ${last ? rupeesText(last.net_put_in_paise) : ''}`}
            style={{ height: HEIGHT }}
            className="w-full"
          />
          <ul className="flex flex-wrap gap-x-4 gap-y-1 text-sm text-[color:var(--text-secondary)]">
            <li className="inline-flex items-center gap-2">
              <svg aria-hidden="true" width="24" height="6" focusable="false">
                <line x1="0" y1="3" x2="24" y2="3" stroke={CHART_COLOURS.value} strokeWidth="3" />
              </svg>
              Value{last ? ` ${rupeesText(last.value_paise)}` : ''}
            </li>
            <li className="inline-flex items-center gap-2">
              <svg aria-hidden="true" width="24" height="6" focusable="false">
                <line
                  x1="0"
                  y1="3"
                  x2="24"
                  y2="3"
                  stroke={CHART_COLOURS.put_in}
                  strokeWidth="2"
                  strokeDasharray="4 3"
                />
              </svg>
              Net money put in{last ? ` ${rupeesText(last.net_put_in_paise)}` : ''}
            </li>
          </ul>
          {report.series_words && (
            <p className="text-[0.78125rem] text-[color:var(--text-secondary)]">
              {report.series_words}
            </p>
          )}
        </>
      )}
    </section>
  )
}
