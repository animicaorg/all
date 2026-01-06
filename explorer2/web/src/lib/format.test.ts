import { describe, it, expect } from 'vitest'
import { formatBalance } from './format'

describe('formatBalance', () => {
  it('should convert 5 nANM to ANM', () => {
    const result = formatBalance('0x5')
    expect(result.anm).toBe('0.000000005')
    expect(result.nanm).toBe('5')
    expect(result.hex).toBe('0x5')
  })

  it('should convert 1000 nANM to ANM with thousand separators', () => {
    const result = formatBalance('0x3e8') // 1000 nANM
    expect(result.anm).toBe('0.000001')
    expect(result.nanm).toBe('1,000')
    expect(result.hex).toBe('0x3e8')
  })

  it('should convert 1 billion nANM to 1 ANM', () => {
    const result = formatBalance('1000000000') // 1 ANM = 10^9 nANM
    expect(result.anm).toBe('1')
    expect(result.nanm).toBe('1,000,000,000')
    expect(result.hex).toBe('0x3b9aca00')
  })

  it('should convert 5 billion nANM to 5 ANM', () => {
    const result = formatBalance('5000000000') // 5 ANM
    expect(result.anm).toBe('5')
    expect(result.nanm).toBe('5,000,000,000')
    expect(result.hex).toBe('0x12a05f200')
  })

  it('should handle fractional ANM correctly', () => {
    const result = formatBalance('1500000000') // 1.5 ANM
    expect(result.anm).toBe('1.5')
    expect(result.nanm).toBe('1,500,000,000')
  })

  it('should handle very large balance (1 million ANM)', () => {
    const result = formatBalance('1000000000000000') // 1M ANM = 10^15 nANM
    expect(result.anm).toBe('1,000,000')
    expect(result.nanm).toBe('1,000,000,000,000,000')
  })

  it('should handle zero balance', () => {
    const result = formatBalance('0x0')
    expect(result.anm).toBe('0')
    expect(result.nanm).toBe('0')
    expect(result.hex).toBe('0x0')
  })

  it('should handle null balance', () => {
    const result = formatBalance(null)
    expect(result.anm).toBe('—')
    expect(result.nanm).toBe('—')
    expect(result.hex).toBe('—')
  })

  it('should handle undefined balance', () => {
    const result = formatBalance(undefined)
    expect(result.anm).toBe('—')
    expect(result.nanm).toBe('—')
    expect(result.hex).toBe('—')
  })

  it('should handle empty string', () => {
    const result = formatBalance('')
    expect(result.anm).toBe('—')
    expect(result.nanm).toBe('—')
    expect(result.hex).toBe('—')
  })

  it('should handle invalid input gracefully', () => {
    const result = formatBalance('invalid')
    expect(result.anm).toBe('invalid')
    expect(result.nanm).toBe('invalid')
    expect(result.hex).toBe('invalid')
  })

  it('should remove trailing zeros from decimal part', () => {
    const result = formatBalance('1234567000') // 1.234567000 ANM should display as 1.234567
    expect(result.anm).toBe('1.234567')
  })

  it('should handle precise fractional values', () => {
    const result = formatBalance('123456789') // 0.123456789 ANM
    expect(result.anm).toBe('0.123456789')
  })
})
