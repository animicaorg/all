import { describe, it, expect } from 'vitest'
import { formatBalance } from './format'

describe('formatBalance', () => {
  it('should convert hex balance to decimal', () => {
    const result = formatBalance('0x5')
    expect(result.decimal).toBe('5')
    expect(result.hex).toBe('0x5')
  })

  it('should format large hex balance with thousand separators', () => {
    const result = formatBalance('0x3e8') // 1000 in decimal
    expect(result.decimal).toBe('1,000')
    expect(result.hex).toBe('0x3e8')
  })

  it('should format very large balance', () => {
    const result = formatBalance('0xde0b6b3a7640000') // 1 ETH (10^18 wei)
    expect(result.decimal).toBe('1,000,000,000,000,000,000')
    expect(result.hex).toBe('0xde0b6b3a7640000')
  })

  it('should handle zero balance', () => {
    const result = formatBalance('0x0')
    expect(result.decimal).toBe('0')
    expect(result.hex).toBe('0x0')
  })

  it('should handle null balance', () => {
    const result = formatBalance(null)
    expect(result.decimal).toBe('—')
    expect(result.hex).toBe('—')
  })

  it('should handle undefined balance', () => {
    const result = formatBalance(undefined)
    expect(result.decimal).toBe('—')
    expect(result.hex).toBe('—')
  })

  it('should handle empty string', () => {
    const result = formatBalance('')
    expect(result.decimal).toBe('—')
    expect(result.hex).toBe('—')
  })

  it('should handle plain decimal string', () => {
    const result = formatBalance('1000')
    expect(result.decimal).toBe('1,000')
    expect(result.hex).toBe('0x3e8')
  })

  it('should handle invalid input gracefully', () => {
    const result = formatBalance('invalid')
    expect(result.decimal).toBe('invalid')
    expect(result.hex).toBe('invalid')
  })
})
