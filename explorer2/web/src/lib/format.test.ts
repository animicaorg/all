import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { formatBalance, timeAgo, timeAgoNoSeconds } from './format'

describe('timeAgo', () => {
  const NOW = new Date('2026-04-17T12:00:00Z')

  beforeEach(() => {
    vi.useFakeTimers()
    vi.setSystemTime(NOW)
  })

  afterEach(() => {
    vi.useRealTimers()
  })

  it('renders second-level precision for recent events', () => {
    const nowSec = Math.floor(NOW.getTime() / 1000)
    expect(timeAgo(nowSec - 12)).toBe('12s ago')
  })

  it('renders minute + second precision under one hour', () => {
    const nowSec = Math.floor(NOW.getTime() / 1000)
    expect(timeAgo(nowSec - 95)).toBe('1m 35s ago')
    expect(timeAgo(nowSec - 120)).toBe('2m ago')
  })

  it('renders hour + minute precision under one day', () => {
    const nowSec = Math.floor(NOW.getTime() / 1000)
    expect(timeAgo(nowSec - (3 * 3600 + 7 * 60))).toBe('3h 7m ago')
  })
})

describe('timeAgoNoSeconds', () => {
  const NOW = new Date('2026-04-17T12:00:00Z')

  beforeEach(() => {
    vi.useFakeTimers()
    vi.setSystemTime(NOW)
  })

  afterEach(() => {
    vi.useRealTimers()
  })

  it('uses just now for sub-minute events', () => {
    const nowSec = Math.floor(NOW.getTime() / 1000)
    expect(timeAgoNoSeconds(nowSec - 12)).toBe('just now')
  })

  it('rounds to minute precision under one hour', () => {
    const nowSec = Math.floor(NOW.getTime() / 1000)
    expect(timeAgoNoSeconds(nowSec - 95)).toBe('1m ago')
    expect(timeAgoNoSeconds(nowSec - 120)).toBe('2m ago')
  })
})

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

  it('should handle extremely large balance without precision loss', () => {
    // 1 trillion ANM = 10^21 nANM (larger than Number.MAX_SAFE_INTEGER)
    const result = formatBalance('1000000000000000000000') 
    expect(result.anm).toBe('1,000,000,000,000')
    expect(result.nanm).toBe('1,000,000,000,000,000,000,000')
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
