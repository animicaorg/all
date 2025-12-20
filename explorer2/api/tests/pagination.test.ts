import { describe, expect, it } from 'vitest'
import { clampLimit, nextCursorForHeight, parseCursor } from '../src/pagination'

describe('pagination helpers', () => {
  it('clamps limits', () => {
    expect(clampLimit(0)).toBe(20)
    expect(clampLimit(100)).toBe(50)
  })

  it('parses cursor', () => {
    expect(parseCursor('12')).toBe(12)
    expect(parseCursor('bad')).toBeUndefined()
  })

  it('calculates next cursor', () => {
    expect(nextCursorForHeight(10)).toBe('9')
  })
})
