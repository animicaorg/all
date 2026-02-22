import { describe, expect, it } from 'vitest'
import { normalizeTxHash } from '../src/txHash'

describe('normalizeTxHash', () => {
  it('normalizes uppercase and adds 0x', () => {
    const out = normalizeTxHash('A'.repeat(64))
    expect(out).toBe('0x' + 'a'.repeat(64))
  })

  it('throws for invalid length', () => {
    expect(() => normalizeTxHash('0xabc')).toThrow('Invalid transaction hash')
  })
})
