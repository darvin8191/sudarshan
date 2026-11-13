import { describe, expect, it } from 'vitest'
import { fromSurface } from './parts'

describe('where a history line was done', () => {
  it('says the server for a reset run there', () => {
    expect(fromSurface('server')).toBe('from the server')
  })
})
