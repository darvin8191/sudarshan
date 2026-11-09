import { describe, expect, it } from 'vitest'
import { SPEC, widgetCard } from '@/test/screener'
import { dayOnlySpec } from './AddWidgetSheet'
import { movedKeys } from './dashboardWrites'

describe('movedKeys', () => {
  const cards = [1, 2, 3].map((id) => widgetCard({ screen_id: id }))
  it('sends the whole order with one card moved one place', () => {
    expect(movedKeys(cards, cards[0], 1)).toEqual([
      [2, 'table'],
      [1, 'table'],
      [3, 'table'],
    ])
    expect(movedKeys(cards, cards[2], -1)).toEqual([
      [1, 'table'],
      [3, 'table'],
      [2, 'table'],
    ])
  })
  it('moves nothing past either end', () => {
    expect(movedKeys(cards, cards[0], -1)).toBeNull()
    expect(movedKeys(cards, cards[2], 1)).toBeNull()
  })
})

describe('dayOnlySpec', () => {
  it('is true only when every line read is on the day candle', () => {
    const day = {
      ...SPEC,
      where: {
        all: [
          {
            id: 'c1',
            on: true,
            left: { field: 'open', tf: 'D' as const },
            op: 'gt',
            right: { num: '1' },
          },
        ],
      },
    }
    expect(dayOnlySpec(day)).toBe(true)
    expect(dayOnlySpec(SPEC)).toBe(false)
    expect(dayOnlySpec({ ...SPEC, where: { all: [] } })).toBe(false)
  })
})
