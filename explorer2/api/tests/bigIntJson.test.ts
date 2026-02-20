import { describe, expect, it } from 'vitest'
import {
  bigIntReplacer,
  bigIntSafeStringify,
  safeJsonParse,
  parseHexQuantity,
  parseDecimalQuantity,
  formatBigInt
} from '../src/bigIntJson'

describe('bigIntReplacer', () => {
  it('converts BigInt to string', () => {
    expect(bigIntReplacer('', 123n)).toBe('123')
    expect(bigIntReplacer('', 0n)).toBe('0')
    expect(bigIntReplacer('', 9999999999999999999n)).toBe('9999999999999999999')
  })

  it('passes through non-BigInt values unchanged', () => {
    expect(bigIntReplacer('', 42)).toBe(42)
    expect(bigIntReplacer('', 'hello')).toBe('hello')
    expect(bigIntReplacer('', null)).toBe(null)
    expect(bigIntReplacer('', true)).toBe(true)
  })
})

describe('bigIntSafeStringify', () => {
  it('serialises plain objects', () => {
    expect(bigIntSafeStringify({ a: 1 })).toBe('{"a":1}')
  })

  it('serialises BigInt values as decimal strings', () => {
    const result = bigIntSafeStringify({ value: 1000000000000000000000n })
    expect(result).toBe('{"value":"1000000000000000000000"}')
  })

  it('serialises nested BigInt values', () => {
    const result = bigIntSafeStringify({ outer: { inner: 42n } })
    expect(result).toBe('{"outer":{"inner":"42"}}')
  })

  it('handles circular references without throwing', () => {
    const obj: Record<string, unknown> = { a: 1 }
    obj['self'] = obj
    expect(() => bigIntSafeStringify(obj)).not.toThrow()
    const result = bigIntSafeStringify(obj)
    expect(result).toContain('"[Circular]"')
  })

  it('handles arrays with BigInt', () => {
    const result = bigIntSafeStringify([1n, 2n, 3n])
    expect(result).toBe('["1","2","3"]')
  })

  it('supports space parameter for pretty printing', () => {
    const result = bigIntSafeStringify({ a: 1 }, 2)
    expect(result).toContain('\n')
  })
})

describe('safeJsonParse', () => {
  it('parses valid JSON', () => {
    expect(safeJsonParse('{"a":1}')).toEqual({ a: 1 })
  })

  it('returns null for invalid JSON', () => {
    expect(safeJsonParse('not json')).toBe(null)
    expect(safeJsonParse('')).toBe(null)
  })
})

describe('parseHexQuantity', () => {
  it('parses 0x0 to 0n', () => {
    expect(parseHexQuantity('0x0')).toBe(0n)
  })

  it('parses hex values correctly', () => {
    expect(parseHexQuantity('0x1')).toBe(1n)
    expect(parseHexQuantity('0xff')).toBe(255n)
    expect(parseHexQuantity('0x3b9aca00')).toBe(1000000000n)
  })

  it('parses large hex values as BigInt', () => {
    const big = parseHexQuantity('0xde0b6b3a7640000')
    expect(big).toBe(1000000000000000000n)
  })

  it('handles value without 0x prefix', () => {
    expect(parseHexQuantity('ff')).toBe(255n)
  })

  it('returns 0n for null/undefined/empty', () => {
    expect(parseHexQuantity(null)).toBe(0n)
    expect(parseHexQuantity(undefined)).toBe(0n)
    expect(parseHexQuantity('')).toBe(0n)
    expect(parseHexQuantity('0x')).toBe(0n)
  })

  it('returns 0n for invalid hex', () => {
    expect(parseHexQuantity('0xgg')).toBe(0n)
  })
})

describe('parseDecimalQuantity', () => {
  it('parses decimal strings', () => {
    expect(parseDecimalQuantity('1000')).toBe(1000n)
    expect(parseDecimalQuantity('0')).toBe(0n)
  })

  it('parses numbers', () => {
    expect(parseDecimalQuantity(42)).toBe(42n)
  })

  it('returns 0n for null/undefined/empty', () => {
    expect(parseDecimalQuantity(null)).toBe(0n)
    expect(parseDecimalQuantity(undefined)).toBe(0n)
    expect(parseDecimalQuantity('')).toBe(0n)
  })
})

describe('formatBigInt', () => {
  it('formats with thousand separators', () => {
    expect(formatBigInt(1000000n)).toMatch(/1[,.]000[,.]000/)
  })

  it('formats zero', () => {
    expect(formatBigInt(0n)).toBe('0')
  })
})
