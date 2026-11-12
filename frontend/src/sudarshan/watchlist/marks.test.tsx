import { render } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { MARK_IDS, MARKS, MarkGlyph, markOf } from './marks'

describe('marks', () => {
  it('gives each of the five marks its own shape, name and colour token', () => {
    expect(MARK_IDS).toEqual([1, 2, 3, 4, 5])
    expect(MARK_IDS.map((id) => MARKS[id].shape)).toEqual([
      'circle',
      'square',
      'triangle',
      'diamond',
      'star',
    ])
    expect(new Set(MARK_IDS.map((id) => MARKS[id].name)).size).toBe(5)
    for (const id of MARK_IDS) expect(MARKS[id].color).toBe(`var(--mark-${id})`)
  })

  it('draws a glyph for each mark, hidden from a screen reader (the name is beside it)', () => {
    for (const id of MARK_IDS) {
      const { container, unmount } = render(<MarkGlyph mark={id} />)
      const svg = container.querySelector('svg')
      expect(svg?.getAttribute('data-shape')).toBe(MARKS[id].shape)
      expect(svg?.getAttribute('aria-hidden')).toBe('true')
      unmount()
    }
  })

  it('draws nothing for none or a number that is not a mark', () => {
    for (const bad of [0, 6, -1]) {
      expect(markOf(bad)).toBeNull()
      const { container, unmount } = render(<MarkGlyph mark={bad} />)
      expect(container.querySelector('svg')).toBeNull()
      unmount()
    }
  })
})
