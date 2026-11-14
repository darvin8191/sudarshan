import { strikeWords } from './words'

export interface SmilePoint {
  strike: number
  /** IV in percent; null where it could not be worked out. */
  call: number | null
  put: number | null
}

const W = 328
const H = 196
const LEFT = 30
const RIGHT = 8
const TOP = 12
const BOTTOM = 26

/** The scale a smile is drawn on: the strikes across, the IVs up, with a little room. */
export function smileScale(points: readonly SmilePoint[]) {
  const ivs = points
    .flatMap((each) => [each.call, each.put])
    .filter((iv): iv is number => iv !== null)
  if (points.length < 2 || ivs.length === 0) return null
  const low = Math.floor(Math.min(...ivs))
  const high = Math.max(Math.ceil(Math.max(...ivs)), low + 1)
  const first = points[0].strike
  const last = points[points.length - 1].strike
  return {
    low,
    high,
    x: (strike: number) => LEFT + ((strike - first) / (last - first)) * (W - LEFT - RIGHT),
    y: (iv: number) => TOP + (1 - (iv - low) / (high - low)) * (H - TOP - BOTTOM),
  }
}

/**
 * The smile of one expiry (M11.5, O7), as plain SVG: call IV and put IV by strike. The two lines
 * differ by dash and by the shape of their points as well as by ink, and the at-the-money strike is
 * a labelled rule. Decoration for the eye: the table under it carries the same figures for a screen
 * reader, so the drawing is hidden from one.
 */
export function Smile({ points, atm }: { points: readonly SmilePoint[]; atm: number | null }) {
  const scale = smileScale(points)
  if (!scale) return null
  const path = (side: 'call' | 'put') =>
    points
      .filter((each) => each[side] !== null)
      .map(
        (each) => `${scale.x(each.strike).toFixed(1)},${scale.y(each[side] as number).toFixed(1)}`
      )
      .join(' ')
  const ticks = [points[0], points[Math.floor((points.length - 1) / 2)], points[points.length - 1]]
  const middle = (scale.low + scale.high) / 2
  return (
    <svg
      viewBox={`0 0 ${W} ${H}`}
      role="img"
      aria-hidden="true"
      data-smile="true"
      className="h-auto w-full text-[color:var(--text-tertiary)]"
    >
      {[scale.low, middle, scale.high].map((iv) => (
        <g key={iv}>
          <line
            x1={LEFT}
            x2={W - RIGHT}
            y1={scale.y(iv)}
            y2={scale.y(iv)}
            stroke="var(--divider)"
          />
          <text
            x={LEFT - 4}
            y={scale.y(iv) + 3.5}
            textAnchor="end"
            fontSize="10"
            fill="currentColor"
          >
            {Number.isInteger(iv) ? iv : iv.toFixed(1)}
          </text>
        </g>
      ))}
      {ticks.map((each, index) => (
        <text
          key={each.strike}
          x={scale.x(each.strike)}
          y={H - 8}
          textAnchor={index === 0 ? 'start' : index === 2 ? 'end' : 'middle'}
          fontSize="10"
          fill="currentColor"
        >
          {strikeWords(each.strike)}
        </text>
      ))}
      {atm !== null && points.some((each) => each.strike === atm) && (
        <g data-smile-atm="true">
          <line
            x1={scale.x(atm)}
            x2={scale.x(atm)}
            y1={TOP}
            y2={H - BOTTOM}
            stroke="var(--border-strong)"
            strokeDasharray="2 3"
          />
          <text
            x={scale.x(atm)}
            y={TOP - 3}
            textAnchor="middle"
            fontSize="10"
            fontWeight="700"
            fill="var(--text-primary)"
          >
            ATM
          </text>
        </g>
      )}
      <polyline
        data-line="call"
        points={path('call')}
        fill="none"
        stroke="var(--brand)"
        strokeWidth="2"
      />
      <polyline
        data-line="put"
        points={path('put')}
        fill="none"
        stroke="var(--text-secondary)"
        strokeWidth="2"
        strokeDasharray="5 4"
      />
      {points.map((each) => (
        <g key={each.strike}>
          {each.call !== null && (
            <circle cx={scale.x(each.strike)} cy={scale.y(each.call)} r="2.5" fill="var(--brand)" />
          )}
          {each.put !== null && (
            <rect
              x={scale.x(each.strike) - 2.5}
              y={scale.y(each.put) - 2.5}
              width="5"
              height="5"
              fill="var(--surface-card)"
              stroke="var(--text-secondary)"
              strokeWidth="1.5"
            />
          )}
        </g>
      ))}
    </svg>
  )
}

/** The legend's marks, drawn as the lines are. */
export function SmileKey({ side }: { side: 'call' | 'put' }) {
  return (
    <svg viewBox="0 0 28 10" aria-hidden="true" className="h-2.5 w-7 shrink-0">
      {side === 'call' ? (
        <>
          <line x1="0" x2="28" y1="5" y2="5" stroke="var(--brand)" strokeWidth="2" />
          <circle cx="14" cy="5" r="3" fill="var(--brand)" />
        </>
      ) : (
        <>
          <line
            x1="0"
            x2="28"
            y1="5"
            y2="5"
            stroke="var(--text-secondary)"
            strokeWidth="2"
            strokeDasharray="5 4"
          />
          <rect
            x="11"
            y="2"
            width="6"
            height="6"
            fill="var(--surface-card)"
            stroke="var(--text-secondary)"
            strokeWidth="1.5"
          />
        </>
      )}
    </svg>
  )
}
